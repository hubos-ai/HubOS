// ── URLs ──────────────────────────────────────────────────────────────────

export const PYPI_URL = "https://pypi.org/pypi/hubos/json";

export const GITHUB_URL = "https://github.com/agentscope-ai/HubOS" as const;

// ── Timing ────────────────────────────────────────────────────────────────

export const ONE_HOUR_MS = 60 * 60 * 1000;

// ── Navigation ────────────────────────────────────────────────────────────

export const DEFAULT_OPEN_KEYS = [
  "chat-group",
  "control-group",
  "agent-group",
  "settings-group",
  "admin-group",
];

export const KEY_TO_PATH: Record<string, string> = {
  chat: "/chat",
  channels: "/channels",
  sessions: "/sessions",
  "cron-jobs": "/cron-jobs",
  heartbeat: "/heartbeat",
  skills: "/skills",
  "skill-pool": "/skill-pool",
  tools: "/tools",
  mcp: "/mcp",
  workspace: "/workspace",
  "work-experience": "/work-experience",
  agents: "/agents",
  models: "/models",
  environments: "/environments",
  "agent-config": "/agent-config",
  security: "/security",
  "token-usage": "/token-usage",
  "voice-transcription": "/voice-transcription",
  "admin-sessions": "/admin/sessions",
  "task-modes": "/task-modes",
};

export const KEY_TO_LABEL: Record<string, string> = {
  chat: "nav.chat",
  channels: "nav.channels",
  sessions: "nav.sessions",
  "cron-jobs": "nav.cronJobs",
  heartbeat: "nav.heartbeat",
  skills: "nav.skills",
  "skill-pool": "nav.skillPool",
  tools: "nav.tools",
  mcp: "nav.mcp",
  "work-experience": "nav.workExperience",
  "agent-config": "nav.agentConfig",
  workspace: "nav.workspace",
  models: "nav.models",
  environments: "nav.environments",
  security: "nav.security",
  "token-usage": "nav.tokenUsage",
  agents: "nav.agents",
  "admin-sessions": "nav.adminSessions",
  "task-modes": "nav.taskModes",
};

// ── URL helpers ───────────────────────────────────────────────────────────

export const getWebsiteLang = (lang: string): string =>
  lang.startsWith("zh") ? "zh" : "en";

// Local static files (served from console/public/ → dist/)
export const getChangelogUrl = (baseUrl: string): string =>
  `${baseUrl}CHANGELOG.md`;

export const getFaqMdUrl = (lang: string, baseUrl: string): string =>
  `${baseUrl}faq.${lang.startsWith("zh") ? "zh" : "en"}.md`;

// External links
export const getDocsUrl = (_lang: string): string => `${GITHUB_URL}#readme`;

export const getReleaseNotesUrl = (_lang: string): string =>
  `${GITHUB_URL}/releases`;

// ── Version helpers ────────────────────────────────────────────────────────

// Filter out pre-release versions; post-releases are treated as stable.
// PEP 440 pre-release suffixes: aN / bN / rcN (or cN) / devN.
export const isStableVersion = (v: string): boolean =>
  !/(\d)(a|alpha|b|beta|rc|c|dev)\d*/i.test(v);

// Compare two PEP 440 version strings. Returns >0 if a>b, <0 if a<b, 0 if equal.
// .postN releases sort after their base version (e.g. 1.0.0.post1 > 1.0.0).
// Pre-release versions (aN, bN, rcN) sort before their base version.
export const compareVersions = (a: string, b: string): number => {
  const normalise = (v: string): number[] => {
    // Handle .postN suffix
    const postMatch = v.match(/\.post(\d+)$/i);
    const postNum = postMatch ? Number(postMatch[1]) : 0;
    const baseVersion = v.replace(/\.post\d+$/i, "");

    // Handle pre-release suffix (e.g., 1.0.1b1 -> base=1.0.1, preType=b, preNum=1)
    const preMatch = baseVersion.match(/^(.+?)(a|alpha|b|beta|rc|c)(\d*)$/i);
    let coreVersion = baseVersion;
    let preType = 0; // 0 = stable, -3 = alpha, -2 = beta, -1 = rc
    let preNum = 0;
    if (preMatch) {
      coreVersion = preMatch[1];
      const preLabel = preMatch[2].toLowerCase();
      preType =
        preLabel === "a" || preLabel === "alpha"
          ? -3
          : preLabel === "b" || preLabel === "beta"
          ? -2
          : -1; // rc or c
      preNum = preMatch[3] ? Number(preMatch[3]) : 0;
    }

    const parts = coreVersion.split(/[.\-]/).map((seg) => Number(seg) || 0);
    // Append: preType (0 for stable, negative for pre-release), preNum, postNum
    return [...parts, preType, preNum, postNum];
  };

  const aN = normalise(a);
  const bN = normalise(b);
  const len = Math.max(aN.length, bN.length);
  for (let i = 0; i < len; i++) {
    const diff = (aN[i] ?? 0) - (bN[i] ?? 0);
    if (diff !== 0) return diff;
  }
  return 0;
};

// ── Update markdown ───────────────────────────────────────────────────────

export const UPDATE_MD: Record<string, string> = {
  zh: `### HubOS如何更新

要更新 HubOS 到最新版本，可根据你的安装方式选择对应方法：

1. 如果你使用的是一键安装脚本，直接重新运行安装命令即可自动升级。

2. 如果你是通过 pip 安装，在终端中执行以下命令升级：

\`\`\`
pip install --upgrade hubos
\`\`\`

3. 如果你是从源码安装，进入项目目录并拉取最新代码后重新安装：

\`\`\`
cd HubOS
git pull origin main
pip install -e .
\`\`\`

4. 如果你使用的是 Docker，拉取最新镜像并重启容器：

\`\`\`
docker pull agentscope/hubos:latest
docker run -p 127.0.0.1:8088:8088 -v hubos-data:/app/working agentscope/hubos:latest
\`\`\`

升级后重启服务 hubos app。`,

  ru: `### Как обновить HubOS

Чтобы обновить HubOS, выберите способ в зависимости от типа установки:

1. Если вы устанавливали через однострочный скрипт, повторно запустите установщик для обновления.

2. Если устанавливали через pip, выполните:

\`\`\`
pip install --upgrade hubos
\`\`\`

3. Если устанавливали из исходников, получите последние изменения и переустановите:

\`\`\`
cd HubOS
git pull origin main
pip install -e .
\`\`\`

4. Если используете Docker, загрузите новый образ и перезапустите контейнер:

\`\`\`
docker pull agentscope/hubos:latest
docker run -p 127.0.0.1:8088:8088 -v hubos-data:/app/working agentscope/hubos:latest
\`\`\`

После обновления перезапустите сервис с помощью \`hubos app\`.`,

  en: `### How to update HubOS

To update HubOS, use the method matching your installation type:

1. If installed via one-line script, re-run the installer to upgrade.

2. If installed via pip, run:

\`\`\`
pip install --upgrade hubos
\`\`\`

3. If installed from source, pull the latest code and reinstall:

\`\`\`
cd HubOS
git pull origin main
pip install -e .
\`\`\`

4. If using Docker, pull the latest image and restart the container:

\`\`\`
docker pull agentscope/hubos:latest
docker run -p 127.0.0.1:8088:8088 -v hubos-data:/app/working agentscope/hubos:latest
\`\`\`

After upgrading, restart the service with \`hubos app\`.`,
};
