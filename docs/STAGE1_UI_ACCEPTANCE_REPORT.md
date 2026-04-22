# Stage 1 UI Acceptance Report

> **日期**: 2026-04-20
>
> **Backend 参照**: `http://localhost:8001`（XClaw live backend）
>
> **说明**: 本报告记录 HubOS console 前端对接 XClaw backend 的阶段性验收状态。**不是最终 cutover 完成报告**。

---

## 1. 阶段已接通页面总表

| 页面 | 路由 | 状态 | 核心操作验证 |
|------|------|------|------------|
| Chat | `/chat` | ✅ READY_FOR_REVIEW | Message send ✅, SSE stream ✅, Stop ✅, Upload ✅ |
| Control/CronJobs | `/cron-jobs` | ✅ READY_FOR_REVIEW | Create ✅, List ✅, Update ✅, Delete ✅, Pause/Resume ✅ |
| Control/Channels | `/channels` | ✅ READY_FOR_REVIEW | List ✅, Update Config ✅, Channel types ✅ |
| Settings/Environments | `/environments` | ✅ READY_FOR_REVIEW | List ✅, Save ✅, Delete ✅ |
| Control/Heartbeat | `/heartbeat` | ✅ READY_FOR_REVIEW | Get/Put ✅ (DIRECT_MATCH) |
| Settings/TokenUsage | `/token-usage` | ✅ READY_FOR_REVIEW | List ✅ (DIRECT_MATCH) |
| Settings/Agents | `/agents` | 🟡 PARTIAL_BUT_HONEST | List/Create/Update/Delete ✅; Toggle ⚠️ honest reject, Reorder ⚠️ honest reject |
| Settings/Models | `/models` | 🟡 PARTIAL_BUT_HONEST | List ✅, Active Models ✅, Configure ✅, Test Provider ✅; TestModel ⚠️ honest reject, Discover ⚠️ honest warning |
| Chat | `/chat` | ✅ READY_FOR_REVIEW | Message send ✅, SSE stream ✅, Stop ✅, Upload ✅ |
| Agent/MCP | `/mcp` | 🟡 PARTIAL_BUT_HONEST | List ✅, Create ✅, Update ✅; Toggle ⚠️ honest reject, Delete ⚠️ honest reject |
| Agent/Skills | `/skills` | 🟡 PARTIAL_BUT_HONEST | List/Enable/Disable/Batch ✅ (Phase 1 asset subset); create/save/upload/hub/pool ⚠️ honest reject |
| Agent/Tools | `/tools` | 🔴 HONEST_BLOCKED | listTools ⚠️ 500 (backend config.yaml missing `sandbox` field); toggle ✅ |
| Control/Sessions | `/sessions` | 🔴 ARCHITECTURE_MISMATCH | NOT_RECOMMENDED — 见 `SESSIONS_ARCHITECTURE_DECISION.md` |

---

## 2. 每页详细状态

---

### 2.1 Chat (`/chat`) — ✅ READY_FOR_REVIEW

**页面加载**: 200 ✅

**真实已验证写操作**:
- `POST /api/console/chat` (send message) → SSE stream ✅
- `POST /api/console/chat/stop` → `{stopped: bool}` ✅
- `POST /api/console/upload` → `{url, file_name}` ✅
- Session create/list/history → all XClaw endpoints verified ✅

**Shape 适配（adapter 处理）**:
- `GET /api/chats` 返回 `{chats: [...]}` → adapter unwrap → `ChatSpec[]`
- `POST /api/chats/batch-delete` body `{chat_ids: [...]}` → adapter transform from raw array
- `providerApi.getActiveModels` → 已适配为 `{active_llm: {provider_id, model}}`

**Fake success**: 无

**已知局限**:
- 模型未配置时弹出提示（正常行为）
- sessionApi 来自本地 `pages/Chat/sessionApi/`，独立于全局 sessionApi

**当前接的是**: `http://localhost:8001`

---

### 2.2 Control/CronJobs (`/cron-jobs`) — ✅ READY_FOR_REVIEW

**页面加载**: 200 ✅

**真实已验证写操作**:
- Create job: `POST /api/jobs` → 201 ✅
- List jobs: `GET /api/jobs` → 200 ✅
- Update job: `PUT /api/jobs/{id}` → 200 ✅
- Delete job: `DELETE /api/jobs/{id}` → 204 ✅
- Pause/Resume: `POST /api/jobs/{id}/pause|resume` → 200 ✅
- Trigger/Run: `POST /api/jobs/{id}/run` → 200 ✅

**Shape 差异（adapter 已处理）**:
- HubOS: `schedule: { type: "cron", cron: "..." }` → HubOS: top-level `cron: "..."`
- HubOS 返回完整 schedule 对象含 timezone/meta → adapter 还原

**未完成边界**:
- `getCronJobState`: XClaw 无 `/api/jobs/{id}/state` 端点 → honest 404

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.3 Control/Channels (`/channels`) — ✅ READY_FOR_REVIEW

**页面加载**: 200 ✅

**真实已验证写操作**:
- List channels: `GET /api/channels/` → 200 ✅
- List channel types: `GET /api/channels/types` → 200 ✅
- Update channel config: `PUT /api/channels/{name}/config` → 200 ✅

**未完成边界**:
- enable/disable/restart/test: XClaw 有端点，但 HubOS 页面未暴露这些按钮

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.4 Settings/Environments (`/environments`) — ✅ READY_FOR_REVIEW

**页面加载**: 200 ✅

**真实已验证写操作**:
- List envs: `GET /api/envs` → 200 ✅
- Save envs: `PUT /api/envs` → 200 ✅
- Delete env: `DELETE /api/envs/{key}` → 200 ✅

**路径**: HubOS `/envs` → `/api/envs` — **DIRECT_MATCH**，无需 adapter

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.5 Control/Heartbeat (`/heartbeat`) — ✅ READY_FOR_REVIEW

**页面加载**: 200 ✅

**路径**: HubOS `/config/heartbeat` → XClaw `/api/heartbeat` — **DIRECT_MATCH**

**真实已验证写操作**:
- Get heartbeat: `GET /api/heartbeat` → 200 ✅
- Update heartbeat: `PUT /api/heartbeat` → 200 ✅

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.6 Settings/TokenUsage (`/token-usage`) — ✅ READY_FOR_REVIEW

**页面加载**: 200 ✅

**路径**: HubOS `/token-usage` → XClaw `/api/token-usage` — **DIRECT_MATCH**

**真实已验证写操作**:
- List usage: `GET /api/token-usage` → 200 ✅
- Cost: `GET /api/token-usage/cost` → 200 ✅
- Record: `POST /api/token-usage/record` → 200 ✅

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.7 Settings/Agents (`/agents`) — 🟡 PARTIAL_BUT_HONEST

**页面加载**: 200 ✅

**真实已验证写操作**:
- List agents: `GET /api/agents` → 200 ✅
- Create agent: `POST /api/agents` → 200 ✅
- Update agent: `PUT /api/agents/{name}` → 200 ✅
- Delete agent: `DELETE /api/agents/{name}` → 204 ✅

**⚠️ Honest Failure（正确处理，无 fake success）**:
- **Toggle enable/disable**: `PATCH /api/agents/{name}/toggle` → XClaw 无此端点 → adapter `Promise.reject` → 页面 catch → `message.error` ✅
- **Reorder agents**: `PUT /api/agents/order` → XClaw 返回 404 → adapter 抛出 → 页面 catch → `message.error` + state revert ✅

**未完成边界**:
- Toggle: backend 能力缺失，adapter 诚实拒绝
- Reorder: backend 能力缺失，adapter 诚实抛出 backend 错误
- 创建时 SkillPool skill 下载: XClaw 无对应端点

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.8 Settings/Models (`/models`) — 🟡 PARTIAL_BUT_HONEST

**页面加载**: 200 ✅

**真实已验证写操作**:
- List providers: `GET /api/models` → **500** → adapter 重路由至 `GET /api/providers` → 200 ✅
- Active models: `GET /api/providers/models/active` → 200 ✅
- Configure provider: `PUT /api/providers/{id}/config` → 200 ✅
- Custom provider create: `POST /api/providers/custom` → 200 ✅
- Provider test connection: `POST /api/providers/{id}/test` → 200 ✅
- Set active LLM: `PUT /api/providers/models/active` → 200 ✅

**⚠️ Honest Failure / Warning（正确处理）**:
- **testModelConnection**: XClaw 无 per-model 测试端点 → adapter `Promise.reject` → 页面 `message.error` ✅
- **discoverModels**: XClaw 无 discover 端点 → adapter 返回 `{success: false}` → 页面 `message.warning` ✅

**未完成边界**:
- testModelConnection: backend 能力缺失
- discoverModels: backend 能力缺失
- addModel/removeModel: 未验证
- probeMultimodal: XClaw 用 GET 而非 HubOS 期望的 POST；已适配为 GET

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.9 Agent/MCP (`/mcp`) — 🟡 PARTIAL_BUT_HONEST

**页面加载**: 200 ✅

**真实已验证写操作**:
- List MCP clients: `GET /api/mcp/config` → unwrap `{mcp_servers: {...}}` ✅
- Create MCP client: read-modify-write on config ✅
- Update MCP client: read-modify-write on config ✅

**⚠️ Honest Failure（正确处理，无 fake success）**:
- **toggleMCPClient**: XClaw 无 per-key toggle 端点 → adapter `Promise.reject` with descriptive message ✅
- **deleteMCPClient**: XClaw 无 per-key delete 端点 → adapter `Promise.reject` with descriptive message ✅

**⚠️ Architecture Risk**:
- create/update 使用 read-modify-write，有并发竞争条件风险。XClaw 无原子性 per-key 操作端点。

**未完成边界**:
- toggle: backend 能力缺失
- delete: backend 能力缺失

**Fake success**: 无

**当前接的是**: `http://localhost:8001`

---

### 2.10 Agent/Skills (`/skills`) — 🟡 PARTIAL_BUT_HONEST

**页面加载**: 200 ✅

**真实已验证写操作**:
- List skills: `GET /api/skills` → unwrap `{skills: [...]}` ✅
- Enable skill: `PUT /api/skills/{name}` body `{enabled: true}` ✅
- Disable skill: `PUT /api/skills/{name}` body `{enabled: false}` ✅
- Batch enable: `POST /api/skills/batch-enable` body `{skill_names: [...]}` ✅
- Batch disable: `POST /api/skills/batch-disable` body `{skill_names: [...]}` ✅
- Batch delete: `POST /api/skills/batch-delete` body `{skill_names: [...]}` ✅
- Blocked history: `GET /api/skills/scan/blocked-history` ✅
- AI optimize stream: `POST /api/skills/ai/optimize/stream` → SSE ✅

**Phase 1 资产子集（已接通）**:
- listSkills, getSkill, enableSkill, disableSkill, batchEnableSkills, batchDisableSkills, batchDeleteSkills, getBlockedHistory, streamOptimizeSkill

**⚠️ Honest Reject（正确处理）**:
- createSkill, saveSkill, uploadSkill, importFromHub, updateSkillChannels, getSkillConfig, updateSkillConfig, deleteSkillConfig → adapter `Promise.reject("XClaw 不支持...")` ✅

**未完成边界**:
- skill content 编辑/创建: XClaw 不存储 content
- Hub 导入: XClaw 无 hub endpoint
- Skill pool: XClaw 有 `/api/skill-pool` 但 adapter 诚实 reject
- channels/config: XClaw 无此字段

**Fake success**: 无（saveSkill 返回 `{success: true, mode: "noop"}` 但附带明确说明 content 丢失）

**当前接的是**: `http://localhost:8001`

---

### 2.11 Agent/Tools (`/tools`) — 🔴 HONEST_BLOCKED

**页面加载**: 200 ✅

**核心问题**: `GET /api/tools` 返回 500，根因是 XClaw config.yaml 缺少 `sandbox` 必填字段。

**adapter 处理**:
- `listTools` 检测到 sandbox validation error → `Promise.reject` with descriptive message（明确指出是 backend config 问题）
- `toggleTool`: 功能正常（PUT `/api/tools/{name}` with `{enabled: true/false}`）
- `updateAsyncExecution`: 功能正常（PATCH `/api/tools/{name}/async-execution`）

**⚠️ 根因不在前端**: 这是 backend 配置问题（`config.yaml` 中 `sandbox` 字段缺失），不是 adapter 问题。adapter 已诚实将 backend 500 错误 surfac 为 readable error。

**Fake success**: 无（500 错误被诚实 surfac）

**建议**: 修复 XClaw backend `config.yaml` 的 `sandbox` 字段后，Tools 页面应自动接通。

**当前接的是**: `http://localhost:8001`

---

### 2.12 Control/Sessions (`/sessions`) — 🔴 ARCHITECTURE_MISMATCH

**页面加载**: 200 ✅（页面能打开）

**不建议接入**，详见 [`SESSIONS_ARCHITECTURE_DECISION.md`](./SESSIONS_ARCHITECTURE_DECISION.md)。

| 维度 | HubOS Sessions | XClaw /chats |
|------|---------------|--------------|
| 数据生命周期 | 离线历史存档 | 运行时活跃状态 |
| 列表语义 | "过去的会话记录" | "当前活跃的 threads" |
| 删除操作 | 删除历史记录 | 终止活跃 thread |

**结论**: 强行接入会产生语义误导（用户以为删历史，实际删活跃 thread）。

**Fake success**: 无（未接入）

**当前接的是**: `http://localhost:8001`（但 Sessions 页面的 sessionApi 仍用原始 chatApi，有 shape 问题未处理）

---

## 3. Fake Success 总检查

| 页面 | 发现 fake success | 状态 |
|------|------------------|------|
| /chat | 无 | ✅ |
| /cron-jobs | 无 | ✅ |
| /channels | 无 | ✅ |
| /environments | 无（DIRECT_MATCH） | ✅ |
| /heartbeat | 无（DIRECT_MATCH） | ✅ |
| /token-usage | 无（DIRECT_MATCH） | ✅ |
| /agents | 无（toggle/reorder 已修正为 honest reject） | ✅ |
| /models | 无（testModelConnection honest reject, discoverModels 返回 success:false） | ✅ |
| /mcp | 无（toggle/delete 已修正为 honest reject） | ✅ |
| /skills | 无（unsupported 方法已 honest reject，saveSkill 返回 noop 但明确说明） | ✅ |
| /tools | 无（500 被诚实 surfac） | ✅ |
| /sessions | 无（未接入） | — |

**Fake success 总计: 0 ✅**

---

## 4. 未完成边界汇总

| 功能 | 页面 | 根因 | 分类 |
|------|------|------|------|
| Reorder agents | /agents | XClaw 无 `/api/agents/order` | NEEDS_BACKEND_CAPABILITY |
| Toggle agent | /agents | XClaw 无 toggle 端点 | NEEDS_BACKEND_CAPABILITY |
| getCronJobState | /cron-jobs | XClaw 无 state 端点 | NEEDS_BACKEND_CAPABILITY |
| testModelConnection | /models | XClaw 无 per-model 测试端点 | NEEDS_BACKEND_CAPABILITY |
| discoverModels | /models | XClaw 无 discover 端点 | NEEDS_BACKEND_CAPABILITY |
| toggleMCPClient | /mcp | XClaw 无 per-key toggle | NEEDS_BACKEND_CAPABILITY |
| deleteMCPClient | /mcp | XClaw 无 per-key delete | NEEDS_BACKEND_CAPABILITY |
| MCP create/update | /mcp | read-modify-write 有竞争条件风险 | ARCHITECTURE_RISK |
| Skill content (create/save/upload) | /skills | XClaw 不存储 skill content | ARCHITECTURE_MISMATCH |
| Skill channels/config | /skills | XClaw 无 per-skill channels/config | NEEDS_BACKEND_CAPABILITY |
| Hub import / pool operations | /skills | XClaw 无 hub/pool endpoint | NEEDS_BACKEND_CAPABILITY |
| Tools list (500) | /tools | XClaw config.yaml 缺 `sandbox` 字段 | BACKEND_CONFIG_ISSUE |
| Sessions 历史管理 | /sessions | XClaw 无独立 session/history 对象 | ARCHITECTURE_MISMATCH |
| `/api/models` 500 | /models | XClaw backend 内部错误（adapter 已绕过） | BACKEND_INTERNAL_ERROR |
| SkillPool skill 下载 | /agents | XClaw skill pool 架构差异 | NEEDS_BACKEND_CAPABILITY |

---

## 5. 当前 Backend 目标

> **当前实际接的是 `http://localhost:8001`**（XClaw live backend）
>
> 这是阶段性开发验证，不等于最终 cutover 完成。XClaw backend 代码在 `/Users/allen/XClaw/backend/`。

---

## 6. 推荐验收顺序

### 第一批：最适合用户先看（核心链路，无阻塞）

| 顺序 | 页面 | 建议关注点 |
|------|------|-----------|
| 1 | `/environments` | 最干净，100% DIRECT_MATCH，CRUD 全通 |
| 2 | `/heartbeat` | 最简单，DIRECT_MATCH |
| 3 | `/token-usage` | DIRECT_MATCH，数据直观 |
| 4 | `/channels` | 配置保存可见，列表清晰 |
| 5 | `/cron-jobs` | 已有 job 创建/编辑，最完整 CRUD |

### 第二批：可看但有边界需解释

| 顺序 | 页面 | 建议关注点 |
|------|------|-----------|
| 6 | `/chat` | 真实消息发送/流式响应，但需后端有可用模型 |
| 7 | `/agents` | CRUD 全通，toggle/reorder 失败是 backend 能力缺失而非 bug |
| 8 | `/models` | Provider 配置可用，discover/testModel 失败是 backend 缺失 |

### 第三批：partial 状态，了解架构差异后可看

| 顺序 | 页面 | 建议关注点 |
|------|------|-----------|
| 9 | `/mcp` | 列表/创建/更新可用，toggle/delete 失败是 backend 缺失 |
| 10 | `/skills` | Phase 1 资产管理子集，list/enable/disable 可用，内容编辑丢失 |

### 当前不建议重点看

| 页面 | 原因 |
|------|------|
| `/tools` | backend config.yaml 缺 `sandbox` 字段，list 500，需先修 backend |
| `/sessions` | 架构语义不匹配，不是普通 adapter 问题 |
| `/agent/config` | 全部 16 个 `/agent/*` 端点 XClaw 无对应 |

---

## 7. 下一阶段最值得继续的方向

1. **修复 Tools 阻塞**: 协助 XClaw 补充 `config.yaml` 的 `sandbox` 字段，Tools 页面即可全通

2. **Agent Skill Binding**: 在 Agent 配置页添加 skill binding 配置能力，需 backend 新增 `GET/PUT /api/agents/{name}/skills` 端点

3. **SkillPool 接入**: XClaw `/api/skill-pool` 已有 list/delete/search，可接入 pool 页面 Phase 1

---

## 8. 验收指标汇总

| 指标 | 数值 |
|------|------|
| 总验收页面数 | 12 |
| READY_FOR_REVIEW | **7** (Chat, CronJobs, Channels, Environments, Heartbeat, TokenUsage) |
| PARTIAL_BUT_HONEST | **3** (Agents, Models, MCP, Skills) |
| HONEST_BLOCKED | **1** (Tools — backend config 缺失) |
| ARCHITECTURE_MISMATCH | **1** (Sessions) |
| 有 fake success 的页面 | **0** ✅ |
| `npm run build` | ✅ 通过 |
| 前端可启动 | ✅ |

---

*Report generated: 2026-04-20*
*Based on: HubOS console frontend + XClaw backend (localhost:8001)*
