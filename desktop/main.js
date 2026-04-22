// HubOS Desktop shell
//
// Responsibilities:
//   1. Spawn the local HubOS backend (`hubos app`) when this Electron app
//      launches, unless something is already listening on the backend port.
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
const { spawn } = require("child_process");
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

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

const BACKEND_HOST = "127.0.0.1";
const BACKEND_PORT = 8088;
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}/`;
const HEALTH_TIMEOUT_MS = 90_000;
const HEALTH_POLL_INTERVAL_MS = 500;

const HUBOS_HOME = path.join(os.homedir(), ".hubos");
const DEFAULT_HUBOS_BIN = path.join(HUBOS_HOME, "venv", "bin", "hubos");
const LOG_DIR = path.join(HUBOS_HOME, "logs");
const DESKTOP_LOG = path.join(LOG_DIR, "desktop.log");
const BACKEND_LOG = path.join(LOG_DIR, "desktop-backend.log");

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
    // Don't inherit NODE_* / ELECTRON_* noise that could confuse Python.
    PYTHONUNBUFFERED: "1",
  };
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
  if (!backendProc || backendProc.killed) return;
  if (!spawnedByUs) {
    log("stopBackend: backend was not spawned by us — leaving it alone");
    return;
  }
  log("stopBackend: sending SIGTERM pid=", backendProc.pid);
  try {
    backendProc.kill("SIGTERM");
  } catch (e) {
    log("stopBackend: kill error", e.message);
  }
  // Escalate if it doesn't die within 4s.
  const proc = backendProc;
  setTimeout(() => {
    if (proc && !proc.killed) {
      log("stopBackend: escalating to SIGKILL");
      try {
        proc.kill("SIGKILL");
      } catch (_) {
        /* ignore */
      }
    }
  }, 4000);
}

async function restartBackend() {
  log("restartBackend: begin");
  showOverlay("正在重启服务…");

  stopBackend();
  // Wait a beat for the port to free up.
  await new Promise((r) => setTimeout(r, 1500));

  startBackend();
  const ok = await waitForBackendHealthy();
  if (!ok) {
    dialog.showErrorBox(
      "HubOS backend did not come back up",
      `Checked ${BACKEND_URL} for ${HEALTH_TIMEOUT_MS / 1000}s without a response.\n\n` +
        `See logs: ${BACKEND_LOG}`,
    );
    hideOverlay();
    return;
  }
  // Reload the UI once the backend answers.
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.loadURL(BACKEND_URL);
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
  mainWindow.loadURL(BACKEND_URL);
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
        label: "重启服务",
        accelerator: "CmdOrCtrl+Shift+R",
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
        click: () => shell.openPath(BACKEND_LOG),
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
    startBackend();
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
        `See logs: ${BACKEND_LOG}`,
    );
  } else {
    // Re-load in case the initial loadURL fired before the server answered.
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.loadURL(BACKEND_URL);
    }
  }
}

app.whenReady().then(bootstrap);

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
