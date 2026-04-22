# HubOS

HubOS 是一个面向多用户、多渠道接入的 AI 控制平台。当前仓库已经是**合并后的单仓结构**：前端 WebUI、FastAPI 应用、CLI、运行时逻辑、测试和文档都在同一个项目里维护。

## 快速部署到另一台 Mac

### 部署前准备

部署机器建议先确认这几项：

- macOS 已登录当前用户桌面会话
- 能访问 GitHub 私有仓库
- 已安装或可安装 Homebrew
- 网络可以下载 Python / npm 依赖
- 计划开放端口 `8088`

### 私有仓库首次部署

先登录 GitHub：

```bash
gh auth login
```

然后执行：

```bash
git clone https://github.com/allenzh0115/HubOS.git
cd HubOS
bash scripts/deploy_mac_private.sh
```

### 已经拉过仓库的机器

```bash
cd HubOS
bash scripts/deploy_mac.sh
```

### 部署完成后访问

- 本机：`http://localhost:8088`
- 局域网：`http://你的Mac局域网IP:8088`

### 后续升级

```bash
cd HubOS
bash scripts/update_mac.sh
```

### 停止服务

```bash
cd HubOS
bash scripts/stop_mac.sh
```

### 查看服务状态

```bash
cd HubOS
bash scripts/status_mac.sh
```

## 当前仓库结构

```text
HubOS/
├── console/        WebUI（React + Vite + TypeScript）
├── src/hubos/      FastAPI app、CLI、runtime、memory、channels
├── tests/          pytest 测试
├── scripts/        直接运行的验证脚本 / e2e 脚本
├── docs/           设计文档与阶段报告
└── desktop/        桌面端相关代码
```

概念上我们仍然区分两条主线：

- **WebUI / General Manager 层**：多渠道接入、会话管理、聊天 UI、管理员视图。
- **Runtime / Execution 层**：任务调度、多 agent 协作、worker 集成、权限与审计。

只是它们现在已经不再拆成 `HubOS-WebUI/` 和 `HubOS-Runtime/` 两个独立目录，而是统一收敛在这个仓库里。

## 主要目录说明

### `console/`

- React 18 + Vite + TypeScript + Ant Design 5
- 当前 UI 适配与页面验收工作主要都在这里进行
- 本地开发默认跑在 `http://127.0.0.1:5173`

### `src/hubos/`

- `hubos.app._app:app`：FastAPI 应用入口
- `hubos.cli.*`：CLI 子命令
- `hubos.app.routers.*`：HTTP API 路由
- `hubos.core.*`：memory / infra / execution 等核心逻辑

### `tests/`

- 标准 pytest 测试目录
- 现在默认测试入口只收集这里，不再把 `scripts/test_*.py` 当成 pytest 测试模块

### `scripts/`

- 面向开发验证的直接运行脚本
- 例如 `scripts/test_admin_sessions_api.py`、`scripts/test_runtime_delegate.py`
- 这些脚本用于局部验证，不等于 pytest 正式测试入口

## 环境要求

- Python：`>=3.10,<3.14`
- Node.js：建议使用当前 LTS
- 包管理：
  - Python：`uv`
  - Frontend：`npm`

> 仓库根目录的 `.python-version` 固定为 `3.10`。如果直接用系统 `python3`，请先确认不是 3.14+。

## 本地启动

### 1. 安装 Python 依赖

```bash
cd /Users/allen/HubOS
uv sync --extra dev
```

### 2. 启动 HubOS FastAPI

```bash
cd /Users/allen/HubOS
uv run hubos app --reload --host 127.0.0.1 --port 8088
```

- 默认 OpenAPI：<http://127.0.0.1:8088/docs>

### 3. 启动 WebUI

如果你要让前端直接连本地 HubOS API，可以显式覆盖 `VITE_API_BASE_URL`：

```bash
cd /Users/allen/HubOS/console
npm install
VITE_API_BASE_URL=http://127.0.0.1:8088 npm run dev
```

> 当前 `console/.env` / `vite.config.ts` 里保留了兼容性评估用的 dev 配置；如果你在做本地 HubOS 联调，推荐像上面这样显式覆盖 `VITE_API_BASE_URL`。

## 测试与验证

### pytest

```bash
cd /Users/allen/HubOS
uv run pytest
```

### Session 隔离合约测试

```bash
cd /Users/allen/HubOS
uv run python scripts/verify_session_isolation.py
```

### Admin Sessions API 验证

```bash
cd /Users/allen/HubOS
uv run python scripts/test_admin_sessions_api.py
```

这个脚本现在会使用临时 `HUBOS_MEMORY_ROOT` 和临时 `HUBOS_WORKING_DIR`，不会再扫描开发机真实 `~/.hubos/workspaces`。

### Runtime delegation 验证

```bash
cd /Users/allen/HubOS
uv run python scripts/test_runtime_delegate.py
```

## 当前状态

- UI 适配工作已完成一轮阶段性收口
- 多个页面已按 CoPaw 结构完成对接或诚实降级
- Backend capability、sessions 方案、skills 权限模型等还在继续收敛

更细的页面级验收和兼容矩阵，见：

- [docs/STAGE1_UI_ACCEPTANCE_REPORT.md](./docs/STAGE1_UI_ACCEPTANCE_REPORT.md)
- [docs/COPAW_API_COMPATIBILITY_MATRIX.md](./docs/COPAW_API_COMPATIBILITY_MATRIX.md)
- [docs/HUBOS_SESSIONS_PRODUCT_AND_API_PLAN.md](./docs/HUBOS_SESSIONS_PRODUCT_AND_API_PLAN.md)
