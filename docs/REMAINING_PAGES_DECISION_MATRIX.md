# 剩余页面决策清单

> **日期**: 2026-04-19
> **目的**: 在继续接新页面之前，评估每个剩余页面的架构兼容性和接入风险，避免像 Sessions 一样做到一半才发现语义错位
> **评估范围**: Settings/Security、Agent/Tools、Agent/MCP、Agent/Skills、Agent/SkillPool、Agent/Workspace、Agent/Config、Chat

---

## 分类标准说明

| 分类 | 含义 |
|------|------|
| **READY_TO_ADAPT** | 路径+形状基本兼容，adapter 工作量低，可直接接入 |
| **PARTIAL_BUT_HONEST** | 部分端点有对应能力，部分缺失，需要 honest partial 处理缺失部分 |
| **ARCHITECTURE_MISMATCH** | 后端设计目的与前端产品语义不匹配，强行接入会产生误导 |
| **NEEDS_BACKEND_CAPABILITY** | 后端路由不存在，需要 backend 先实现，再做 adapter |
| **DO_NOT_TOUCH_YET** | 缺少关键信息，暂无法判断，先不碰 |

---

## 总览矩阵

| 页面 | 路由 | 分类 | 接入风险 | 推荐动作 |
|------|------|------|---------|---------|
| Settings/Security | `/security` | 🔴 ARCHITECTURE_MISMATCH | 高 — 安全模型完全不对齐 | 不建议接入 |
| Agent/Tools | `/tools` | 🟡 PARTIAL_BUT_HONEST | 低 — 端点基本兼容 | 可接入，需 honest 处理 toggle |
| Agent/MCP | `/mcp` | 🟡 PARTIAL_BUT_HONEST | 低 — GET/PUT 有，CRUD 缺 | 可接入，需 honest 处理 create/delete |
| Agent/Skills | `/skills` | 🟡 PARTIAL_BUT_HONEST | 中 — 路径和形状有差异 | 可接入，但部分操作有差异 |
| Agent/SkillPool | `/skill-pool` | 🟡 PARTIAL_BUT_HONEST | 中 — 路径前缀不同 | 可接入，需 adapter 路径映射 |
| Agent/Workspace | `/workspace` | 🔴 ARCHITECTURE_MISMATCH | 高 — 文件浏览器无 backend 对应 | 不建议接入文件浏览功能 |
| Agent/Config | `/agent/config` | 🔴 ARCHITECTURE_MISMATCH | 极高 — 12 个端点缺 10 个 | 不建议接入 |
| Chat | `/chat` | 🟡 PARTIAL_BUT_HONEST | 中 — 会话管理语义不匹配但核心功能可用 | 可接入，注意会话语义差异 |

---

## 逐页分析

---

### 1. Settings/Security — 🔴 ARCHITECTURE_MISMATCH

**HubOS 产品语义**: 运维安全管控台，三个 Tab：
- **ToolGuard**: 工具调用规则引擎（guarded_tools、denied_tools、自定义规则含 severity/category/patterns/disable_rules）
- **FileGuard**: 路径级文件访问防护（精确路径列表）
- **SkillScanner**: Skill 安全扫描（block/warn、blocked-history 管理、whitelist 管理）

**XClaw 后端现状** (`/api/security`):
```
GET/PUT /api/security/config → {
  tool_guard_enabled: bool,
  file_guard_enabled: bool,
  skill_scanner_auto: bool,
  whitelist: string[]   // ← 这里是敏感文件路径列表，不是 skill whitelist
}
```

**根本差异**:
- XClaw `whitelist` = FileGuard 的敏感文件路径列表，与 SkillScanner 的 skill whitelist 完全不是一回事
- XClaw 没有 ToolGuard 规则引擎（guarded_tools/denied_tools/custom_rules/disable_rules）
- XClaw 没有 blocked-history、whitelist 管理端点
- SkillScanner 只有全局开关（block/warn/off），没有 per-skill 的 blocked-history

**强行接入的误导**: 用户在 Security 页面配置的"安全规则"实际上不会被 XClaw 执行——ToolGuard 规则完全不会被 backend 生效。

**结论**: **DO NOT ADAPT — ARCHITECTURE_MISMATCH**

---

### 2. Agent/Tools — 🟡 PARTIAL_BUT_HONEST

**HubOS 端点**:
- `GET /tools` → `ToolInfo[]`
- `PATCH /tools/{name}/toggle` → `ToolInfo`（切换 enabled）
- `PATCH /tools/{name}/async-execution` → `ToolInfo`

**XClaw 端点**:
- `GET /api/tools` → `{tools: ToolResponse[]}`（需 unwrap）
- `PUT /api/tools/{tool_name}`（body: `{enabled: bool}`）→ `ToolResponse`
- `PATCH /api/tools/{tool_name}/async-execution` → `dict`（确认 async）

**差异**:
- HubOS 用 `PATCH /tools/{name}/toggle`，XClaw 用 `PUT /api/tools/{tool_name}`（body 相同）
- HubOS 返回 `ToolInfo`，XClaw 返回 `ToolResponse`（字段基本兼容，都有 name/enabled/description）
- `GET /tools` 需 unwrap `{tools: []}` → `ToolInfo[]`

**缺失能力**: 无本质缺失，端点对应关系清晰。

**结论**: **PATH_ADAPTER_ENOUGH + SHAPE_ADAPTER_ENOUGH**，adapter 工作量低。建议接入。

---

### 3. Agent/MCP — 🟡 PARTIAL_BUT_HONEST

**HubOS 端点**:
- `GET /config/mcp` → MCP server 列表
- `POST /config/mcp` → 创建 server
- `PUT /config/mcp` → 全量更新 servers
- `DELETE /config/mcp/{key}` → 删除 server
- `PATCH /config/mcp/{key}` → 部分更新 server

**XClaw 端点**:
- `GET /api/mcp/config` → `McpConfigResponse`（mcp_servers 字典）
- `PUT /api/mcp/config` → 全量更新

**差异**:
- XClaw 只有 GET/PUT，无 POST/DELETE/PATCH 细粒度 CRUD
- HubOS 的 MCPClientCard 支持 toggle enabled、delete、update，但没有直接的 toggle endpoint → HubOS 每次改完一个 server 需要调 PUT 全量更新

**缺失能力**: POST/DELETE/PATCH per-server 操作。HubOS 页面期望的 toggle/delete/edit 需要通过 PUT 全量更新实现（效率低但功能可行）。

**结论**: **PARTIAL_BUT_HONEST — GET/PUT 已有，细粒度 CRUD 需 honest 处理**。建议接入，但需要 adapter 处理 PUT 全量更新逻辑。

---

### 4. Agent/Skills — 🟡 PARTIAL_BUT_HONEST

**HubOS 端点**:
- `GET /skills` → `SkillSpec[]`（含 content、source）
- `POST /skills` → 创建 skill
- `PUT /skills/{name}` → 更新 skill
- `DELETE /skills/{name}` → 删除 skill
- `POST /skills/{name}/toggle` → 切换 enabled
- `POST /skills/upload` → 上传 zip
- `POST /skills/import-from-hub` → 从 hub 导入
- `POST /skills/batch-delete` → 批量删除

**XClaw 端点**:
- `GET /api/skills` → `SkillsListResponse`（无 content）
- `PUT /api/skills/{skill_name}` → 更新 enabled 状态
- `POST /api/skills/install` → 从 .skill 文件安装（thread_id + virtual_path）
- `POST /api/skills/batch-enable/disable/delete`
- `GET /api/skills/scan/blocked-history`
- `DELETE /api/skills/scan/blocked-history`
- `POST /api/skills/ai/optimize/stream`（AI 优化）

**差异**:
- XClaw `GET /api/skills` 返回的 SkillResponse **无 content 字段**（只有 name/description/license/category/enabled）
- HubOS Skill 页面编辑需要 skill content，XClaw 不存储 content（只管理 enabled 状态）
- HubOS 的 `POST /skills` = 创建 skill 包含 content；XClaw 无此操作
- HubOS `POST /skills/upload` = 上传 zip 创建 skill；XClaw 用 `POST /api/skills/install`（从 thread 虚拟路径安装）
- HubOS `import-from-hub` = 从 URL 导入；XClaw 无此端点

**关键问题**: HubOS Skills 页面可以编辑 skill content，XClaw 后端**不存储 content**，只管理 enabled 状态。编辑 content 会导致数据丢失。

**结论**: **PARTIAL_BUT_HONEST — enabled 管理部分可用，content 管理不兼容**。建议接入，但 skill content 编辑/save 需 honest 处理（save 后数据不落地）。

---

### 5. Agent/SkillPool — 🟡 PARTIAL_BUT_HONEST

**HubOS 端点**（前缀 `/skills/pool`）:
- `GET /api/skills/pool` → pool 列表
- `POST /api/skills/pool/refresh` → 刷新
- `POST /api/skills/pool/upload-zip` → 上传 zip
- `POST /api/skills/pool/download` → 下载 skill 到 workspace
- `DELETE /api/skills/pool/{name}` → 从 pool 删除
- `POST /api/skills/pool/batch-delete` → 批量删除
- `GET /api/skills/pool/builtin-sources` → 内置源
- `POST /api/skills/pool/import-builtin` → 导入内置

**XClaw 端点**（前缀 `/api/skill-pool`）:
- `GET /api/skill-pool` → pool 列表
- `POST /api/skill-pool/search` → 搜索（本地 pool）
- `POST /api/skill-pool/install` → 安装（name/description/url/version）
- `DELETE /api/skill-pool/{skill_name}` → 删除

**差异**:
- HubOS: `/skills/pool` → XClaw: `/api/skill-pool`（路径前缀不同）
- HubOS 支持 zip 上传/下载，XClaw 无这些操作
- HubOS 有 broadcast（推送到多个 workspace），XClaw 无对应
- HubOS 有 builtin-sources + import-builtin，XClaw 无对应
- HubOS 的 `download` = 从 pool 下载到 workspace；XClaw 无此操作
- HubOS 有 refresh，XClaw 无（但 refresh 语义是"重新加载 pool metadata"）

**结论**: **PARTIAL_BUT_HONEST — 列表+删除基本兼容，broadcast/upload-zip/import-builtin/discover 等操作缺失**。建议接入，但 broadcast/upload 需 honest 处理。

---

### 6. Agent/Workspace — 🔴 ARCHITECTURE_MISMATCH

**HubOS 产品语义**: Workspace 文件浏览器 + 编辑器，包含：
- 文件列表浏览（含 dailyMemoryLogs）
- 文件内容读取/编辑/保存
- 文件启用/禁用
- 文件顺序拖拽排序
- Zip 上传/下载（整个 workspace）

**XClaw 后端** (`/api/workspace`):
- `GET /api/workspace/download` → zip 下载
- `POST /api/workspace/upload` → zip 上传（合并，不是替换）

**根本差异**:
- HubOS Workspace 是**文件级浏览和编辑**界面
- XClaw `/api/workspace` 只有**整个 workspace zip 的上传下载**
- HubOS 的 `files` 列表、`fileContent` 编辑、`dailyMemoryLogs` 在 XClaw **完全没有对应端点**

**强行接入的误导**: 用户在 Workspace 页面看不到任何文件（XClaw 只返回 zip），编辑功能完全无法使用。上传/下载 zip 的语义与 HubOS 的"文件管理"完全不符。

**结论**: **DO NOT ADAPT — ARCHITECTURE_MISMATCH**（zip 上传下载功能除外——这两个可以接入，但只是 zip 操作，不是 workspace 文件管理）

---

### 7. Agent/Config — 🔴 ARCHITECTURE_MISMATCH

**HubOS 端点**（12 个）:
```
GET/PUT  /agent/running-config       ← AgentsRunningConfig（retry/rate_limit/context 等）
GET/PUT  /agent/language
GET/PUT  /agent/timezone
GET      /agent/embedding-config
GET      /agent/memory-summary
GET      /agent/llm-retry
GET      /agent/llm-rate-limiter
GET      /agent/context-compact
GET      /agent/tool-result-compact
GET      /user-profile
GET/PUT  /config/user-timezone
```

**XClaw 端点**（2 个有交集）:
```
GET/PUT  /api/agents/{name}          ← 部分对应（model/tool_groups/soul）
GET/PUT  /api/user-profile           ← 对应
```

**其余 10 个端点 XClaw 完全不存在**。

**HubOS Agent/Config 页面的 7 个 Card**:
- ReactAgentCard: language（多语言模板）、timezone
- LlmRetryCard: llm_retry_enabled、llm_max_retries、llm_backoff_base/cap
- LlmRateLimiterCard: rate limit 配置
- ContextCompactCard: context 压缩配置
- ToolResultCompactCard: tool result 处理配置
- MemorySummaryCard: memory 相关摘要
- EmbeddingConfigCard: embedding 配置

**这些配置项 XClaw 完全没有对应的存储或 API**。

**结论**: **DO NOT ADAPT — 12 个端点缺 10 个，且缺失的是 HubOS 核心配置模型**。接入后页面所有 Card 都是空的或报 404。

---

### 8. Chat — 🟡 PARTIAL_BUT_HONEST

**HubOS Chat 页面架构**:
- 使用 `@hubos-ai/chat` 组件库
- `sessionApi` → HubOS 自实现的会话管理（基于 `/chats/*` 端点）
- `chatApi` → 文件上传（`/console/upload`）、streaming chat
- `customFetch` → `POST /console/chat`（streaming 实时对话）
- `providerApi.getActiveModels()` → 获取当前 agent 的 active LLM

**XClaw 对应能力**:
- `/api/chats` → LangGraph threads（活跃 thread，非历史 session）
- `/api/console/chat` → XClaw 的 `POST /api/console/chat`（HubOS stream chat）
- `/api/providers/models/active` → active model（已接入）

**关键差异**:
1. **Sessions = Chat 中的 thread 管理**: Chat 页面左侧的 session 列表来自 `sessionApi`（`/chats`），这些"session"本质上是 LangGraph threads。Sessions 页面和 Chat 页面共用同一套 `/chats` 端点——Sessions 看到的是"历史 thread"，Chat 看到的是"活跃 thread"，两者是同一数据的不同视图。
2. **Streaming chat**: HubOS `POST /console/chat` ↔ XClaw `POST /api/console/chat`，路径相同
3. **Session 语义**: 与 Sessions 页面同样的问题，但 Chat 是**活跃对话入口**，用户期望看到活跃 thread，所以这里的语义错位不如 Sessions 严重

**Chat 页面 vs Sessions 页面的区别**:
- Sessions 页面 = 离线历史视角，XClaw threads 不等于"历史会话" → ARCHITECTURE_MISMATCH
- Chat 页面 = 活跃对话入口，XClaw threads 作为"活跃 thread"基本对等 → PARTIAL_BUT_HONEST

**结论**: **PARTIAL_BUT_HONEST — 核心聊天功能可用（streaming、model selection），session 列表语义有差异但不阻断**。Chat 是 HubOS 的核心页面，建议接入。

---

## 推荐优先级

### 最推荐继续做的 3 页

| 优先级 | 页面 | 理由 |
|--------|------|------|
| 🥇 1 | **Agent/Tools** | 端点对应清晰，adapter 工作量低（<1h），风险最低 |
| 🥈 2 | **Agent/MCP** | GET/PUT 已有，细粒度 CRUD 缺失但可 honest 处理，页面核心功能可用 |
| 🥉 3 | **Chat** | HubOS 核心页面，streaming chat 路径对齐，session 语义差异不阻断主要操作 |

### 最不建议碰的 3 页

| 优先级 | 页面 | 理由 |
|--------|------|------|
| 🚫 1 | **Agent/Config** | 12 个端点缺 10 个，所有配置 Card 都无 backend 对应，接入后页面全空 |
| 🚫 2 | **Settings/Security** | 安全模型完全不对齐，用户配置的安全规则不生效，比"功能缺失"更危险 |
| 🚫 3 | **Agent/Workspace** | 文件浏览器功能 XClaw 无对应，强行接入产生严重误导 |

### 是否建议先暂停前端接页，转去补 backend capability？

**建议: 部分同意**

**不建议暂停的理由**:
- Tools、MCP、Chat 的 adapter 工作量低、风险可控，可以并行进行
- 这些页面的部分能力（Tools list/toggle、MCP GET/PUT、Chat streaming）已经可以工作

**建议暂停，转去补 backend capability 的页面**:
- **Agent/Config**: 10/12 端点缺失，前端 adapter 无论如何努力都无法弥补这个差距。应该先向 XClaw 后端提出 capability requirement。
- **Settings/Security**: 安全模型的架构差异太大，不是 adapter 能解决的。

**推荐路线**:
```
当前状态
   │
   ├─→ 继续接入: Tools（极低风险）、MCP（中低风险）、Chat（中风险）
   │
   └─→ 暂停接入，发起 backend capability 讨论:
        ├─ Agent/Config: 需要 XClaw 新增 10 个配置端点
        └─ Settings/Security: 需要 XClaw 重构安全模型（ToolGuard 规则引擎）
```

---

## 附录：各页面详细端点对照

### Agent/Tools 端点对照

| HubOS | XClaw | 适配说明 |
|--------|--------|---------|
| `GET /tools` | `GET /api/tools` | 需 unwrap `{tools: []}` |
| `PATCH /tools/{name}/toggle` | `PUT /api/tools/{tool_name}` | 差异：PATCH → PUT，其余相同 |
| `PATCH /tools/{name}/async-execution` | `PATCH /api/tools/{name}/async-execution` | ✅ 完全匹配 |

### Agent/MCP 端点对照

| HubOS | XClaw | 适配说明 |
|--------|--------|---------|
| `GET /config/mcp` | `GET /api/mcp/config` | ✅ 形状兼容 |
| `PUT /config/mcp` | `PUT /api/mcp/config` | ✅ 形状兼容 |
| `POST /config/mcp`（create） | ❌ 不存在 | honest partial：改用 PUT 全量更新 |
| `DELETE /config/mcp/{key}` | ❌ 不存在 | honest partial |
| `PATCH /config/mcp/{key}` | ❌ 不存在 | honest partial |

### Agent/Skills 端点对照

| HubOS | XClaw | 适配说明 |
|--------|--------|---------|
| `GET /skills` | `GET /api/skills` | XClaw 无 content 字段 |
| `PUT /skills/{name}` | `PUT /api/skills/{name}` | ✅ enabled 更新兼容 |
| `POST /skills`（创建含 content） | ❌ 不存在 | honest partial |
| `POST /skills/upload` | `POST /api/skills/install` | XClaw 从 thread 虚拟路径安装，不是 zip |
| `POST /skills/import-from-hub` | ❌ 不存在 | honest partial |

### Agent/SkillPool 端点对照

| HubOS | XClaw | 适配说明 |
|--------|--------|---------|
| `GET /api/skills/pool` | `GET /api/skill-pool` | 路径前缀不同 |
| `DELETE /api/skills/pool/{name}` | `DELETE /api/skill-pool/{name}` | ✅ 匹配 |
| `POST /api/skills/pool/broadcast` | ❌ 不存在 | honest partial |
| `POST /api/skills/pool/upload-zip` | ❌ 不存在 | honest partial |

### Chat 端点对照

| HubOS | XClaw | 适配说明 |
|--------|--------|---------|
| `POST /console/chat`（streaming） | `POST /api/console/chat` | ✅ 路径匹配 |
| `GET /chats`（session list） | `GET /api/chats` | ✅ 路径匹配，语义差异见 Sessions 分析 |
| `GET /providers/models/active` | `GET /api/providers/models/active` | ✅ 已接入 |
| `POST /console/upload` | ✅ XClaw 有 uploads router | 需确认路径 |

---

*文档版本: 1.0*
*创建日期: 2026-04-19*
