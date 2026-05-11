# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-05-10

### 🎯 核心新功能

#### 实时引导打断（Real-time Run Guidance）
任务执行过程中可以随时发送引导指令，中断当前行为并注入新方向 — 类似 Codex 的实时交互体验。

- 任务运行中在输入框输入内容 → 自动拦截为 Pending Guidance Card
- 三种操作：发送引导（注入新指令）/ 终止任务 / 取消
- DOM capture phase Enter 键拦截，同步读 ref，零延迟响应
- 后端 RunControl 模块：运行注册、状态追踪、引导注入、取消执行
- REST API：`GET /api/run-control/runs`、`POST cancel`、`POST guidance`
- SSE 流结束即时放行，任务完成后立即可输入，不等心跳轮询

#### 任务流程可视化（Task Workflow Visualization）
实时展示多 Agent 协作任务的全链路进度，每个子任务的创建、运行、完成状态一目了然。

- 后端 TaskMonitorStore：内存单例，asyncio.Queue 广播，实时进度推送
- SSE 端点 `GET /api/task-monitor/stream`：前端实时接收任务状态变更
- 独立 TaskMonitor 全屏页面 — 总览所有任务的执行状态
- Chat 内 ChatTaskPanel 侧边面板 — 对话中查看当前任务进度
- spawn_subagents / coordinate_workflow / delegate_task 自动接入监控
- 任务计划（TaskPlan）模块：计划生成、风险评估、自动执行

#### 上下文压缩优化
- 压缩模型独立配置：`compact_model_provider` + `compact_model_name`，不再回退主模型
- 细粒度计时日志：`check_context` / `compact_memory` / `stat_message` 各步骤计时
- `memory_compaction.py` AttributeError 修复

#### Work Experience v4 改进
- 经验卡片合并：45 → 29 → 40 张（清洗后）
- Retriever v4 prompt 优化：强调复用已有类型，大方向一致就匹配
- 反思引擎 prompt 重写：好/坏例子对比，数量上限 8 条，禁止模板格式和 dict 格式
- 反思结果清洗层：dict → 字符串自动转换，列表超长自动截断
- 经验卡片生成改为后台异步（fire-and-forget），不阻塞 Runner 返回

### Changed

- 前端 Chat 页面大幅重构：移除 runningBar、简化运行控制状态管理、清理调试日志
- `delegate_task` 超时读取 `agent.json` 的 `task_modes` 配置（max 语义，用户配的 1800s 生效）
- SSE 流结束即时放行：`runtimeLoadingRef` 在 stream done 时立即 false，不等心跳
- MCP 智谱客户端协议迁移：`sse` + `/sse` → `streamable_http` + `/mcp`
- MCP stdio 客户端命令路径改为绝对路径（`/opt/homebrew/bin/npx`、`/opt/homebrew/bin/uvx`）
- 经验种子卡 `search-engine-fallback` 更新：web_search_prime 首选，browser_use 最后手段

### Fixed

- `memory_compaction.py` `get_compressed_summary` AttributeError：`getattr(memory, "_compressed_summary", "")`
- `command_handler.py` 压缩摘要读取同步修复
- `retriever_v4.py` 语法错误修复
- ChatTaskPanel 不显示任务：移除 session_id 过滤
- TaskMonitor + ChatTaskPanel 深色模式 CSS 修复
- Pending Guidance Card 定位从 scroll area 移到外层，absolute 定位在输入框正上方
- 过时测试文件 `test_runner_work_experience.py` 清理（引用的函数在 v4 中已移除）

### Removed

- `runningBar` UI 及相关 CSS
- `composerText` state 和 textarea 同步 useEffect
- `stagePendingGuidanceFromComposer` 函数和 `guidanceStagerRef`
- `handleBeforeSubmit` 和旧 `beforeSubmit` 闭包
- 旧版 `hasControllableRun` 心跳 useEffect（重复轮询）
- 所有调试 `console.log`（`[heartbeat]`、`[intercept]`、`[bridge]`、`[guidance]`）

## [0.1.0] - 2026-05-07

### Added

#### Core Platform
- Multi-agent architecture with 9 specialized agents (sales, marketing, research, R&D, finance, HR, customer success, operations, GM)
- FastAPI async backend with 170+ API endpoints
- React 18 + TypeScript + Ant Design 5 frontend (28 pages)
- Electron desktop application
- 14+ channel integrations (Feishu, WeChat, DingTalk, Discord, Telegram, QQ, WeCom, Matrix, Mattermost, iMessage, MQTT, voice, XiaoYi, console)
- 4-language internationalization (English, Chinese, Japanese, Russian)
- JWT authentication with RBAC (admin/user/viewer roles)

#### Agent System
- HubOSAgent with ReAct reasoning loop
- Agent instance pooling with LRU cache and concurrency-safe borrowing
- Per-agent model configuration (GLM-5.1 for complex reasoning, MiniMax for speed)
- Agent sandbox with write whitelists and identity file protection
- Three task modes: parallel delegation, pipeline coordination, background tasks

#### Memory & Self-Evolution
- Three-layer memory system (long-term MEMORY.md, Work Experience v4, daily notes)
- Work Experience v4: LLM-powered semantic matching, auto-reflection on task completion
- Context compression with independent high-speed model (200K token window)
- ChromaDB vector storage for semantic session search

#### Tools & Skills
- 18 built-in tools (shell, file I/O, browser automation, memory search, customer development pipeline, etc.)
- 24 skills (PDF/Word/Excel/PPT processing, web crawling, email, cron, Feishu integration, etc.)
- Skill marketplace with AI-assisted optimization
- MCP (Model Context Protocol) client integration with hot-reload

#### Security
- File-level locking (fcntl.flock) for concurrent write safety
- Tool guard with risk-level policies and human-in-the-loop approval
- File guard for path-based access control
- Agent tool permission registry

#### Infrastructure
- DAG orchestration engine with adaptive parallelism
- Task tracker with SSE real-time progress and reconnect support
- Cron scheduler with timezone awareness
- Heartbeat system for periodic health checks
- Docker support with multi-arch builds (amd64/arm64)

#### Extensibility
- Extensible tool and skill framework for custom business integrations
- MCP (Model Context Protocol) client support with hot-reload

#### LLM Providers
- Multi-provider routing (Zhipu GLM, MiniMax, OpenAI, Anthropic, Google Gemini, DeepSeek, Qwen, etc.)
- Per-agent model selection
- Streaming SSE output with heartbeat keepalive
- Automatic fallback and retry logic
