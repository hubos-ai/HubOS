# HubOS Sessions 产品与 API 设计草案

> **日期**: 2026-04-19
> **作者**: HubOS → HubOS adaptation team
> **目的**: 为 HubOS/XClaw 后端设计真正的会话历史管理能力，为 HubOS Sessions 页面提供完整接入蓝图
> **背景**: 当前 XClaw `/api/chats` 是活跃 thread 基础设施，不能作为 Sessions 数据源

---

## 1. 产品目标

### 1.1 Sessions 是什么

**Sessions（会话历史）** 是 HubOS 的跨渠道统一会话管理界面。

它是运维/管理人员查看、检索、管理平台**历史聊天记录**的集中入口。管理员可以按渠道、用户、会话名称等维度过滤历史会话，查看任意会话的聊天内容摘要，并进行重命名、删除等管理操作。

**核心用户场景**:
- 客服场景：查看某用户在某个渠道的历史会话，检查问题处理记录
- 合规审计：按日期范围导出某渠道的所有会话元数据
- 数据分析：统计各渠道会话量、用户活跃度（会话数、消息数）
- 运维管理：清理过期会话数据，批量删除不需要的历史记录

### 1.2 与 HubOS 原始 Sessions 的关系

HubOS 的 Sessions 页面**已经是**这个产品形态的原型：

| 功能 | HubOS Sessions 原型 | 目标 Sessions 能力 |
|------|-------------------|-----------------|
| 列表展示 | ✅ 有（Table） | ✅ 保留 |
| 按 user_id 过滤 | ✅ 有 | ✅ 保留 |
| 按 channel 过滤 | ✅ 有（动态下拉） | ✅ 保留，并支持多选 |
| 编辑会话名称 | ✅ 有（Drawer） | ✅ 保留 |
| 删除单个会话 | ✅ 有 | ✅ 保留，并记录删除操作 |
| 批量删除 | ✅ 有 | ✅ 保留 |
| 分页 | ✅ 有（10条/页） | ✅ 保留，可配置 |
| 按更新时间排序 | ✅ 有（默认降序） | ✅ 保留 |
| **聊天内容查看** | ❌ 无 | **新增**：点击行查看详情 |
| **Hermes 记忆检索** | ❌ 无 | **新增**：按自然语言检索会话内容 |
| **跨会话分析** | ❌ 无 | **新增**：统计视图 |
| **权限过滤** | ❌ 无 | **新增**：基于角色的会话可见性 |

### 1.3 与 XClaw `/api/chats` 的根本区别

| 维度 | XClaw `/api/chats`（当前） | 目标 Sessions（未来） |
|------|--------------------------|---------------------|
| **数据性质** | 运行时活跃 thread | 持久化历史存档 |
| **生命周期** | 随对话存在，对话结束即失效 | 独立于活跃对话，会话结束后仍保留 |
| **数据来源** | LangGraph thread state | 独立会话存储（可从 thread 归档） |
| **列表语义** | "当前活跃的 threads" | "历史会话记录" |
| **删除语义** | 终止活跃工作上下文（危险） | 删除历史存档（安全） |
| **过期策略** | 无主动归档 | 支持配置保留期（30d/90d/永久） |
| **权限模型** | 无权限隔离 | 基于角色+渠道的细粒度可见性 |
| **与 Hermes 的关系** | 无关联 | Hermes 为会话内容建索引，支持检索 |

**关键区别**: XClaw `/api/chats` 的每个 chat 是一个**活跃工作上下文**（含 agent state、tools、memory）；Sessions 的每个 session 是一个**离线存档**（含消息历史、用户标识、渠道信息）。

---

## 2. 数据组织

### 2.1 分层结构

```
Sessions
├── by Channel（渠道隔离）
│   ├── weixin（微信）
│   ├── feishu（飞书）
│   ├── slack
│   ├── telegram
│   └── ...
└── by Account within Channel（账号隔离）
    ├── weixin / user_openid_1
    ├── weixin / user_openid_2
    ├── feishu / union_id_1
    └── ...
```

### 2.2 会话独立记录（Session Document）

每个会话是一条独立记录，不依赖活跃 thread：

```typescript
interface Session {
  id: string;                    // 全局唯一 ID（UUID）
  session_key: string;             // 格式: "{channel}:{account_id}"，如 "weixin:oABC123"
  channel: string;                // 渠道名：weixin / feishu / slack / telegram / ...
  account_id: string;             // 渠道内用户唯一标识（openid / union_id / user_id）
  account_name?: string;          // 用户昵称（可选，从 Hermes 记忆补充）

  name: string;                   // 会话显示名称（用户可编辑）
  status: SessionStatus;          // "active" | "archived" | "deleted"

  created_at: string;             // ISO 8601，会话首次创建时间
  updated_at: string;             // ISO 8601，最后一条消息时间
  closed_at?: string;             // ISO 8601，会话关闭时间

  message_count: number;          // 消息总数
  token_used?: number;            // 本会话累计 token 消耗（可选）

  metadata: Record<string, unknown>;  // 渠道特定的扩展字段

  archived_from_thread_id?: string;   // 如果由 thread 归档而来，记录原始 thread ID
}

type SessionStatus = "active" | "archived" | "deleted";
```

### 2.3 会话内容文件独立存储

会话消息内容**不存储在 Session Document 中**，而是独立存储：

```
{storage_base}/
└── sessions/
    └── {channel}/
        └── {account_id}/
            └── {session_id}/
                ├── meta.json        # Session document（上面的结构）
                ├── messages.json    # 消息列表
                └── artifacts/       # 附件/文件（如果有）
```

**messages.json 结构**:
```typescript
interface Message {
  id: string;
  role: "user" | "assistant" | "system" | "tool";
  content: unknown;           // 文本或多媒体内容
  created_at: string;         // ISO 8601
  token_count?: number;       // token 统计
  metadata?: Record<string, unknown>;
}
```

**设计理由**: 消息内容通常远大于元数据，分离存储支持灵活的保留策略（messages.json 可单独配置保留期）、归档（移到冷存储）和删除（只删 meta 保留匿名统计）。

### 2.4 Hermes 记忆系统的参与

Hermes（XClaw 的 memory 系统）以**用户为中心**组织记忆，与 Sessions 的关系：

| Hermes 维度 | Sessions 维度 | 关联方式 |
|------------|-------------|---------|
| `user_id` | `account_id` within channel | Hermes 记忆按 user_id 组织；Sessions 需结合 channel + account_id |
| `workContext` | Session 的消息内容 | Hermes 为用户的跨会话工作上下文建模 |
| `facts` | Session 内特定事实 | 从单条消息中提取的事实存入 Hermes |
| 跨会话检索 | Hermes 不直接索引 session | **需要扩展 Hermes**：新增 `session_id` 维度，让记忆可按 session 检索 |

**Sessions 查询 Hermes 的场景**:

1. **用户上下文补充**: 查看会话详情时，通过 Hermes 获取该用户的 `workContext`，作为会话摘要的补充上下文（"该用户上次讨论的主题是 X"）

2. **自然语言检索会话**: 用户在 Sessions 页面搜索"关于 XX 问题的讨论"，后端将查询转发给 Hermes，Hermes 返回相关事实列表，每个事实关联到 `session_id`，前端据此过滤 Sessions 列表

3. **跨会话用户画像**: 在 Sessions 列表中显示每个用户的 Hermes `topOfMind`，帮助管理员快速了解用户背景

**需要 XClaw 后端扩展 Hermes 的能力**:
- `GET /api/memory/query` — 接受自然语言查询，返回相关记忆及关联的 `session_id` 列表
- 记忆记录中新增 `session_ids[]` 字段（一个事实可能来自多个会话）

---

## 3. 权限模型

### 3.1 角色定义

| 角色 | 权限范围 |
|------|---------|
| `super_admin` | 所有渠道的所有会话 |
| `channel_admin:{channel}` | 指定渠道的所有会话 |
| `user` | 仅自己的会话（按 channel + account_id 匹配） |

### 3.2 权限过滤规则

**所有 Sessions API 必须在 backend 层完成权限过滤**，禁止将未过滤数据交给前端。

```
Backend 权限过滤流程:
  用户请求 GET /api/sessions
  → 解析用户角色和 account_id
  → 应用权限过滤条件:
      super_admin:   无过滤（返回所有会话）
      channel_admin: channel = 指定渠道
      user:          channel + account_id = 用户的 channel:account_id
  → 返回过滤后的结果
  → 前端永远看不到无权限的会话
```

**实现位置**: 所有 `/api/sessions/*` 端点须在 router handler 入口处注入权限过滤中间件，与现有 HubOS auth 中间件集成。

### 3.3 API 层权限字段

API 响应中包含权限上下文（供前端 UI 条件渲染）：

```typescript
interface SessionsListResponse {
  sessions: Session[];
  pagination: {
    total: number;
    page: number;
    page_size: number;
  };
  // 权限上下文
  accessible_channels: string[];   // 当前用户可访问的渠道列表
  is_super_admin: boolean;
}
```

---

## 4. API 草案

**Base Path**: `/api/sessions`

### 4.1 GET /api/sessions — 列表

**Query Parameters**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `channel` | `string` | 按渠道过滤（可选，多选逗号分隔） |
| `account_id` | `string` | 按账号过滤（可选） |
| `status` | `SessionStatus` | 按状态过滤（默认返回非 deleted） |
| `q` | `string` | 自然语言检索（走 Hermes） |
| `start_date` | `string` | 按 created_at 范围起始（ISO 8601） |
| `end_date` | `string` | 按 created_at 范围结束 |
| `page` | `number` | 页码（默认 1） |
| `page_size` | `number` | 每页条数（默认 20，最大 100） |
| `sort` | `updated_at \| created_at` | 排序字段（默认 updated_at） |
| `order` | `asc \| desc` | 排序方向（默认 desc） |

**Response**:
```json
{
  "sessions": [
    {
      "id": "sess_001",
      "session_key": "weixin:oABC123",
      "channel": "weixin",
      "account_id": "oABC123",
      "name": "关于产品咨询",
      "status": "archived",
      "created_at": "2026-04-01T10:00:00Z",
      "updated_at": "2026-04-01T10:30:00Z",
      "closed_at": "2026-04-01T10:30:00Z",
      "message_count": 12,
      "metadata": {}
    }
  ],
  "pagination": {
    "total": 142,
    "page": 1,
    "page_size": 20
  },
  "accessible_channels": ["weixin", "feishu"],
  "is_super_admin": false
}
```

**权限过滤**: 中间件根据用户角色自动追加 `channel` / `account_id` 过滤条件。

---

### 4.2 GET /api/sessions/{id} — 详情

**Response**:
```json
{
  "session": {
    "id": "sess_001",
    "session_key": "weixin:oABC123",
    "channel": "weixin",
    "account_id": "oABC123",
    "name": "关于产品咨询",
    "status": "archived",
    "created_at": "2026-04-01T10:00:00Z",
    "updated_at": "2026-04-01T10:30:00Z",
    "closed_at": "2026-04-01T10:30:00Z",
    "message_count": 12,
    "token_used": 3840,
    "metadata": {}
  },
  "hermes_context": {
    "work_context": "该用户关注产品 A 的定价问题",
    "top_of_mind": "产品 A, 定价"
  }
}
```

**聊天内容**通过 `GET /api/sessions/{id}/messages` 单独获取（见 4.7）。

---

### 4.3 PUT /api/sessions/{id} — 更新会话元数据

**Request Body**:
```json
{
  "name": "新的会话名称"
}
```

**Response**: 返回更新后的 `Session` 对象。

---

### 4.4 DELETE /api/sessions/{id} — 删除会话

**行为**: 软删除（`status: "deleted"`），数据保留直到归档清理策略触发。

**Response**:
```json
{
  "success": true,
  "id": "sess_001"
}
```

---

### 4.5 POST /api/sessions/batch-delete — 批量删除

**Request Body**:
```json
{
  "session_ids": ["sess_001", "sess_002"]
}
```

**Response**:
```json
{
  "success": true,
  "deleted_count": 2,
  "failed_ids": []
}
```

**权限**: `super_admin` 或 `channel_admin` 可批量删除；普通用户只能删除自己的会话。

---

### 4.6 POST /api/sessions/query — 自然语言检索

**Request Body**:
```json
{
  "query": "关于 XX 问题的讨论",
  "channels": ["weixin", "feishu"],
  "page": 1,
  "page_size": 20
}
```

**行为**:
1. 后端将 `query` 发给 Hermes 记忆系统
2. Hermes 返回相关记忆及对应的 `session_id` 列表
3. 后端根据 `session_id` 列表查询 Sessions 列表
4. 返回匹配的 Sessions（去重、排序）

**Response**:
```json
{
  "sessions": [...],
  "query_context": {
    "hermes_matches": 5,
    "query": "关于 XX 问题的讨论"
  },
  "pagination": {...}
}
```

---

### 4.7 GET /api/sessions/{id}/messages — 获取会话消息

**Query Parameters**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `page` | `number` | 页码（消息可能很大，支持分页） |
| `page_size` | `number` | 每页条数（默认 50） |

**Response**:
```json
{
  "session_id": "sess_001",
  "messages": [
    {
      "id": "msg_001",
      "role": "user",
      "content": "你好，我想咨询产品 A",
      "created_at": "2026-04-01T10:00:00Z"
    },
    {
      "id": "msg_002",
      "role": "assistant",
      "content": "您好！产品 A 的定价是...",
      "created_at": "2026-04-01T10:00:30Z"
    }
  ],
  "pagination": {
    "total": 12,
    "page": 1,
    "page_size": 50
  }
}
```

---

### 4.8 GET /api/sessions/channels — 获取有会话的渠道列表

**Response**:
```json
{
  "channels": [
    {
      "name": "weixin",
      "session_count": 89,
      "latest_session_at": "2026-04-19T08:00:00Z"
    },
    {
      "name": "feishu",
      "session_count": 45,
      "latest_session_at": "2026-04-18T22:00:00Z"
    }
  ]
}
```

**权限过滤**: 返回用户有权限的渠道。

---

### 4.9 GET /api/sessions/accounts — 获取指定渠道的账号列表

**Query Parameters**:

| 参数 | 类型 | 说明 |
|------|------|------|
| `channel` | `string` | **必填**，渠道名 |

**Response**:
```json
{
  "channel": "weixin",
  "accounts": [
    {
      "account_id": "oABC123",
      "session_count": 7,
      "latest_session_at": "2026-04-19T08:00:00Z"
    }
  ]
}
```

---

## 5. 前端页面映射

### 5.1 当前 HubOS Sessions 页面组件

```
SessionsPage
├── PageHeader + FilterBar
│   ├── filterUserId（Input）
│   └── filterChannel（Select — 从 listChannelTypes 加载）
├── Table（核心列表）
│   ├── ID
│   ├── Name
│   ├── SessionID
│   ├── UserID
│   ├── Channel（Tag）
│   ├── CreatedAt
│   ├── UpdatedAt（默认降序排序）
│   └── Action（Edit / Delete）
├── rowSelection（批量选择）
├── 批量删除按钮（选中时显示）
└── SessionDrawer（编辑名称）
```

### 5.2 可直接复用的区域

| 前端组件 | 复用可行性 | 说明 |
|---------|----------|------|
| PageHeader + FilterBar 布局 | ✅ 完全复用 | 布局不变，API 数据源替换 |
| Table 列表主体 | ✅ 完全复用 | 列定义不变，数据来自新 API |
| `filterUserId`（按 account_id 过滤） | ✅ 完全复用 | 对应 API 的 `account_id` 参数 |
| `filterChannel`（按渠道过滤） | ✅ 完全复用 | 对应 API 的 `channel` 参数 |
| 排序（UpdatedAt 降序） | ✅ 完全复用 | 对应 API 的 `sort=updated_at&order=desc` |
| 分页 | ✅ 完全复用 | 对应 API 的 `page` + `page_size` |
| rowSelection + 批量删除 | ✅ 完全复用 | 调用 `POST /api/sessions/batch-delete` |
| SessionDrawer（编辑名称） | ✅ 完全复用 | 调用 `PUT /api/sessions/{id}` |

### 5.3 需要等真实 backend 的交互

| 交互 | 当前 HubOS 状态 | 依赖条件 |
|------|---------------|---------|
| 点击行查看聊天内容 | ❌ 当前无此功能 | 需要 `GET /api/sessions/{id}/messages` |
| 自然语言检索（搜索框） | ❌ 当前无此功能 | 需要 `POST /api/sessions/query`（依赖 Hermes 扩展） |
| 渠道统计（session_count） | ❌ 当前只显示 channel 下拉 | 需要 `GET /api/sessions/channels` |
| 账号列表下拉 | ❌ 当前 user_id 是自由文本输入 | 需要 `GET /api/sessions/accounts` |
| Hermes 上下文补充（侧边栏） | ❌ 当前无此功能 | 需要 Hermes 新增 `session_id` 索引 |
| 软删除 vs 硬删除标识 | ❌ 当前直接删除 | 需要 backend 实现软删除 |

### 5.4 当前阶段页面标注建议

在真实 Sessions backend 实现之前，建议在 HubOS Sessions 页面加以下标注：

**方案：在 FilterBar 下方加信息条**

```
┌─────────────────────────────────────────────────────────────┐
│ ⚠️ Sessions 历史管理功能暂未接入。当前显示为 XClaw 活跃     │
│ threads，非历史会话记录。       [了解详情]                   │
└─────────────────────────────────────────────────────────────┘
```

"了解详情" 链接到 `SESSIONS_ARCHITECTURE_DECISION.md`。

**目的**: 诚实告知用户当前状态，避免用户误以为看到的列表是完整的历史会话。

---

## 6. 实施建议

### 6.1 三个选项对比

#### Option A: 先保持未接入（推荐第一步）

**做法**: 不写任何 adapter，在 HubOS Sessions 页面加信息条注明"暂未接入"。

**优点**:
- 零开发成本
- 不产生误导
- 为真正实现争取时间

**缺点**:
- 用户完全无法使用该页面

#### Option B: 先做只读列表（推荐作为第二阶段）

**做法**:
1. XClaw backend 新增 `/api/sessions` router（只读）
2. `GET /api/sessions` — 列表（从 LangGraph thread 归档数据，channel + account_id 从 metadata 提取）
3. `GET /api/sessions/{id}` — 详情
4. `GET /api/sessions/{id}/messages` — 消息历史（从 LangGraph thread state 读取）
5. HubOS 前端 adapter 对接

**额外工作（backend）**:
- 设计 Session document 存储结构（可复用 JSON 文件存储）
- 实现 thread → session 的归档流程（thread 结束时自动归档）
- 不需要 Hermes 扩展（第一阶段不做检索）

**优先级**: `GET /api/sessions` 最先实现，因为这是页面加载时唯一调用的接口。

#### Option C: 先做完整查询+详情（推荐作为第三阶段）

**做法**: 在 Option B 基础上增加：
- `POST /api/sessions/query`（依赖 Hermes 自然语言检索扩展）
- `GET /api/sessions/channels` + `GET /api/sessions/accounts`（统计视图）
- Hermes `session_id` 索引支持
- 权限模型完整实现

**额外工作**:
- Hermes 系统扩展（支持按 session_id 检索）
- 权限中间件实现
- 软删除逻辑

### 6.2 推荐实施顺序

```
当前状态
   │
   ▼
Phase 0: Option A
  └─ 不接入，加信息条（现在就可以做，零成本）
   │
   ▼
Phase 1: Option B — 只读列表
  ├─ Backend: 实现 /api/sessions（列表+详情+消息）
  ├─ Backend: 设计 Session 存储结构（JSON 文件即可）
  ├─ Backend: 实现 thread → session 归档流程
  └─ Frontend: adapter 对接，页面完全可用（只读）
   │
   ▼
Phase 2: Option C — 完整查询+权限
  ├─ Backend: Hermes 扩展（session_id 索引）
  ├─ Backend: /api/sessions/query（自然语言检索）
  ├─ Backend: 权限中间件
  ├─ Backend: 软删除
  └─ Frontend: 检索 UI + Hermes 上下文展示
```

**为什么推荐这个顺序**:

1. **Phase 0 零成本**：当前立即执行，不消耗任何开发资源，不产生误导
2. **Phase 1 最小可行产品**：只读列表是 Sessions 最核心的功能，用户可以查看历史会话，backend 改动量可控
3. **Phase 2 完整价值**：检索+权限是 Sessions 的差异化价值，需要 Hermes 配合，适合作为独立里程碑

---

## 7. 结论

### 7.1 为什么当前不应接 XClaw `/api/chats`

**原因 1: 语义不匹配是根本性的**

HubOS Sessions 的产品语义是**历史会话管理**（离线、存档、可安全删除），而 XClaw `/api/chats` 的语义是**活跃 thread 管理**（运行时、不可轻易删除）。这不是 adapter 能解决的语法差异。

**原因 2: 强行接入会产生严重的用户误导**

用户在 Sessions 页面看到"历史会话"列表，点了 Delete，实际上是在终止一个可能正在运行的 LangGraph thread。这个误操作的后果无法通过 UI 文案完全消除。

**原因 3: 数据不完整**

XClaw `/api/chats` 只返回当前活跃的 threads。如果 LangGraph 的 thread retention 策略不保留历史数据，用户连"历史"都看不到——看到的只是"当前活跃"。

**原因 4: 权限模型完全缺失**

XClaw `/api/chats` 无任何权限隔离。管理员在 Sessions 页面期望看到"我管理范围内的所有会话"，但实际上会看到（或者看不到）不该看到的数据。

### 7.2 真正的 Sessions 路线

**推荐路径**: Phase 0 → Phase 1 → Phase 2（见上方实施顺序）

每个阶段都是可独立验收的里程碑，不依赖后续阶段。

**与 XClaw `/api/chats` 的关系**:
- `/api/chats` 继续作为活跃对话基础设施（Chat 页面使用）
- 新增 `/api/sessions` 作为独立的历史会话管理系统
- 两者通过"thread 归档"流程连接：活跃 thread 结束时，自动生成一条 Session 记录

**最终状态**: HubOS Sessions 页面完整功能激活，历史会话管理 + 检索 + 权限隔离全部实现。

---

*文档版本: 1.0*
*创建日期: 2026-04-19*
*基于: HubOS console Sessions 页面原型 + XClaw backend 架构*
