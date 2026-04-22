# HubOS 记忆分层架构 (L1–L4)

> **Stage A.5 决策文档** · 定格 HubOS 总经理 agent + hubos.core 运行端的四层记忆模型，
> 说明每层的所有权、生命周期、写入触发、读取触发、当前实现与未来扩展点。

---

## 1. 结论（给赶时间的人）

HubOS 把记忆切成 **4 层**，每层一个明确的所有者和生命周期，互相之间通过**单向写入 + 按需读取**衔接：

| 层 | 名称 | 作用域 | 生命周期 | 当前实现 | 状态 |
|----|----|----|----|----|----|
| **L1** | Working memory | 一个 LLM turn | 几秒 | host-app InMemoryMemory（per-request agent 实例） | ✅ 已就位 |
| **L2** | Short-term memory | 一个 chat session | 几小时～几天 | host-app session manager + JSON 落盘 | ✅ 已就位 |
| **L3** | Mid-term memory | 一个 agent / 一个 user | 几天～几月 | host-app agent profile + `hubos.core.memory.MemoryService`（占位） | ⚠️ 占位 |
| **L4** | Long-term semantic memory | 跨 session / 跨 agent / 跨 user | 永久 | `hubos.core.memory.LocalMemoryStore`（文件落盘） | ✅ 已就位（sa5-1） |

**关键约定**：

1. **任何层的写入都不阻塞 LLM turn**。L4 写入是 fire-and-forget；摘要 / 归档异步发生。
2. **任何层的读取都明确受 caller 控制**。GM agent 主动调 `recall_long_term(...)`/`recall_session(...)`，
   不会有"系统偷偷塞进 prompt"的隐性注入。
3. **L4 后端可替换**。所有 L4 后端实现 `hubos.core.memory.MemoryStore` Protocol，
   `LocalMemoryStore` 只是默认；后续可换 embedding 检索 / 远端 hosted memory，
   而不影响上层 GM persona 模板和工具签名。

---

## 2. 分层详解

### 2.1 L1 · Working memory（一个 LLM turn）

| 项 | 内容 |
|---|---|
| 所有者 | host-app per-request agent instance |
| 生命周期 | 一个 query → response 周期，几秒 |
| 落盘 | 否（纯内存） |
| 写入触发 | LLM 输出 token / tool_call / tool_response 实时累加 |
| 读取触发 | 同 turn 内的工具调用 / 后续 token 生成 |

**为什么需要这一层**：模型 context window 内的滚动状态、tool_call 中间结果、reasoning 中
间产物，都不应也不能写到任何外部存储——它们只在当前 turn 有意义。

**为什么不在这层做更多**：试图在 L1 持久化任何内容都会引入"agent 之间互相串"的风险
（参见 `architecture-session-isolation.md` §2.1：每次请求新建 agent 实例正是为了 L1 天然隔离）。

---

### 2.2 L2 · Short-term memory（一个 chat session）

| 项 | 内容 |
|---|---|
| 所有者 | host-app session manager |
| 生命周期 | 同一 `(user_id, session_id)` 的所有连续对话 |
| 落盘 | JSON 文件（一个 session 一份文件） |
| 写入触发 | 每个 turn 结束后 `session.save_session_state(...)` |
| 读取触发 | 每个新 turn 开始前 `session.load_session_state(...)` |

**作用**：跨 turn 的"刚刚说过什么"。同一用户在同一会话窗口内的所有上下文。

**与 L1 的区别**：L1 是 turn 内瞬时缓冲；L2 是 turn 之间的持久 message history。

**与 L4 的区别**：L2 写文件位置由 host-app 控制（通常在用户 chats 目录下，跟会话一起删除）；
L4 是**跨 session** 的语义记忆库，长期保留、可搜索。

---

### 2.3 L3 · Mid-term memory（一个 agent / 一个 user）

| 项 | 内容 |
|---|---|
| 所有者 | host-app agent profile + `hubos.core.memory.MemoryService` |
| 生命周期 | 一个 agent 配置的整个生命期 / 一个 user 的所有 session |
| 落盘 | Markdown / JSON（per-agent 偏好文件 + per-user 跨 session 摘要） |
| 写入触发 | session 结束时（end_session hook）或显式 `update_agent_profile(...)` |
| 读取触发 | agent 启动时加载 persona；GM agent 切换 persona 时刷新 |

**作用**：
- "这个 agent 应该记住关于这个 user 的什么"——长期偏好、口味、限制、专长。
- "这个 agent 自身的稳定状态"——技能开关、skill 配置、MCP 注册表。

**与 L2 的区别**：L2 跟某个具体 chat 绑定；L3 跨 chat（"同一 agent 的所有 chat 都共享这份偏好"）。

**与 L4 的区别**：L3 是结构化的、agent 自己直接写的、容量小（几十 KB）；
L4 是流水式的、由摘要器/检索器加工生成的、容量可以很大（GB 级）。

> **当前状态**：L3 在 host-app 侧已有 agent profile 落盘 + `MemoryService` 接口占位，
> 但跨 session 摘要尚未接 L4 的 `DailySummaryGenerator`。Stage B sb-2 会把
> `recall_long_term` / `recall_session` 工具串起来。

---

### 2.4 L4 · Long-term semantic memory（跨 session / 跨 agent / 跨 user）

| 项 | 内容 |
|---|---|
| 所有者 | `hubos.core.memory.MemoryStore`（接口）+ 默认实现 `LocalMemoryStore` |
| 生命周期 | 永久（30 天后自动归档到 cold storage） |
| 落盘根 | `~/.hubos/memory/`（可被 `HUBOS_MEMORY_ROOT` env var 覆盖） |
| 写入触发 | session 结束时摘要落盘 / 手工 `append_message(...)` / 工具调用钩子 |
| 读取触发 | GM agent 主动调 `recall_long_term(query)`，跨 session 检索 |

**目录结构**：

```
~/.hubos/memory/
├── archives/YYYY-MM/{session_id}.json.gz   # 30 天后归档（gzip）
├── daily/YYYY-MM-DD.md                     # DailySummaryGenerator 产出
├── sessions/{session_id}/
│   ├── metadata.json                       # session 元数据（schema v1.0）
│   ├── messages.jsonl                      # append-only 消息流
│   ├── tools/{message_id}.json             # 工具调用详情
│   └── attachments/{attachment_id}.{ext}   # 二进制附件
├── schemas/                                # 元数据 / 工具调用 schema
└── index/
    ├── sessions_index.jsonl                # 列表 + 搜索索引
    └── daily_summaries.jsonl               # 每日摘要索引
```

**契约文件**：`hubos.core/memory/local_store/schemas/`

- `session_metadata.schema.json` (HubOS Memory Session Metadata v1.0)
- `tool_call.schema.json` (HubOS Memory Tool Call v1.0)

**Protocol（`hubos.core.memory.base`）**：

```python
@runtime_checkable
class MemoryStore(Protocol):
    # lifecycle
    def create_session(session_id, metadata) -> str: ...
    def end_session(session_id, ended_at, end_reason) -> None: ...
    def update_metadata(session_id, metadata) -> None: ...
    # writes
    def append_message(session_id, message) -> None: ...
    def save_tool_call(session_id, message_id, tool_call) -> None: ...
    # reads
    def load_session(session_id) -> Optional[dict]: ...
    def list_sessions(start_date=None, end_date=None) -> list[dict]: ...
    def search_sessions(query, fields=None) -> list[dict]: ...
    def search_messages(query, session_id=None) -> list[dict]: ...

@runtime_checkable
class ArchivableMemoryStore(MemoryStore, Protocol):
    def archive_session(session_id) -> None: ...
    def auto_archive() -> list[str]: ...

@runtime_checkable
class SummarizableMemoryStore(MemoryStore, Protocol):
    def save_daily_summary(date, summary) -> None: ...
    def get_daily_summary(date) -> Optional[str]: ...
    def append_daily_summary_index(record) -> None: ...
```

**能力探测**：调用方用 `isinstance(store, ArchivableMemoryStore)` 来判断是否暴露归档相关工具。
`LocalMemoryStore` 同时满足全部三个 Protocol。

**未来后端**（Stage C 起）：

- 远端 embedding 检索（语义召回，而不是当前的字串 substring）
- 真接 Hermes / 第三方 hosted memory（同一 Protocol 后端切换）

---

## 3. 数据流（写）

```
LLM turn 结束
  │
  ├─► L1 (in-memory)               立刻丢弃
  │
  ├─► L2 (host session manager)    同步落盘 JSON（一个 turn 一次 write）
  │
  ├─► L4 fire-and-forget:
  │     append_message(session_id, msg)              ◄── append-only JSONL
  │     save_tool_call(session_id, msg_id, payload)  ◄── 一个工具调用一个文件
  │
  └─► (session 结束时)
        L4: end_session(session_id, ended_at, reason)
        L4: DailySummaryGenerator.save(date)         ◄── 异步触发
        L3: 选择性把摘要里的"用户偏好/项目状态"回写 agent profile
```

**关键非阻塞约束**：上面所有箭头里**只有 L1→L2 是同步**（host-app 现有行为）。
L4 的写入由 GM agent 在 turn 结束后 fire-and-forget 触发，**绝不能阻塞下一个 user 输入**。

---

## 4. 数据流（读）

```
GM agent 收到新 user query
  │
  ├─► L2 自动读取（host-app session manager 已经做了）
  │     → 当前 chat 的 message history 进 prompt
  │
  ├─► L3 隐式生效
  │     → agent persona / 用户偏好已经在 system prompt 里
  │
  └─► L4 显式：仅在 GM 主动调用以下工具时才发生
        recall_long_term(query="...", limit=N)
        recall_session(session_id="sess_...")
        list_sessions(start_date=..., end_date=...)
```

**关键约定**：L4 读取**永远是显式 tool call 触发**，不会有"系统偷偷把跨 session 内容塞进 prompt"。
理由：
- prompt 注入面攻击防御（用户没法被一句"忽略你的记忆"操控隐性内容）
- token 预算可控（L4 摘要可能很长，模型不应被动承担）
- 可观测性（哪些 turn 真的用了 L4 都能从 tool_call log 看到）

---

## 5. 隔离与多租户

**作用域**：截至 sa5-2，`LocalMemoryStore` 把所有 session 平铺在 `sessions/`，
没有 per-tenant / per-user 的物理隔离。

**当前隔离手段**：

1. `metadata.json` 里记录 `user_id` / `tenant_id`（schema 字段已预留）
2. `list_sessions(...)` / `search_*` 由 caller 自己按 `user_id` 过滤
3. `HUBOS_MEMORY_ROOT` env var 可让不同部署/不同测试用不同根目录

**Stage C 才做的**：

- middleware 把 `tenant_context` 注入 `LocalMemoryStore` 调用，强制按租户分目录
- RBAC：管理员可以跨租户查，普通 user 只能看自己的
- audit log：每次 L4 读 / 写都进审计

不在 sa5-* 范围内——是 admin / multi-tenancy 主题，跟 S4 一起做。

---

## 6. 与 hubos.core Coordinator 的关系

`hubos.core.execution.ExecutionOrchestrator` / `Coordinator` 内部已经有 `MemoryContext` /
`MemoryUpdate` 的占位（`Coordinator.get_memory_context()` / `write_memory()`），
**目前是 no-op**（只 logging，不真写）。

Stage B sb-1/sb-2 会把这两个钩子接到 `MemoryStore`：

- `Coordinator.get_memory_context(...)` → 调 `store.search_messages(...)` 把相关历史塞进 plan
- `Coordinator.write_memory(...)` → 调 `store.append_message(...)` 把 task 输出落到 L4

这样 in-process Runtime 跑出来的每个 task，自动就成为 L4 的一份子，无须 GM 手动调 `append_message`。

---

## 7. 命名洁净度

`hubos.core/memory/` 下的所有源码 + schema 文件遵守以下命名约束：

- 不出现任何外部参考项目的名称
- 不出现 host-app 的项目名称
- 保留 `hubos.core`（用户接受为内部命名）
- 保留 `HubOS`（项目主名）

由 `scripts/test_local_memory_store.py::T8` 和 `test_memory_protocol.py::T6`
两个自动测试守住——黑名单字符串一旦回潮，CI 立刻红灯。

如新增 L4 后端实现，应继续遵守上述约束。

---

## 8. 已有验证脚本

| 脚本 | 检查内容 | 当前结果 |
|---|---|---|
| `scripts/test_local_memory_store.py` | L4 LocalMemoryStore 8 项 CRUD/归档/摘要/搜索/沙箱/命名 | 8/8 PASS |
| `scripts/test_memory_protocol.py` | MemoryStore Protocol 6 项契约/能力探测/拒绝坏实现 | 6/6 PASS |

未来扩展：

- sb-4：GM 端到端 → Coordinator → L4 写 + recall 召回（待写）
- Stage C：tenant_context 注入 + audit log（待写）

---

## 9. 变更日志

- **2026-04-20** sa5-1: `LocalMemoryStore` + `DailySummaryGenerator` 落地，路径根
  `~/.hubos/memory/`，零依赖纯标准库，8/8 测试通过。
- **2026-04-20** sa5-2: `MemoryStore` / `ArchivableMemoryStore` / `SummarizableMemoryStore`
  Protocol 抽出，runtime_checkable，6/6 测试通过。
- **2026-04-20** sa5-3: 本文（L1–L4 契约固化）。
