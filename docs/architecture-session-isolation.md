# 总经理 Agent 多 Session 隔离架构

> **S2 决策文档** · 定格 HubOS-WebUI 总经理 agent 的 session 隔离模型，说明为什么
> **不需要**新增 SessionManager / LRU / per-session agent 实例，以及目前已经具备的
> 能力、已知约束、未来扩展点。

---

## 1. 结论（给赶时间的人）

HubOS-WebUI 复用 HubOS 现成的 session 隔离机制，无需新增代码。模型是：

```
stateless agent (per-request 新建) + per-(user_id, session_id) JSON 落盘持久化
```

**不是** "长生命周期 agent 池 + SessionManager + LRU 驱逐"。后者更复杂，且无收益。

已由合约测试 `scripts/verify_session_isolation.py` 验证（4/4 通过，60 条并发 query
35.7 ms）。

## 2. 模型全貌

### 2.1 一次请求的生命周期

```
POST /chats/.../query   (FastAPI, src/hubos/app/runner/api.py)
   │
   ▼
AgentRunner.query_handler(msgs, request)   runner/runner.py:205
   │
   ├─ new HubOSAgent(...)                         # ★ 每次请求都 new，天然无共享
   │     runner/runner.py:294
   │
   ├─ await session.load_session_state(           # 从磁盘加载该 session 历史 memory
   │       session_id, user_id, agent=agent)
   │     runner/runner.py:360
   │
   ├─ async for msg, last in stream_printing_messages(agents=[agent], ...):
   │       yield msg, last                        # LLM 推理 + 工具调用 + 流式输出
   │
   └─ finally:
         await session.save_session_state(        # 落盘，下一轮能加载
             session_id, user_id, agent=agent)
            runner/runner.py:411-416
```

关键点：**agent 是短命对象**。LLM 推理结束后立即销毁，状态全部刷回磁盘。进程
重启、水平扩展、灰度发布都不丢数据。

### 2.2 文件命名规则

`SafeJSONSession._get_save_path` (`runner/session.py:56-69`):

```
save_dir/{sanitize(user_id)}_{sanitize(session_id)}.json
```

- Windows 非法字符 `\\ / : * ? " < > |` 被替换为 `--`（跨平台安全）
- `user_id` 为空时退化为 `{sanitize(session_id)}.json`（单用户场景）
- channel 前缀天然进入 session_id：`discord:dm:12345` → `discord--dm--12345`

### 2.3 Chat 元数据（给"管理员看所有 session"用）

与 memory 分离，由 `ChatManager` + `JSONChatRepository` 管理：

| 接口 | 文件 | 说明 |
|---|---|---|
| `list_chats(user_id=None, channel=None)` | `runner/manager.py:44` | 不带 filter → 所有 session；带 user_id → 某用户的 session |
| `get_chat(chat_id)` | `runner/manager.py:68` | 拿单条 chat 元数据 |
| `GET /chats` | `runner/api.py:64` | HTTP 映射：对应管理员列表视图 |
| `GET /chats/{chat_id}` | `runner/api.py:133` | HTTP 映射：加载任意 session 完整 messages |

**只要在上层加 RBAC（S4 要做），就是天然的管理员视图**。

## 3. 为什么不用"长生命周期 agent 池"

|  | stateless (当前)  | 长生命周期 agent 池 |
|---|---|---|
| 内存管理复杂度 | **零**（agent 用完即销毁） | 必须 LRU / TTL / 驱逐策略 |
| 进程重启 | **不丢数据**（磁盘即真源） | 丢失内存态，需 warm-up |
| 水平扩展 | **天然**（无内存态需共享） | 需粘性路由或内存态外移 |
| 同 session 并发 query | 串行语义（见 §4） | 天然支持 |
| 每 query 开销 | 加载 JSON state（~几 ms～几十 ms） | 零 |

对话级延迟（秒级）下几十 ms 的 IO 开销可忽略；换来的是架构极简和水平扩展天然支持。
**收益完全压过代价**，这就是我们选择的理由。

## 4. 已知约束：同一 session 内 query 必须串行

合约测试在 S2a 第一次迭代里意外发现：**`query_handler` 对同一 `(user_id,
session_id)` 的 load→modify→save 整个 RMW 循环没有加锁**。如果上层真的在同一个
chat 上并发发起 N 个 query，会出现"最后写者赢"的丢数据问题。

### 为什么这不是 bug

真实会话是 **turn-based** 的：用户发一条 → 等总经理回复完 → 发下一条。UI（前端
聊天框、Discord/TG 客户端）天然串行。60 条并发 query 在 **跨 session** 维度下
完全正确（见测试 4/4 通过）。

### 上层需要做什么

| 调用方 | 串行策略 |
|---|---|
| 前端 WebUI | 发送框在接收到 `last=true` 事件前禁用（已有） |
| IM channel (Discord/TG/…) | 客户端天然串行；服务端再用 `task_tracker` 兜底（`runner/task_tracker.py`） |
| 直接调 HTTP API 的第三方 | 自觉，或遇到"请稍候"错误（S3/S5 再加） |

### 如果哪天真需要同 session 并发安全

选项 A（推荐）：在 `AgentRunner.query_handler` 入口按 `session_id` 加 `asyncio.Lock`。

选项 B：用文件锁（`fcntl`）保护 load→save 窗口。

选项 C：切成 append-only event log（对标 solo-hub 的 event_store 模型）。

三个都容易加，不影响上层。**此刻不加**，避免过度工程。

## 5. 总经理 agent 对应的 profile

当前使用 `default` agent profile（`config/config.py:1267`）。S3 会做：

- [ ] 在 default profile 的 `PROFILE.md` 里写总经理人设（意图理解、任务拆分、派单、
  回收结果、自然语言回复）
- [ ] 加三个核心工具：`delegate_task` / `track_task` / `cancel_task`
- [ ] 接 HubOS-Runtime 的 `POST /v1/tasks` + SSE（S1 已就绪）

## 6. 并发隔离合约测试

**位置**：`HubOS/scripts/verify_session_isolation.py`

**不需要**装 hubos（重依赖），用 stdlib 精确复刻算法。

```
$ python3 scripts/verify_session_isolation.py

[1/4] 文件名 sanitize 边界用例
  ✓ 4 个 sanitize 用例全部通过（含 Windows 非法字符）
[2/4] 同 session 顺序 query 的 memory 累积性
  ✓ 同 session 10 次 query 正确累积到 memory
[3/4] 同 session_id 跨 user 的数据隔离
  ✓ 同一 session_id 的 alice / bob 数据完全隔离
[4/4] 多用户 × 多 session 并发隔离（主测试）
  · 12 个 session 并发（每 session 内 5 个 turn 顺序），共 60 条 query 耗时 35.7ms
  ✓ 4×3=12 个独立 session × 每对 5 条消息 = 60 条消息全部隔离正确
```

脚本里每个复刻函数都在 docstring 标注了 HubOS 原始源码行号，作为活文档使用。

## 7. 下一步

本文档冻结了 S2 的设计决策。后续：

- **S3**（下一步）：总经理 agent 加 `delegate_task/track_task/cancel_task` 工具
  + 写 `PROFILE.md` 人设
- **S4**：管理员 session 查看视图 —— 给 `GET /chats` / `GET /chats/{id}` 上 RBAC
  中间件，加 `/v1/admin/sessions` endpoint 和前端管理页
- **S5**：walking skeleton 端到端 —— WebUI 发消息 → 总经理 → Runtime → stub
  worker → 返回，打通全链路
