# Sessions 页面架构决策

> **日期**: 2026-04-19
> **状态**: DECISION_REQUIRED
> **影响页面**: Control/Sessions (`/sessions`)

---

## 1. HubOS Sessions 的产品语义

**路径**: `/sessions`，属于 Control 分组

**产品定位**: 历史聊天会话管理界面，面向运维/管理人员。

**核心功能**:
- 列表展示所有历史聊天会话（按 `updated_at` 降序）
- 按 `user_id` 和 `channel` 过滤会话
- 编辑会话名称（只改 name）
- 删除单个会话、批量删除会话
- 列表字段: ID, Name, SessionID, UserID, Channel, CreatedAt, UpdatedAt

**用户意图**: 用户登录 Sessions 页面，期望看到**过去曾经发生过的聊天会话记录**，并进行管理操作（重命名、删除）。这是**离线/历史视角**，不是活跃对话视角。

**与 Chat 页面的关系**: Chat 页面是**活跃对话界面**（发送消息、查看当前对话）；Sessions 是**历史管理界面**（查看/管理过去的会话）。

---

## 2. XClaw `/api/chats` 的产品语义

**路径**: `/api/chats/*`，定义于 `XClaw/backend/app/gateway/routers/chat_hubos.py`

**产品定位**: LangGraph thread 的实时管理接口，支撑活跃对话基础设施。

**核心语义**:
- `GET /chats` — 列出所有活跃 thread（不限量，无时间过滤）
- `POST /chats` — 创建一个新 thread（用于开始新对话）
- `GET /chats/{id}` — 获取 thread 的当前状态和消息历史
- `PUT /chats/{id}` — 更新 thread 元数据（如 name、status）
- `DELETE /chats/{id}` — 删除 thread
- `POST /chats/batch-delete` — 批量删除

**底层实现**: 每个 chat 对应一个 LangGraph `thread`，thread 有完整的状态（messages、artifacts、todos）。这是**运行时数据结构**，不是历史存档。

**用户意图**: XClaw 的 chat 是"我和 AI 的一个活跃工作会话"，thread 存在于对话进行期间，对话结束后 thread 是否继续存在取决于 LangGraph 的 retention 策略。

---

## 3. 为什么这不是普通 adapter 问题

普通 adapter 问题分为两类：

| 类型 | 特征 | 解决方案 |
|------|------|---------|
| **PATH_ADAPTER_ENOUGH** | 路径不同，形状兼容 | 只改路径 |
| **SHAPE_ADAPTER_ENOUGH** | 路径兼容，形状不同 | 转换请求/响应结构 |

Sessions 的问题不属于以上任何一类：

```
普通 adapter 问题 → 路径或形状的 syntax mismatch
Sessions 问题 → product semantics 的 mismatch
```

**根本原因**: HubOS Sessions 假设"会话是可以脱离活跃对话而独立存在的历史记录对象"，而 XClaw `/chats` 没有任何"历史会话"的抽象——它只有"活跃 thread"。

**具体差异**:

| 维度 | HubOS Sessions | XClaw /chats |
|------|---------------|--------------|
| 数据生命周期 | 离线历史存档 | 运行时活跃状态 |
| 过期/归档策略 | 无（用户主动管理） | 依赖 LangGraph retention |
| 列表语义 | "过去的会话记录" | "当前活跃的 threads" |
| thread 终止后 | 数据保留，可继续管理 | thread 状态不确定 |
| channel/user_id 过滤 | 会话隔离维度 | thread 元数据字段 |
| 更新操作 | 只改 name/meta | 可改 name/status |

这不是改一行路径映射或加一个字段转换能解决的语义鸿沟。

---

## 4. 如果强行接入，会误导用户什么

强行接入（不改变 HubOS 前端，只写 adapter）会产生以下误导：

### 误导 1: 用户以为在管理历史记录，实际在操作活跃 threads

用户看到一条"会话"，点了 Delete，该 thread 会被 LangGraph 删除。这意味着**正在进行的对话会被强制终止**，而用户以为只是删除了历史记录。

### 误导 2: 列表内容与用户预期不符

用户期望看到"所有曾经发生过的聊天会话"，但 XClaw 只返回**当前存在的 threads**。如果 LangGraph 的 checkpointer 或 retention 配置在对话不活跃后清理 thread，用户甚至看不到"历史"。

### 误导 3: 过滤功能暗示了不存在的语义

`filterUserId` 和 `filterChannel` 过滤器让用户以为系统有会话隔离和历史追踪能力。但 XClaw 的 channel/user_id 只是 thread metadata 中的字符串字段，不是真正的多租户或会话隔离机制。

### 误导 4: UpdatedAt 排序不反映"最后活跃时间"

HubOS Sessions 按 `updated_at` 降序排列，期望看到最近更新的会话。但 XClaw 的 `updated_at` 是 thread 的 metadata 更新时间，在 thread 长时间不活跃时，XClaw 不会主动更新这个字段，导致排序语义错乱。

### 误导 5: 批量删除是危险操作

在 XClaw 语境下，批量删除 threads 可能是灾难性的（删掉多个活跃工作上下文），而 HubOS 用户以为这只是"清理历史记录"。

---

## 5. 三个选项对比

### Option A: 暂不接入 Sessions，保留为未接通页

**做法**: 不写 adapter，不修改任何代码，Sessions 页面保持"未接通"状态。

**优点**:
- 不产生任何语义误导
- 不消耗开发资源在错误的方向上
- 未来 XClaw 有真正 history/session capability 时可以直接接入

**缺点**:
- HubOS 用户完全无法使用 Sessions 页面
- Control 分组中有一个页面无功能

---

### Option B: 接入但明确标注为"active threads only"

**做法**: 写 adapter 接入 XClaw `/chats`，在 Sessions 页面标题处加 banner 说明"当前显示为 XClaw 活跃 threads，非历史会话"。

**优点**:
- 部分功能可用（列表、过滤、编辑名称）
- 用户能感知到数据来源的不同

**缺点**:
- **误导无法通过 banner 消除**: 用户仍然会误用删除功能（以为删历史，实际删活跃 thread）
- banner 只能降低误解概率，不能消除语义错位
- 即使加了 banner，用户仍然可能忘记查看，在活跃 thread 上操作导致事故
- 技术上接入后，deleteSession/batchDeleteSessions 会对活跃 thread 生效，而这是最危险的操作

---

### Option C: 后端新增真正的 session/history capability 后再接

**做法**: XClaw backend 层面新增会话历史管理能力（持久化会话存档、独立的会话对象），HubOS 再接入。

**优点**:
- 从根本上解决问题
- 符合 HubOS 的产品语义

**缺点**:
- 需要 XClaw 后端开发，属于较大的功能新增
- 时间不可控，取决于 XClaw roadmap

---

## 6. 推荐结论

**推荐: Option A — 暂不接入，保留为未接通页**

### 原因

1. **语义错位的风险高于功能缺失的损失**: Sessions 页面的核心价值是"历史会话管理"，接入 XClaw 后这个价值完全无法实现，而误操作（删除活跃 thread）的后果比功能不可用更严重。

2. **误导无法通过文案完全消除**: Option B 的 banner 方案只能降低误解概率，但无法防止用户在实际使用中因语义混淆导致的误操作。这是产品设计层面的问题，不是技术文案能解决的。

3. **Sessions 不是 HubOS 的核心功能**: 根据 STAGE1_UI_ACCEPTANCE_REPORT，已接通的 5 个页面（CronJobs、Agents、Channels、Environments、Models）覆盖了 HubOS 的核心管控功能。Sessions 是辅助性管理界面，暂不接通不影响整体产品价值。

4. **Option C 是正确的长期方向，但需要等待后端能力**: 如果未来 XClaw 有真正的会话历史/存档系统，HubOS 应该按 Option C 接入。在此之前，不应该用"活跃 threads"去强行模拟"历史会话"。

5. **开发资源的合理分配**: 在已接通的 5 个页面上继续完善（验收、修复 honest partial）比在一个语义不匹配的页面上消耗资源更有价值。

### 后续建议

- 在 HubOS 的 roadmap 文档中标注 Sessions 页面的依赖：需要 XClaw 提供**独立的会话历史管理能力**（非 LangGraph threads）
- 如有条件，建议向 XClaw 后端团队提出 session/history 能力的 product requirement
- 在 STAGE1_UI_ACCEPTANCE_REPORT 中将 `/sessions` 标记为 `ARCHITECTURE_MISMATCH — NOT_RECOMMENDED_FOR_ADAPTER_ONLY`
