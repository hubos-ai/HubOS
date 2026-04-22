# HubOS Console API Compatibility Matrix

> **目的**: 盘点 HubOS console 前端全部 API 依赖，对照 XClaw backend (8001) 能力，给出分类结论和后续优先级建议。
>
> **基准日期**: 2026-04-19
>
> **Backend 参照**: `http://localhost:8001` (XClaw live backend) — 仅用于评估"HubOS 页面当前能否接入"，不代表最终迁移完成状态。

---

## 1. 总结结论

| 分类 | 数量 | 说明 |
|------|------|------|
| **DIRECT_MATCH** | ~11 | 路径和 Shape 完全匹配，可直接使用 |
| **PATH_ADAPTER_ENOUGH** | ~8 | 路径不同但 shape 兼容，adapter 可解 |
| **SHAPE_ADAPTER_ENOUGH** | ~5 | 路径匹配但 shape 需转换，adapter 可解 |
| **NEEDS_BACKEND_CAPABILITY** | ~20+ | 后端缺失路由或关键字段，无法仅靠 adapter 解决 |
| **UNKNOWN_NEEDS_VERIFICATION** | ~3 | 需要实测 shape 或有疑问 |

**总计约 47+ 个前端 API 端点**（按路由路径计，不同 HTTP 方法分开列）

---

## 2. 详细 API 分类表

### 分类说明

| 代码 | 含义 |
|------|------|
| `DIRECT_MATCH` | 路径完全匹配，shape 兼容，无需任何改动 |
| `PATH_ADAPTER_ENOUGH` | 路径有差异（prefix 缺失/多余），shape 兼容，加一层 path mapping adapter 即可 |
| `SHAPE_ADAPTER_ENOUGH` | 路径匹配，shape 不兼容，需要 response/request transform |
| `NEEDS_BACKEND_CAPABILITY` | 后端缺失该路由，或 shape 差异太大，必须先补 backend 能力 |
| `UNKNOWN_NEEDS_VERIFICATION` | 路径存在但 shape 未知，需要实际调用验证 |

---

### 2.1 Auth 模块

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/auth/login` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无 `/auth/login`。已通过 dev bypass 绕過，仅用于本地开发 |
| `/auth/register` | POST | — | `NEEDS_BACKEND_CAPABILITY` | 同上 |
| `/auth/status` | GET | — | `NEEDS_BACKEND_CAPABILITY` | 同上 |
| `/auth/update-profile` | POST | — | `NEEDS_BACKEND_CAPABILITY` | 同上 |

---

### 2.2 Agent 单体配置模块 (`/agent/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/agent/` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/health` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/process` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/admin/status` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/admin/shutdown` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/running-config` | GET/PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/language` | GET/PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/audio-mode` | GET/PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/transcription-providers` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/transcription-provider` | PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/transcription-provider-type` | GET/PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/local-whisper-status` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |

**结论**: 整个 `/agent/*` 前端 API 层在 XClaw 无对应路由，是 HubOS backend 私有接口群。**全部需要 backend capability**。

---

### 2.3 Agents 配置模块 (`/agents/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/agents` | GET | `GET /api/agents` | `DIRECT_MATCH` | 路径+shape 均匹配 |
| `/agents` | POST | `POST /api/agents` | `DIRECT_MATCH` | 同上 |
| `/agents/{name}` | GET/PUT/DELETE | `GET/PUT/DELETE /api/agents/{name}` | `DIRECT_MATCH` | 完全匹配 |
| `/agents/order` | PUT | `PUT /api/agents/order` | `DIRECT_MATCH` | 完全匹配 |
| `/agents/{name}/files` | GET | `GET /api/agents/{name}/files` | `DIRECT_MATCH` | 完全匹配 |
| `/agents/{name}/files/{filename}` | GET/PUT | `GET/PUT /api/agents/{name}/files/{filename}` | `DIRECT_MATCH` | 完全匹配 |
| `/agents/{name}/heartbeat` | GET/PUT | `GET/PUT /api/agents/{name}/heartbeat` | `DIRECT_MATCH` | 完全匹配 |
| `/agents/{name}/jobs` | GET/POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无对应路由 |
| `/agents/{name}/memory-logs` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无对应路由 |
| `/agents/check` | GET | `GET /api/agents/check` | `DIRECT_MATCH` | 完全匹配 |
| `/agents/dashboard/summary` | GET | `GET /api/agents/dashboard/summary` | `DIRECT_MATCH` | 完全匹配 |
| `/agents/{name}/toggle` (PATCH) | PATCH | — | `NEEDS_BACKEND_CAPABILITY` | XClaw agents 无 toggle 端点 |

**结论**: Agents 的 CRUD + 文件管理路由全部 `DIRECT_MATCH`，是接入最干净的模块。

---

### 2.4 Channels 模块 (`/config/channels/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/config/channels/types` | GET | `GET /api/channels/types` | `PATH_ADAPTER_ENOUGH` | 路径多了 `/config` prefix，需 adapter 移除 |
| `/config/channels` | GET/PUT | `GET /api/channels/` + `PUT /api/channels/{name}/config` | `PATH_ADAPTER_ENOUGH` | 路径差异，shape 兼容 |
| `/config/channels/{name}` | GET/PUT | `GET /api/channels/runtime/{name}` + `PUT /api/channels/{name}/config` | `PATH_ADAPTER_ENOUGH` | 路径拆分映射 |
| `/config/channels/weixin/qrcode` | GET | `GET /api/channels/weixin/qrcode` | `DIRECT_MATCH` | 完全匹配 |
| `/config/channels/weixin/qrcode/status` | GET | `GET /api/channels/weixin/qrcode/status` | `DIRECT_MATCH` | 完全匹配 |

**结论**: Channels 全部是 `PATH_ADAPTER_ENOUGH`，无 backend 能力缺失，adapter 即可解决。

---

### 2.5 Chat / Sessions 模块 (`/chats/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/chats` | GET/POST | `GET/POST /api/chats` | `DIRECT_MATCH` | 完全匹配 |
| `/chats/{chat_id}` | GET/PUT/DELETE | `GET/PUT/DELETE /api/chats/{chat_id}` | `DIRECT_MATCH` | 完全匹配 |
| `/chats/batch-delete` | POST | `POST /api/chats/batch-delete` | `DIRECT_MATCH` | 完全匹配 |
| `/console/chat/stop` | POST | `POST /api/console/chat/stop` | `DIRECT_MATCH` | 完全匹配 |
| `/console/upload` | POST | `POST /api/console/upload` | `DIRECT_MATCH` | 完全匹配 |
| `/files/preview/{filepath}` | GET | `HEAD/GET /api/files/preview/{filepath}` | `DIRECT_MATCH` | 完全匹配 |

**结论**: Chat 模块全部 `DIRECT_MATCH`，是接入最干净的模块之一。

---

### 2.6 Console 模块 (`/console/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/console/push-messages` | GET | `GET /api/console/push-messages` | `DIRECT_MATCH` | 完全匹配 |
| `/console/chat` | POST | `POST /api/console/chat` | `DIRECT_MATCH` | 完全匹配 |
| `/console/chat/stop` | POST | `POST /api/console/chat/stop` | `DIRECT_MATCH` | 完全匹配 |
| `/console/upload` | POST | `POST /api/console/upload` | `DIRECT_MATCH` | 完全匹配 |

---

### 2.7 CronJobs 模块 (`/cron/jobs/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/cron/jobs` | GET/POST | `GET/POST /api/jobs` | `PATH_ADAPTER_ENOUGH` | 路径差异（`/cron` prefix），shape 兼容 |
| `/cron/jobs/{job_id}` | GET/PUT/DELETE | `GET/PUT/DELETE /api/jobs/{job_id}` | `PATH_ADAPTER_ENOUGH` | 同上 |
| `/cron/jobs/{job_id}/pause` | POST | `POST /api/jobs/{job_id}/pause` | `PATH_ADAPTER_ENOUGH` | 同上 |
| `/cron/jobs/{job_id}/resume` | POST | `POST /api/jobs/{job_id}/resume` | `PATH_ADAPTER_ENOUGH` | 同上 |
| `/cron/jobs/{job_id}/run` | POST | `POST /api/jobs/{job_id}/run` | `PATH_ADAPTER_ENOUGH` | 同上 |
| `/cron/jobs/{job_id}/state` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无 `/api/jobs/{job_id}/state` 路由 |

**额外 shape 差异**: HubOS 用 `schedule: { type: "cron", cron: "..." }`，XClaw 用顶层 `cron: "..."` 字段。已在 `cronjobAdapter` 中处理。

**结论**: 主体是 `PATH_ADAPTER_ENOUGH`，`state` 查询端缺失。

---

### 2.8 Envs 模块 (`/envs/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/envs` | GET/PUT | `GET/PUT /api/envs` | `DIRECT_MATCH` | 完全匹配 |
| `/envs/{key}` | DELETE | `DELETE /api/envs/{key}` | `DIRECT_MATCH` | 完全匹配 |

**结论**: Envs 全部 `DIRECT_MATCH`。

---

### 2.9 Heartbeat 模块 (`/config/heartbeat`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/config/heartbeat` | GET/PUT | `GET/PUT /api/heartbeat` | `PATH_ADAPTER_ENOUGH` | 路径多了 `/config` prefix |

**结论**: 纯 path mapping，无 shape 差异。

---

### 2.10 Language 模块 (`/settings/language`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/settings/language` | GET/PUT | `GET/PUT /api/user-profile` | `SHAPE_ADAPTER_ENOUGH` | 路径不同，shape 需验证 |

**说明**: XClaw 只有 `/api/user-profile`，HubOS 有独立的 `/settings/language`。shape 未知，需验证。

---

### 2.11 Local Models 模块 (`/local-models/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/local-models/server` | GET/POST/DELETE | `GET/POST/DELETE /api/local-models/server` | `DIRECT_MATCH` | 完全匹配 |
| `/local-models/models` | GET | `GET /api/local-models/models` | `DIRECT_MATCH` | 完全匹配 |
| `/local-models/models/recommended` | GET | `GET /api/local-models/models/recommended` | `DIRECT_MATCH` | 完全匹配 |
| `/local-models/server/download` | GET/DELETE | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/local-models/models/download` | GET/POST/DELETE | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |

**结论**: 核心 server/models 状态接口匹配，download 管理接口缺失。

---

### 2.12 MCP 模块 (`/mcp/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/mcp` | GET/POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 只有 `/api/mcp/config`（GET/PUT），无列表/创建/删除 |
| `/mcp/{clientKey}` | GET/PUT/DELETE | — | `NEEDS_BACKEND_CAPABILITY` | 同上 |
| `/mcp/{clientKey}/toggle` | PATCH | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |

**结论**: MCP 前端期望完整的 CRUD，但 XClaw 只有 GET/PUT config。

---

### 2.13 Models / Providers 模块 (`/models/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/models` | GET | `GET /api/models` | `SHAPE_ADAPTER_ENOUGH` | 路径匹配，但 shape 差异大（见下） |
| `/models/active` | GET/PUT | `GET/PUT /api/providers/models/active` | `PATH_ADAPTER_ENOUGH` | 路径不同，shape 需验证 |
| `/models/{providerId}/config` | PUT | `PUT /api/providers/{provider_id}/config` | `PATH_ADAPTER_ENOUGH` | 同上 |
| `/models/custom-providers` | POST/DELETE | `POST /api/providers/custom` | `SHAPE_ADAPTER_ENOUGH` | 路径差异，shape 差异大 |
| `/models/{providerId}/test` | POST | `POST /api/providers/{provider_id}/test` | `DIRECT_MATCH` | 完全匹配 |
| `/models/{providerId}/models/{modelId}/probe-multimodal` | POST | `GET /api/providers/{provider_id}/models/{model_id}/probe-multimodal` | `SHAPE_ADAPTER_ENOUGH` | 方法不匹配（POST vs GET）|
| `/models/{providerId}/discover` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无 discover 路由 |
| `/providers` | GET | `GET /api/providers` | `SHAPE_ADAPTER_ENOUGH` | shape 字段差异大 |

**Shape 差异详情**:
- HubOS `ProviderInfo`: `api_key_prefix`, `is_custom`, `is_local`, `extra_models[]`, `freeze_url`, `require_api_key`, `generate_kwargs`...
- XClaw `ProviderInfo`: `id`, `name`, `base_url`, `api_key`, `chat_model`, `models[]`...
- 字段重叠少，需要 adapter 做字段映射

**结论**: Models/Providers 模块 shape 差异显著，adapter 复杂但可解；`discover` 功能后端缺失。

---

### 2.14 Root 模块

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/` | GET | — | `UNKNOWN_NEEDS_VERIFICATION` | XClaw 根路径返回 `{"detail": "Not Found"}`，可能不影响 |
| `/version` | GET | `GET /api/version` | `PATH_ADAPTER_ENOUGH` | 路径多了 `/api` prefix |

---

### 2.15 Security 模块 (`/config/security/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/config/security/tool-guard` | GET/PUT | `GET/PUT /api/security/config` | `SHAPE_ADAPTER_ENOUGH` | 路径不同，shape 需合并转换 |
| `/config/security/tool-guard/builtin-rules` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw security config 无 builtin-rules |
| `/config/security/file-guard` | GET/PUT | 合并到 `/api/security/config` | `SHAPE_ADAPTER_ENOUGH` | 路径不同，shape 合并 |
| `/config/security/skill-scanner` | GET/PUT | 合并到 `/api/security/config` | `SHAPE_ADAPTER_ENOUGH` | 同上 |
| `/config/security/skill-scanner/blocked-history` | GET/DELETE | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/config/security/skill-scanner/whitelist` | POST/DELETE | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |

**Shape 差异**: HubOS 分开查 tool-guard/file-guard/skill-scanner，XClaw 统一在 `/api/security/config` 返回 `{tool_guard_enabled, file_guard_enabled, skill_scanner_auto, whitelist}`。Adapter 需要拆分/合并。

**结论**: Security 主体 shape 可 adapter，但 builtin-rules、blocked-history、whitelist 是 HubOS 私有能力。

---

### 2.16 Skills 模块 (`/skills/*`)

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/skills` | GET | `GET /api/skills` | `SHAPE_ADAPTER_ENOUGH` | 返回 `{skills: [...]}` vs HubOS 期望 `SkillSpec[]`，缺 content/source/config |
| `/skills/pool` | GET | `GET /api/skill-pool` | `SHAPE_ADAPTER_ENOUGH` | 同上 |
| `/skills/{skill_name}` | GET/PUT/DELETE | `GET/PUT /api/skills/{skill_name}`（无 DELETE） | `SHAPE_ADAPTER_ENOUGH` | 路径匹配，shape 缺 content |
| `/skills/ai/optimize/stream` | POST | `POST /api/skills/ai/optimize/stream` | `DIRECT_MATCH` | 完全匹配（streaming） |
| `/skills/batch-enable` | POST | `POST /api/skills/batch-enable` | `DIRECT_MATCH` | 完全匹配 |
| `/skills/batch-disable` | POST | `POST /api/skills/batch-disable` | `DIRECT_MATCH` | 完全匹配 |
| `/skills/batch-delete` | POST | `POST /api/skills/batch-delete` | `DIRECT_MATCH` | 完全匹配 |
| `/skills/install` | POST | `POST /api/skills/install` | `DIRECT_MATCH` | 完全匹配 |
| `/skills/hub/search` | GET | `POST /api/skill-pool/search` | `PATH_ADAPTER_ENOUGH` | GET→POST，参数映射 |
| `/skills/hub/install/start` | POST | `POST /api/skill-pool/install` | `PATH_ADAPTER_ENOUGH` | 路径/方法映射 |
| `/skills/pool/import` | POST | `POST /api/skill-pool/install` | `PATH_ADAPTER_ENOUGH` | 同上 |
| `/skills/pool/builtin-sources` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/import-builtin` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/{name}/enable` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/{name}/disable` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/refresh` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/refresh` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/workspaces` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/{skillName}/update-builtin` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/save` | PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/save` | PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/upload` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/upload-zip` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/download` | POST | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/{name}/config` | GET/PUT/DELETE | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/{name}/config` | GET/PUT/DELETE | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/skills/pool/{name}` | DELETE | `DELETE /api/skill-pool/{skill_name}` | `PATH_ADAPTER_ENOUGH` | 路径匹配 |

**关键 shape 差异**:
- XClaw `/api/skills` 返回 `{"skills": [{name, description, license, category, enabled}]}` — 无 `content`、`source`、`version_text`、`channels`、`config` 等 HubOS `SkillSpec` 必填字段
- HubOS 的 skill 是完整文本技能（包含 `content`），XClaw 的 skill 是轻量元数据引用

**结论**: Skills 模块是适配工作量最大的模块之一。大量路由 XClaw 根本没有；现存路由的 shape 也缺少核心字段。

---

### 2.17 Token Usage 模块

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/token-usage` | GET | `GET /api/token-usage` | `DIRECT_MATCH` | 路径+shape 均匹配 |
| `/token-usage/cost` | GET | `GET /api/token-usage/cost` | `DIRECT_MATCH` | 完全匹配 |
| `/token-usage/record` | POST | `POST /api/token-usage/record` | `DIRECT_MATCH` | 完全匹配 |

**结论**: Token Usage 全部 `DIRECT_MATCH`。

---

### 2.18 Tools 模块

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/tools` | GET | `GET /api/tools` | `SHAPE_ADAPTER_ENOUGH` | shape 需验证 |
| `/tools/{tool_name}/toggle` | PATCH | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/tools/{tool_name}/async-execution` | PATCH | `PATCH /api/tools/{tool_name}/async-execution` | `DIRECT_MATCH` | 完全匹配 |

**结论**: Tools toggle 功能后端缺失。

---

### 2.19 User Timezone 模块

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/config/user-timezone` | GET/PUT | `GET/PUT /api/user-profile` | `SHAPE_ADAPTER_ENOUGH` | 路径不同，shape 需验证 |

---

### 2.20 Workspace / Agent Files 模块

| 前端 API 路径 | 方法 | 对应后端 | 状态 | 说明 |
|---|---|---|---|---|
| `/workspace/download` | GET | `GET /api/workspace/download` | `DIRECT_MATCH` | 完全匹配 |
| `/workspace/upload` | POST | `POST /api/workspace/upload` | `DIRECT_MATCH` | 完全匹配 |
| `/agent/files` | GET | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/files/{filename}` | GET/PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |
| `/agent/memory` | GET/PUT | `GET/PUT /api/memory` | `SHAPE_ADAPTER_ENOUGH` | 路径+shape 需验证 |
| `/agent/system-prompt-files` | GET/PUT | — | `NEEDS_BACKEND_CAPABILITY` | XClaw 无此路由 |

---

## 3. 页面 → API 依赖映射

| 页面 | 路由 | 主要依赖 API 模块 | 阻塞程度 | 最难适配点 |
|------|------|-----------------|---------|-----------|
| Login | `/login` | auth | ✅ bypass 已解决 | dev bypass 替代了真实 auth |
| Chat | `/chat/*` | chat, sessionApi, console | 🟡 低阻塞 | 基本 DIRECT_MATCH |
| Control/Channels | `/channels` | channel | 🟡 低阻塞 | PATH_ADAPTER_ENOUGH |
| Control/Sessions | `/sessions` | sessionApi | 🔴 高阻塞 | XClaw 无 `/api/sessions` 路由 |
| Control/CronJobs | `/cron-jobs` | cronjob | 🟢 已接通 | state 端点缺失 |
| Control/Heartbeat | `/heartbeat` | heartbeat | 🟢 已接通 | PATH_ADAPTER_ENOUGH |
| Agent/Config | `/agent/config` | agent | 🔴 高阻塞 | 全部 `/agent/*` 路由缺失 |
| Agent/Skills | `/skills` | skill | 🔴 高阻塞 | 大部分路由缺失，shape 缺 content |
| Agent/SkillPool | `/skill-pool` | skill | 🔴 高阻塞 | pool CRUD 大部分缺失 |
| Agent/Tools | `/tools` | tools | 🟡 中阻塞 | toggle 端点缺失 |
| Agent/MCP | `/mcp` | mcp | 🔴 高阻塞 | MCP CRUD 路由缺失 |
| Agent/Workspace | `/workspace` | workspace | 🔴 高阻塞 | agent/files 路由缺失 |
| Settings/Agents | `/agents` | agents | 🟢 已接通 | 仅 `/agents/{name}/jobs` 缺失 |
| Settings/Models | `/models` | provider | 🟡 中阻塞 | shape 差异大但可 adapter |
| Settings/Security | `/security` | security | 🟡 中阻塞 | builtin-rules 等私有能力缺失 |
| Settings/Environments | `/environments` | env | 🟢 已接通 | DIRECT_MATCH |
| Settings/TokenUsage | `/token-usage` | tokenUsage | 🟢 已接通 | DIRECT_MATCH |
| Settings/VoiceTranscription | `/voice-transcription` | — | 🔴 高阻塞 | 路由缺失 |

---

## 4. 优先级建议

### 🟢 立刻可以继续接（已验证 DIRECT_MATCH 或 PATH_ADAPTER_ENOUGH）

1. **Settings/Agents** (`/agents`) — Agents CRUD + files + heartbeat 全通，仅 `/{name}/jobs` 缺失
2. **Control/Channels** (`/channels`) — PATH_ADAPTER_ENOUGH，channel types + config 路由全部存在
3. **Settings/Environments** (`/environments`) — 100% DIRECT_MATCH
4. **Settings/TokenUsage** (`/token-usage`) — 100% DIRECT_MATCH
5. **Control/Heartbeat** (`/heartbeat`) — PATH_ADAPTER_ENOUGH

### 🟡 Adapter 层可解决（中度工作量）

6. **Settings/Models** (`/models`) — path+shape 差异显著但可映射；最复杂的是 `ProviderInfo` 字段差异
7. **Control/Sessions** (`/sessions`) — Chat 模块本身 DIRECT_MATCH，但 HubOS Sessions 是独立 UI，需要验证 session list/query 端点
8. **Settings/Security** (`/security`) — tool-guard/file-guard/skill-scanner 合并到单一 endpoint，adapter 拆分即可
9. **Chat** (`/chat/*`) — 本身 DIRECT_MATCH，但 skill optimize stream 需要验证
10. **Agent/Tools** (`/tools`) — toggle 缺失，其余 adapter 可解

### 🔴 需先补 Backend Capability（高阻力）

11. **Agent/Skills** (`/skills`) — 34 个端点中 ~20 个 XClaw 缺失，大量 shape 缺 content
12. **Agent/SkillPool** (`/skill-pool`) — pool CRUD 基本缺失
13. **Agent/MCP** (`/mcp`) — 只有 config GET/PUT，无列表/创建/删除
14. **Agent/Workspace** (`/workspace`) — agent/files 路由群全部缺失
15. **Agent/Config** (`/agent/config`) — 全部 `/agent/*` 路由缺失（16 个端点）
16. **Settings/VoiceTranscription** (`/voice-transcription`) — transcription API 独立于 HubOS 架构

---

## 5. 共性问题分析

### Auth 层（已 bypass）
- HubOS 的 auth 是独立 `/auth/*` 路由，XClaw 完全无对应实现
- Dev bypass 只解决"能否进入受保护页面"问题
- 真实 auth 集成需要：login → real token → protected API 调用

### Agent 单体配置层（完全缺失）
- 整个 `/agent/*` API 群（16 端点）在 XClaw 无对应
- 这是 HubOS backend 对"本机 agent 进程管理"的接口，XClaw 是分布式架构，可能不需要

### Skills 内容的根本差异
- HubOS skill = 完整文本内容 (`content` 字段) + 元数据
- XClaw skill = 轻量元数据引用（`name`, `description`, `enabled`），无 content
- 这是架构性差异，不是简单 adapter 能解决的

### MCP 能力差距
- HubOS MCP = 完整 MCP client lifecycle management
- XClaw = 只有 `mcp/config` GET/PUT

---

## 6. 建议的下一页顺序

| 顺序 | 页面 | 理由 |
|------|------|------|
| **1** | Settings/Agents | 最干净：CRUD 100% DIRECT_MATCH，无阻塞点，仅个别次要路由缺失 |
| **2** | Control/Channels | PATH_ADAPTER_ENOUGH 工作量小，channel 路由全部存在 |
| **3** | Settings/Models | shape 差异较大但可映射，可以作为 shape adapter 练习 |
| **4** | Control/Sessions | Chat DIRECT_MATCH；Sessions 需验证 list/detail 端点 |
| **5** | Chat | 本身已通，可作为完整的端到端验证 |

**不建议碰的顺序**（backend 能力缺口大，先碰会浪费时间）:
- Agent/Skills → 先补 backend capability
- Agent/MCP → 先补 backend capability
- Agent/Config → 先补 backend capability
- Agent/Workspace → 先补 backend capability

---

## 7. 是否建议继续沿此路线推进？

**是，但有条件**。

"HubOS 前端 + adapter 层 + 必要 backend补齐"路线是可行的，前提是：

1. **认知对齐**: 需要明确哪些是 adapter 可解决的问题，哪些必须 backend 补能力。本矩阵给出了明确分界线
2. **优先级纪律**: 不要在 `NEEDS_BACKEND_CAPABILITY` 的页面上投入 adapter 工作
3. **Auth 策略**: Dev bypass 只是开发期临时方案，真实 auth 集成迟早需要
4. **Skills 架构**: 需要和产品确认 HubOS skill 完整内容对 XClaw 的意义，再决定是否要补 backend capability

**路线评估**:
- 可 adapter 解决: ~23 个端点 → 约占 49%
- 需 backend 补齐: ~20 个端点 → 约占 43%
- 需 shape 验证: ~4 个端点 → 约占 8%

对于 43% 需要 backend 补齐的部分，如果 XClaw 本身不打算实现这些能力，则对应 HubOS 页面应标记为 `honest stub`，不做真实接入。

---

*Document created: 2026-04-19*
*Based on: HubOS console frontend API inventory + XClaw backend (localhost:8001) OpenAPI routes*
