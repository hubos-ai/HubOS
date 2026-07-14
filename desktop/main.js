// HubOS Desktop shell
//
// Responsibilities:
//   1. Use the macOS LaunchAgent-managed HubOS backend when installed, or spawn
//      the local backend (`hubos app`) as a dev fallback.
//   2. Poll the backend until it's healthy, THEN open the main window pointed
//      at http://127.0.0.1:<port>/.
//   3. Provide a "Refresh Page" + "Restart Service" menu.
//   4. Tear the backend child down on quit (including SIGINT / crash paths).
//
// Non-goals:
//   * We do NOT bundle Python here. The user already has a working venv at
//     ~/.hubos/venv/bin/hubos. Packaging that into a single `.app` is its own
//     rabbit hole (PyInstaller + code signing for each provider's .so files).

const { app, BrowserWindow, Menu, shell, dialog, nativeImage } = require("electron");
const { spawn, execSync, execFileSync } = require("child_process");
const http = require("http");
const fs = require("fs");
const os = require("os");
const path = require("path");

// ---------------------------------------------------------------------------
// Branding — set BEFORE `app.whenReady()` so the app menu / About panel /
// `process.title` all pick it up. In dev mode (i.e. running inside the
// stock `Electron.app` bundle) this is the only way the menubar shows
// "HubOS" instead of "Electron" — bundle-level Info.plist wins once
// packaged, but electron-builder will regenerate that for us.
// ---------------------------------------------------------------------------

const APP_NAME = "HubOS";
app.setName(APP_NAME);
try {
  // process.title affects Activity Monitor + `ps` output in dev runs.
  process.title = APP_NAME;
} catch (_) {
  /* ignore */
}

// Only one desktop shell should manage the backend lifecycle at a time.
const gotSingleInstanceLock = app.requestSingleInstanceLock();
if (!gotSingleInstanceLock) {
  app.quit();
}

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const BACKEND_HOST = "127.0.0.1";
const BACKEND_PORT = 8088;
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}/`;
const HEALTH_TIMEOUT_MS = 90_000;
const HEALTH_POLL_INTERVAL_MS = 500;
const HEALTH_SHUTDOWN_TIMEOUT_MS = 20_000;

const HUBOS_HOME = path.join(os.homedir(), ".hubos");
const DEFAULT_HUBOS_BIN = path.join(HUBOS_HOME, "venv", "bin", "hubos");
const LOG_DIR = path.join(HUBOS_HOME, "logs");
const DESKTOP_LOG = path.join(LOG_DIR, "desktop.log");
const BACKEND_LOG = path.join(LOG_DIR, "desktop-backend.log");
const LAUNCH_AGENT_LABEL = process.env.HUBOS_LAUNCH_AGENT_LABEL || "io.hubos.server";
const LAUNCH_AGENT_PLIST = path.join(
  os.homedir(),
  "Library",
  "LaunchAgents",
  `${LAUNCH_AGENT_LABEL}.plist`,
);
const LAUNCH_AGENT_LOG_DIR = path.join(os.homedir(), "Library", "Logs", "HubOS");
const LAUNCH_AGENT_ERR_LOG = path.join(LAUNCH_AGENT_LOG_DIR, "hubos.err.log");
const LOG_MAX_BYTES = Number(process.env.HUBOS_LOG_MAX_BYTES ?? 50 * 1024 * 1024);
const LOG_BACKUP_COUNT = Number(process.env.HUBOS_LOG_BACKUP_COUNT ?? 3);

const ASSETS_DIR = path.join(__dirname, "assets");

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let mainWindow = null;
let backendProc = null;
/**
 * Only track processes we OURSELVES spawned, so "Restart Service" won't kill a
 * `hubos app` the user started in a separate terminal.
 */
let spawnedByUs = false;
let shuttingDown = false;

if (gotSingleInstanceLock) {
  app.on("second-instance", () => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
      return;
    }
    createWindow();
  });
}

function loadBackendUrlIgnoringCache() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  mainWindow.loadURL(BACKEND_URL, {
    extraHeaders: "Cache-Control: no-cache\nPragma: no-cache",
  });
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

function ensureDir(p) {
  try {
    fs.mkdirSync(p, { recursive: true });
  } catch (_) {
    /* ignore */
  }
}

function rotateLogIfNeeded(file) {
  try {
    if (!fs.existsSync(file)) return;
    const { size } = fs.statSync(file);
    if (size <= LOG_MAX_BYTES) return;

    for (let i = LOG_BACKUP_COUNT; i > 1; i -= 1) {
      const previous = `${file}.${i - 1}`;
      const next = `${file}.${i}`;
      if (fs.existsSync(previous)) fs.renameSync(previous, next);
    }
    fs.renameSync(file, `${file}.1`);
  } catch (e) {
    log("rotateLogIfNeeded failed:", e.message);
  }
}

function log(...parts) {
  const line = `[${new Date().toISOString()}] ${parts.join(" ")}\n`;
  process.stdout.write(line);
  try {
    ensureDir(LOG_DIR);
    fs.appendFileSync(DESKTOP_LOG, line);
  } catch (_) {
    /* ignore */
  }
}

// ---------------------------------------------------------------------------
// Backend orchestration
// ---------------------------------------------------------------------------

function pingBackend(timeoutMs = 1500) {
  return new Promise((resolve) => {
    const req = http.get(BACKEND_URL, { timeout: timeoutMs }, (res) => {
      // Any HTTP response means *something* is listening — treat as "up".
      res.resume();
      resolve(true);
    });
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.on("error", () => resolve(false));
  });
}

async function waitForBackendHealthy(timeoutMs = HEALTH_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await pingBackend()) return true;
    await new Promise((r) => setTimeout(r, HEALTH_POLL_INTERVAL_MS));
  }
  return false;
}

async function waitForBackendDown(timeoutMs = HEALTH_SHUTDOWN_TIMEOUT_MS) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (!(await pingBackend())) return true;
    await new Promise((r) => setTimeout(r, HEALTH_POLL_INTERVAL_MS));
  }
  return false;
}

function resolveHubosBin() {
  // Allow override via env var so power users can point at a different venv.
  const override = process.env.HUBOS_BIN;
  if (override && fs.existsSync(override)) return override;
  if (fs.existsSync(DEFAULT_HUBOS_BIN)) return DEFAULT_HUBOS_BIN;
  return null;
}

function buildBackendEnv() {
  return {
    ...process.env,
    // Desktop launches should accumulate work-experience cards unless the
    // operator explicitly overrides the flag in the parent environment.
    ENABLE_WORK_EXPERIENCE_LAYER:
      process.env.ENABLE_WORK_EXPERIENCE_LAYER ?? "true",
    // Desktop chat should also use accumulated experience as compact guidance
    // before each response. Operators can still disable this explicitly.
    ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION:
      process.env.ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION ?? "true",
    // Don't inherit NODE_* / ELECTRON_* noise that could confuse Python.
    PYTHONUNBUFFERED: "1",
  };
}

function launchAgentTarget() {
  if (process.platform !== "darwin" || typeof process.getuid !== "function") {
    return null;
  }
  return `gui/${process.getuid()}/${LAUNCH_AGENT_LABEL}`;
}

function launchAgentDomain() {
  if (process.platform !== "darwin" || typeof process.getuid !== "function") {
    return null;
  }
  return `gui/${process.getuid()}`;
}

function isLaunchAgentInstalled() {
  return process.platform === "darwin" && fs.existsSync(LAUNCH_AGENT_PLIST);
}

function isLaunchAgentAvailable() {
  if (!isLaunchAgentInstalled()) return false;
  const target = launchAgentTarget();
  if (!target) return false;
  try {
    execFileSync("launchctl", ["print", target], {
      stdio: "ignore",
      timeout: 5000,
    });
    return true;
  } catch (_) {
    return false;
  }
}

async function restartLaunchAgent() {
  const target = launchAgentTarget();
  const domain = launchAgentDomain();
  if (!target || !domain || !isLaunchAgentInstalled()) return false;

  try {
    try {
      log("restartLaunchAgent: bootout", LAUNCH_AGENT_PLIST);
      execFileSync("launchctl", ["bootout", domain, LAUNCH_AGENT_PLIST], {
        stdio: "pipe",
        timeout: 15000,
      });
    } catch (e) {
      const stderr = e.stderr ? String(e.stderr).trim() : "";
      if (stderr) {
        log("restartLaunchAgent: bootout note:", stderr);
      }
    }

    await waitForBackendDown();

    log("restartLaunchAgent: bootstrap", LAUNCH_AGENT_PLIST);
    execFileSync("launchctl", ["bootstrap", domain, LAUNCH_AGENT_PLIST], {
      stdio: "pipe",
      timeout: 15000,
    });

    log("restartLaunchAgent: kickstart", target);
    execFileSync("launchctl", ["kickstart", "-k", target], {
      stdio: "pipe",
      timeout: 15000,
    });
    backendProc = null;
    spawnedByUs = false;
    return true;
  } catch (e) {
    const stderr = e.stderr ? String(e.stderr).trim() : "";
    log("restartLaunchAgent: failed:", e.message, stderr);
    return false;
  }
}

function serviceLogPath() {
  if (isLaunchAgentAvailable() && fs.existsSync(LAUNCH_AGENT_ERR_LOG)) {
    return LAUNCH_AGENT_ERR_LOG;
  }
  return BACKEND_LOG;
}

function startBackend() {
  if (backendProc && !backendProc.killed) {
    log("startBackend: already running, pid=", backendProc.pid);
    return;
  }

  const bin = resolveHubosBin();
  if (!bin) {
    dialog.showErrorBox(
      "HubOS backend not found",
      `Could not find 'hubos' at ${DEFAULT_HUBOS_BIN}.\n\n` +
        "Install HubOS into ~/.hubos/venv (or set HUBOS_BIN to your binary) and re-open the app.",
    );
    app.quit();
    return;
  }

  ensureDir(LOG_DIR);
  rotateLogIfNeeded(BACKEND_LOG);
  const out = fs.openSync(BACKEND_LOG, "a");
  const err = fs.openSync(BACKEND_LOG, "a");

  log("startBackend: spawning", bin, "app");
  backendProc = spawn(bin, ["app"], {
    stdio: ["ignore", out, err],
    detached: false,
    env: buildBackendEnv(),
  });
  spawnedByUs = true;

  backendProc.on("exit", (code, signal) => {
    log(`backend exited code=${code} signal=${signal}`);
    backendProc = null;
    if (!shuttingDown && mainWindow) {
      // Surface a soft warning — don't kill the whole app; user can restart.
      mainWindow.webContents
        .executeJavaScript(
          `console.warn('[HubOS Desktop] backend exited (code=${code}, signal=${signal})')`,
        )
        .catch(() => {});
    }
  });

  backendProc.on("error", (e) => {
    log("backend spawn error:", e.message);
    dialog.showErrorBox("Failed to start HubOS backend", String(e.message || e));
  });
}

function stopBackend() {
  // 1. Kill the process we spawned (if any).
  if (backendProc && !backendProc.killed) {
    if (spawnedByUs) {
      log("stopBackend: sending SIGTERM to spawned pid=", backendProc.pid);
      try {
        backendProc.kill("SIGTERM");
      } catch (e) {
        log("stopBackend: kill error", e.message);
      }
      const proc = backendProc;
      setTimeout(() => {
        if (proc && !proc.killed) {
          log("stopBackend: escalating to SIGKILL");
          try { proc.kill("SIGKILL"); } catch (_) {}
        }
      }, 4000);
    } else {
      log("stopBackend: backend was not spawned by us — will find by port");
    }
    backendProc = null;
    spawnedByUs = false;
  }

  if (isLaunchAgentAvailable()) {
    log("stopBackend: LaunchAgent detected; leaving managed backend running");
    return;
  }

  // 2. Also kill any hubos process on our port (regardless of who started it).
  const BACKEND_PORT = parseInt(process.env.HUBOS_PORT || "8088", 10);
  try {
    const result = execSync(`lsof -ti :${BACKEND_PORT} -sTCP:LISTEN 2>/dev/null`, {
      encoding: "utf-8",
      timeout: 5000,
    }).trim();
    if (result) {
      const pids = result.split("\n").filter(Boolean);
      for (const pid of pids) {
        log("stopBackend: killing port-based pid=", pid);
        try { process.kill(parseInt(pid, 10), "SIGTERM"); } catch (_) {}
      }
      // Escalate after 4s.
      setTimeout(() => {
        for (const pid of pids) {
          try { process.kill(parseInt(pid, 10), 9); } catch (_) {}
        }
      }, 4000);
    }
  } catch (_) {
    // lsof returns non-zero when nothing is listening — that's fine.
  }
}

async function restartBackend() {
  log("restartBackend: begin");
  showOverlay("正在重启服务…");

  if (isLaunchAgentInstalled()) {
    if (!(await restartLaunchAgent())) {
      dialog.showErrorBox(
        "HubOS backend restart failed",
        `Could not restart LaunchAgent ${LAUNCH_AGENT_LABEL}.\n\n` +
          `See logs: ${serviceLogPath()}`,
      );
      hideOverlay();
      return;
    }

    const ok = await waitForBackendHealthy();
    if (!ok) {
      dialog.showErrorBox(
        "HubOS backend did not come back up",
        `Checked ${BACKEND_URL} for ${HEALTH_TIMEOUT_MS / 1000}s without a response.\n\n` +
          `See logs: ${serviceLogPath()}`,
      );
      hideOverlay();
      return;
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      loadBackendUrlIgnoringCache();
    }
    hideOverlay();
    log("restartBackend: done via LaunchAgent");
    return;
  }

  stopBackend();
  // Wait a beat for the port to free up.
  await new Promise((r) => setTimeout(r, 1500));

  startBackend();
  const ok = await waitForBackendHealthy();
  if (!ok) {
    dialog.showErrorBox(
      "HubOS backend did not come back up",
      `Checked ${BACKEND_URL} for ${HEALTH_TIMEOUT_MS / 1000}s without a response.\n\n` +
        `See logs: ${serviceLogPath()}`,
    );
    hideOverlay();
    return;
  }
  // Reload the UI once the backend answers.
  if (mainWindow && !mainWindow.isDestroyed()) {
    loadBackendUrlIgnoringCache();
  }
  log("restartBackend: done");
}

// ---------------------------------------------------------------------------
// Window + overlay
// ---------------------------------------------------------------------------

function iconCandidate() {
  for (const name of ["icon.png", "icon.icns", "logo.png"]) {
    const p = path.join(ASSETS_DIR, name);
    if (fs.existsSync(p)) return p;
  }
  return undefined;
}

function createWindow() {
  const iconPath = iconCandidate();
  const winOpts = {
    width: 1440,
    height: 900,
    minWidth: 900,
    minHeight: 600,
    backgroundColor: "#0f1115",
    autoHideMenuBar: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  };
  if (iconPath) winOpts.icon = nativeImage.createFromPath(iconPath);

  mainWindow = new BrowserWindow(winOpts);
  loadBackendUrlIgnoringCache();
  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function showOverlay(message) {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const js = `(() => {
    let el = document.getElementById('__hubos_desktop_overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = '__hubos_desktop_overlay';
      Object.assign(el.style, {
        position: 'fixed', inset: '0', zIndex: 2147483646,
        background: 'rgba(0,0,0,0.55)', color: '#fff',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontFamily: '-apple-system,BlinkMacSystemFont,"Helvetica Neue",sans-serif',
        fontSize: '15px', letterSpacing: '.02em',
        backdropFilter: 'blur(2px)'
      });
      document.body.appendChild(el);
    }
    el.textContent = ${JSON.stringify(message)};
  })();`;
  mainWindow.webContents.executeJavaScript(js).catch(() => {});
}

function hideOverlay() {
  if (!mainWindow || mainWindow.isDestroyed()) return;
  const js = `document.getElementById('__hubos_desktop_overlay')?.remove();`;
  mainWindow.webContents.executeJavaScript(js).catch(() => {});
}

// ---------------------------------------------------------------------------
// Menu
// ---------------------------------------------------------------------------

function buildMenu() {
  const isMac = process.platform === "darwin";

  const serviceMenu = {
    label: "服务",
    submenu: [
      {
        label: "刷新页面",
        accelerator: "CmdOrCtrl+R",
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.reload();
          }
        },
      },
      {
        label: "强制刷新（清缓存）",
        accelerator: "CmdOrCtrl+Shift+R",
        click: () => {
          if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.webContents.reloadIgnoringCache();
          }
        },
      },
      {
        label: "重启服务",
        accelerator: "CmdOrCtrl+Shift+Alt+R",
        click: () => {
          restartBackend().catch((e) => {
            log("restartBackend error:", e.stack || e.message);
            dialog.showErrorBox("Restart failed", String(e.message || e));
          });
        },
      },
      { type: "separator" },
      {
        label: "在浏览器打开",
        click: () => shell.openExternal(BACKEND_URL),
      },
      {
        label: "打开服务日志",
        click: () => shell.openPath(serviceLogPath()),
      },
      {
        label: "打开桌面端日志",
        click: () => shell.openPath(DESKTOP_LOG),
      },
    ],
  };

  const viewMenu = {
    label: "视图",
    submenu: [
      { role: "toggleDevTools", label: "开发者工具" },
      { role: "togglefullscreen", label: "全屏" },
      { type: "separator" },
      { role: "resetZoom", label: "实际大小" },
      { role: "zoomIn", label: "放大" },
      { role: "zoomOut", label: "缩小" },
    ],
  };

  const editMenu = {
    label: "编辑",
    submenu: [
      { role: "undo", label: "撤销" },
      { role: "redo", label: "重做" },
      { type: "separator" },
      { role: "cut", label: "剪切" },
      { role: "copy", label: "复制" },
      { role: "paste", label: "粘贴" },
      { role: "selectAll", label: "全选" },
    ],
  };

  const windowMenu = {
    label: "窗口",
    role: "windowMenu",
  };

  const template = [];
  if (isMac) {
    template.push({
      label: app.name,
      submenu: [
        { role: "about", label: `关于 ${app.name}` },
        { type: "separator" },
        { role: "hide", label: "隐藏" },
        { role: "hideOthers", label: "隐藏其他" },
        { role: "unhide", label: "全部显示" },
        { type: "separator" },
        { role: "quit", label: "退出" },
      ],
    });
  }
  template.push(serviceMenu, editMenu, viewMenu, windowMenu);

  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

function applyBranding() {
  // Dock icon on macOS. In dev mode the `.app` bundle icon is Electron's, so
  // the dock stays on Electron's default until we override at runtime. Once
  // packaged, `build.mac.icon` in package.json takes over and this is a no-op.
  const iconPath = iconCandidate();
  if (process.platform === "darwin" && iconPath && app.dock) {
    try {
      const img = nativeImage.createFromPath(iconPath);
      if (!img.isEmpty()) {
        app.dock.setIcon(img);
      }
    } catch (e) {
      log("applyBranding: dock icon failed", e.message);
    }
  }

  // "About HubOS" panel — uses the same icon and our package version.
  let version = "";
  try {
    version = require("./package.json").version || "";
  } catch (_) {
    /* ignore */
  }
  app.setAboutPanelOptions({
    applicationName: APP_NAME,
    applicationVersion: version,
    version,
    copyright: "© HubOS",
    iconPath: iconPath || undefined,
  });
}

async function bootstrap() {
  ensureDir(LOG_DIR);
  log("bootstrap: starting; electron=", process.versions.electron);

  applyBranding();

  // If someone already has `hubos app` running (e.g. started from a terminal),
  // reuse it — don't fight them for the port.
  const already = await pingBackend();
  if (!already) {
    if (isLaunchAgentInstalled()) {
      log("bootstrap: backend down; starting LaunchAgent-managed service");
      const restarted = await restartLaunchAgent();
      if (!restarted) {
        log("bootstrap: LaunchAgent restart failed");
      }
    } else {
      startBackend();
    }
  } else {
    log("bootstrap: backend already up (not spawned by us)");
  }

  buildMenu();
  createWindow();

  const ok = await waitForBackendHealthy();
  if (!ok) {
    dialog.showErrorBox(
      "HubOS backend did not start",
      `Checked ${BACKEND_URL} for ${HEALTH_TIMEOUT_MS / 1000}s without a response.\n\n` +
        `See logs: ${serviceLogPath()}`,
    );
  } else {
    // Re-load in case the initial loadURL fired before the server answered.
    if (mainWindow && !mainWindow.isDestroyed()) {
      try {
        await mainWindow.webContents.session.clearCache();
      } catch (e) {
        log("clearCache failed:", e.message);
      }
      loadBackendUrlIgnoringCache();
    }
  }
}

if (gotSingleInstanceLock) {
  app.whenReady().then(bootstrap);
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("activate", () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

app.on("before-quit", () => {
  shuttingDown = true;
  stopBackend();
});

// Safety net: make sure we don't orphan the Python process on crash / SIGINT.
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    shuttingDown = true;
    stopBackend();
    process.exit(0);
  });
}
