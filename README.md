<p align="center">
  <img src="https://raw.githubusercontent.com/hubos-ai/HubOS/main/assets/logo.png" alt="HubOS Logo" width="400" />
</p>

<h1 align="center">HubOS</h1>

<p align="center">
  <strong>Multi-User AI Employee Management Platform</strong><br>
  <strong>多用户 AI 员工管理平台</strong>
</p>

<p align="center">
  <a href="https://github.com/hubos-ai/HubOS/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License" /></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-green.svg" alt="Python" /></a>
  <a href="https://www.typescriptlang.org/"><img src="https://img.shields.io/badge/TypeScript-5.x-blue.svg" alt="TypeScript" /></a>
  <img src="https://img.shields.io/badge/Agents-9-orange" alt="9 Agents" />
  <img src="https://img.shields.io/badge/Skills-24-purple" alt="24 Skills" />
  <img src="https://img.shields.io/badge/Channels-14+-teal" alt="14+ Channels" />
</p>

<p align="center">
  <a href="#english">English</a> · <a href="#中文">中文</a> · <a href="https://github.com/hubos-ai/HubOS/blob/main/CONTRIBUTING.md">Contributing</a> · <a href="https://github.com/hubos-ai/HubOS/issues">Issues</a>
</p>

---

<a id="english"></a>

## 🌟 What is HubOS?

HubOS is an **open-source, self-hosted AI employee management platform**. It transforms large language models into a team of specialized digital employees that communicate through your existing messaging tools.

**It's not a chatbot — it's an AI workforce.**

Each agent has its own **identity, skills, memory, and job responsibilities**, coordinated by a central dispatcher (General Manager agent). You talk to them through Feishu, WeChat, Discord, or any of 14+ supported channels.

### Why HubOS?

| Problem | HubOS Solution |
|---------|---------------|
| ChatGPT/Claude are single-agent, general-purpose | 9 specialized agents with division of labor |
| SaaS AI tools store your data in the cloud | Everything runs locally, data never leaves your machine |
| One chatbot can't handle complex business workflows | Multi-agent orchestration with parallel/pipeline/DAG modes |
| AI doesn't learn from experience | Self-evolving memory system extracts lessons automatically |
| Every AI tool is a separate subscription | One platform, 24 built-in skills, 18 tools |

### Key Numbers

| Metric | Value |
|--------|-------|
| Code | 150,000+ lines (Python + TypeScript) |
| API Endpoints | 170+ |
| Built-in Tools | 18 |
| Skills | 24 |
| Messaging Channels | 14+ |
| Agent Roles | 9 |
| Frontend Pages | 28 |
| Languages | 4 (EN / ZH / JA / RU) |

---

## ✨ Features

### 🔥 What Makes HubOS Different

Most AI platforms are single-agent chatbots. HubOS is fundamentally different:

| Feature | Description |
|---------|-------------|
| **Multi-Task Concurrency** | Multiple users can chat simultaneously — each request spawns an independent agent session via FastAPI async. No queuing, no blocking. |
| **Agent Instance Pooling** | LRU cache reuses initialized agent instances across requests. Second request for the same agent is ~200× faster (no skill/MCP re-registration). |
| **Sub-Agent Spawning** | Any agent can independently spawn child agents (`spawn_subagents`) for parallel work — and those children can spawn their own children (up to configurable depth). |
| **DAG Orchestration** | Complex multi-step workflows with dependencies: step B uses step A's output, step C+D run in parallel after B. Full pipeline coordination. |
| **Self-Evolving Memory** | After each task, the system automatically reflects on what happened, extracts lessons, and merges them into reusable methodology cards. The more you use it, the smarter it gets. |
| **Multi-User Isolation** | Each user gets independent workspaces, sessions, and memory. File locks (`fcntl.flock`) prevent write conflicts. Agent sandboxing prevents cross-user access. |
| **Hot-Swappable Models** | Per-agent model configuration — assign GPT-4o for complex reasoning, Claude for writing, Gemini for multimodal tasks, or local Ollama models for privacy. Switch models without restarting. |
| **Channel Multiplexing** | One agent team serves all channels simultaneously. A message on WeChat and a message on Discord go to the same agent with the same memory. |

### 🤖 Multi-Agent Team

9 specialized agents, each with its own personality, skills, and model configuration:

| Agent | Department | Role |
|-------|-----------|------|
| **HubOS** | General Manager | Task dispatch, coordination, decision-making |
| **Sam** | Sales | Lead generation, outreach, quotations |
| **Mavis** | Marketing | Content creation, competitive analysis, branding |
| **Iris** | Research | Market research, data analysis, industry reports |
| **Rex** | R&D | Development, system maintenance, automation |
| **Felix** | Finance | Billing, financial reports, cost analysis |
| **Harper** | HR | Documentation, scheduling, process management |
| **Clara** | Customer Success | Support, feedback handling, FAQ maintenance |
| **Oscar** | Operations | Task tracking, scheduled jobs, monitoring |

> 💡 **Fully Customizable**: The 9 built-in agents are just a starting point. You can create any number of agents with custom roles, personalities, skills, and model configurations to match your specific business needs — from a solo assistant to a 50-person digital company.

**Three orchestration modes:**
- **Parallel Delegation** (`spawn_subagents`) — Independent tasks run simultaneously
- **Pipeline Coordination** (`coordinate_workflow`) — Sequential DAG with dependencies
- **Background Tasks** (`delegate_task`) — Long-running jobs with progress tracking

### 📡 14+ Messaging Channels

All channels use a unified message format. One agent serves multiple channels simultaneously:

**Console** (built-in web UI) · **Feishu** · **WeChat** · **DingTalk** · **Discord** · **Telegram** · **WeCom** · **QQ** · **Matrix** · **Mattermost** · **iMessage** · **MQTT** · **XiaoYi** · **Voice**

### 🧠 Three-Layer Memory System

| Layer | Loading | Purpose |
|-------|---------|---------|
| **Long-term Memory** (MEMORY.md) | Auto-loaded every session | Persistent knowledge, tool configs, lessons learned |
| **Work Experience v4** | Auto-retrieved by task type | Methodology cards — one card per workflow |
| **Daily Notes** (memory/) | On-demand search | Detailed session logs, troubleshooting records |

**Work Experience v4 — Self-Evolving Engine:**
- Automatically reflects after task completion → extracts lessons → merges into cards
- LLM semantic matching replaces keyword matching for higher accuracy
- Promotion pipeline: candidate → approved → mature
- Users choose their own reflection model via the UI

### 🛠️ 24 Built-in Skills

| Category | Skills |
|----------|--------|
| **Document Processing** | PDF · Word (.docx) · Excel (.xlsx) · PowerPoint (.pptx) |
| **Web & Search** | Web crawling · Tavily search · Browser automation |
| **Communication** | Channel messaging · Email (himalaya) · Multi-agent collaboration |
| **Business** | E-commerce price search · News aggregation · Cron scheduling |
| **System** | HubOS setup guide · Frontend design · File reading |
| **Platform Integration** | Feishu (Bitable/Doc/Wiki/Drive) · DingTalk channel setup |

### 🔒 Security

| Feature | Description |
|---------|-------------|
| **File Locking** | `fcntl.flock` per-file granularity, 30s timeout — prevents write conflicts |
| **Agent Sandbox** | Each agent can only write to its own workspace |
| **Write Whitelist** | Configurable external directory access (e.g., R&D agent → project dir) |
| **Tool Guard** | Risk-level tool control with human-in-the-loop approval |
| **RBAC** | Role-based access control (admin / user / viewer) |
| **JWT Auth** | Web API and WebSocket authentication |

### ⚡ Performance

| Feature | Implementation |
|---------|---------------|
| **Async Architecture** | FastAPI + Uvicorn, each request as independent asyncio.Task |
| **Agent Instance Pooling** | LRU cache with concurrency-safe borrowing, ~200× speedup on repeat requests |
| **200K Context Window** | GLM-5.1 supports 200K tokens; auto-compression when exceeded |
| **SSE Heartbeat** | 15s ping intervals to prevent proxy timeouts |

---

## 🆕 What's New in v1.1.0

| Feature | Description |
|---------|-------------|
| 🎮 **Real-Time Run Guidance** | Interrupt and redirect agents mid-task — type guidance while they work and they pivot immediately |
| 📊 **Task Workflow Visualization** | Watch multi-agent tasks execute in real-time with a full-page monitor and chat-side panel |
| 🔧 **Status Messages** | Context understanding & experience matching now shown as 🔧 tool-call indicators before each response |
| ⌨️ **Slash Commands** | 24 built-in commands with EN/ZH labels, accessible via `/` in chat input |
| ⚡ **Faster Response** | WE card generation moved to background thread, context compression uses dedicated compact model |
| 🛡️ **Reliability** | Fixed async `await` bug, timeout floor guarantee, instant SSE release, friendly cancel prompts |

<details>
<summary><strong>📋 Full Changelog</strong></summary>

**New Modules**: RunControl API · TaskMonitor (backend + frontend) · TaskPlan (plan/autogen/executor/risk) · Slash Commands · UI Language

**Improvements**: Compact model config · WE async fire-and-forget · Retriever fuzzy matching · Reflection prompt rewrite · Timeout max() semantics · MCP Streamable HTTP · Absolute stdio paths · Desktop port-based restart

**Fixes**: memory_compaction AttributeError · memory.add() missing await · AGENT_ERROR on cancel · Dark mode text · delegate_task timeout override · SSE heartbeat blocking

</details>

---

## 📸 Screenshots

> *Coming soon — screenshots of the Web Console, Agent Management, and Chat interfaces.*

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10 - 3.13
- Node.js 18+ (for frontend build)
- At least one LLM API key (Zhipu GLM, MiniMax, OpenAI, Anthropic, etc.)

### From Source

```bash
# Clone
git clone https://github.com/hubos-ai/HubOS.git
cd HubOS

# Set up virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install Python dependencies
pip install -e .

# Build frontend
cd console
npm ci
npm run build
cd ..

# Initialize (creates ~/.hubos/ directory)
hubos init

# Start the server
hubos app
```

Open http://localhost:8088 and start chatting.

### First-Time Setup

1. Open http://localhost:8088
2. Register an account
3. Go to **Settings → Models** and configure your LLM provider (API key + model)
4. Go to **Settings → Agents** and verify agent configurations
5. Start chatting with your AI team!

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User Interface                        │
│  Web UI │ Electron Desktop │ Feishu │ WeChat │ ...      │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               FastAPI Backend (Async)                    │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌────────┐ │
│  │ Auth/RBAC │  │ Sessions  │  │ Tracker  │  │ Locks  │ │
│  └──────────┘  └───────────┘  └──────────┘  └────────┘ │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Agent Dispatch Layer (GM)                   │
│  ┌────────────────────────────────────────────────────┐ │
│  │ Agent Instance Pool (LRU + concurrency-safe)       │ │
│  └────────────────────────────────────────────────────┘ │
│  Parallel │ Pipeline │ Background   ← Task Modes       │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              9 Specialized Agents                        │
│  Sales │ Marketing │ Research │ R&D │ Finance │ ...     │
│  Each has: Identity + Skills + Memory + Model Config    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Infrastructure                              │
│  Skills │ LLM Router │ Memory │ WE v4 │ MCP │ Cron     │
│  Security │ DAG Engine │ Tool Guard │ File Guard        │
└─────────────────────────────────────────────────────────┘
```

### Directory Structure

```
src/hubos/
├── app/                    # Application layer
│   ├── runner/             # Agent runners (with instance pooling)
│   ├── routers/            # 22 API router modules
│   ├── channels/           # 14+ channel adapters
│   ├── mcp/                # MCP client management
│   └── workspace/          # Workspace lifecycle
├── agents/                 # Agent core
│   ├── react_agent.py     # HubOSAgent (ReAct reasoning)
│   ├── tools/              # 18 built-in tools
│   ├── skills/             # 24 skill definitions
│   ├── hooks/              # Agent lifecycle hooks
│   └── memory/             # Memory managers
├── config/                 # Configuration system (45+ classes)
├── core/                   # Core engine
│   ├── llm/                # LLM provider routing
│   ├── memory/             # Memory storage (ChromaDB)
│   ├── work_experience/    # Experience system (v4)
│   ├── execution/          # Task execution engine
│   ├── dag/                # DAG scheduling engine
│   └── infra/              # RBAC, feature flags, metrics
├── security/               # Tool guard, skill scanner
└── cli/                    # CLI commands

console/                    # React frontend (28 pages)
desktop/                    # Electron desktop app
```

### Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.10+ · FastAPI · Uvicorn |
| Agent Framework | AgentScope 1.0.18 |
| Frontend | React 18 · Vite · TypeScript · Ant Design 5 |
| Desktop | Electron |
| Vector Store | ChromaDB (SQLite backend) |
| UI Components | [@agentscope-ai/chat](https://www.npmjs.com/package/@agentscope-ai/chat) · [@agentscope-ai/design](https://www.npmjs.com/package/@agentscope-ai/design) |

---

## 🤝 Supported LLM Providers

HubOS supports multiple LLM providers with per-agent model configuration:

| Provider | Models | Best For |
|----------|--------|----------|
| **Zhipu (智谱)** | GLM-4, GLM-5.1 | Complex reasoning, long context (200K) |
| **MiniMax** | M2.7-highspeed | Fast structured tasks |
| **OpenAI** | GPT-4o, GPT-4-turbo | General purpose |
| **Anthropic** | Claude 3.5/4 | Analysis, writing |
| **Google** | Gemini 1.5/2.0 | Multimodal tasks |
| **DeepSeek** | DeepSeek-V3 | Cost-effective reasoning |
| **Qwen (通义千问)** | Qwen 2.5/3 | Chinese language tasks |
| **Ollama** | Local models | Privacy-first, no API needed |

---

## 🔌 MCP (Model Context Protocol)

HubOS supports MCP client integration with hot-reload:

- Configure MCP servers via Web UI or config files
- Each skill can define its own MCP dependencies
- Built-in support for Zhipu MCP Server (Vision, Search, webReader, ZRead)

---

## 📋 CLI Reference

```bash
hubos app          # Start FastAPI server
hubos init         # Initialize working directory
hubos agent        # Agent management & communication
hubos cron         # Cron job management
hubos channel      # Channel configuration
hubos models       # Model provider configuration
hubos skills       # Skill management
hubos auth         # Authentication management
hubos shutdown     # Stop all services
```

---

## 🗺️ Roadmap

- [ ] Plugin marketplace for community skills
- [ ] Multi-language agent templates (DE, FR, ES, AR)
- [ ] GPU-accelerated local model support (MLX, llama.cpp)
- [ ] Collaborative workspaces for teams
- [ ] Mobile companion app
- [ ] REST API playground / Swagger UI

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for:

- Development environment setup
- Code style guidelines
- PR submission process
- How to add new skills, channels, and tools

---

## 📄 License

HubOS is released under the [Apache License 2.0](LICENSE).

---

## 🙏 Acknowledgments

HubOS builds upon the following excellent open-source projects:

| Project | Usage | License |
|---------|-------|---------|
| [AgentScope](https://github.com/modelscope/agentscope) | Core agent framework, model routing, MCP integration | Apache-2.0 |
| [AgentScope Runtime](https://github.com/agentscope-ai/agentscope-runtime) | Frontend UI components (@agentscope-ai/chat, @agentscope-ai/design) | Apache-2.0 |
| [FastAPI](https://github.com/tiangolo/fastapi) | Async web framework | MIT |
| [React](https://github.com/facebook/react) | Frontend UI library | MIT |
| [Ant Design](https://github.com/ant-design/ant-design) | UI component library | MIT |
| [Playwright](https://github.com/microsoft/playwright) | Browser automation | Apache-2.0 |
| [ChromaDB](https://github.com/chroma-core/chroma) | Vector storage | Apache-2.0 |
| [ReMe](https://github.com/virtUOS/reme-ai) | Long-term memory management | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | ASGI server | BSD-3-Clause |
| [Electron](https://github.com/electron/electron) | Desktop application framework | MIT |

Special thanks to the open-source community and everyone who contributes to making AI more accessible.

---

<a id="中文"></a>

## 🌟 HubOS 是什么？

HubOS 是一个**开源、自托管的 AI 员工管理平台**。它将大语言模型转化为一支专业的数字员工团队，通过你已有的沟通工具与你协作。

**它不是聊天机器人 — 它是 AI 劳动力。**

每个 Agent 有自己的**身份、技能、记忆和工作职责**，由总经理 Agent 统一调度。你可以通过飞书、微信、钉钉等 14+ 渠道与团队沟通。

### 为什么需要 HubOS？

| 问题 | HubOS 的解决方案 |
|------|----------------|
| ChatGPT/Claude 是单 Agent、通用型 | 9 个专业 Agent，分工明确 |
| SaaS AI 工具把数据存在云端 | 完全本地运行，数据不离开你的机器 |
| 单个聊天机器人无法处理复杂业务流程 | 多 Agent 编排：并行/流水线/DAG 三种模式 |
| AI 不会从经验中学习 | 自进化记忆系统，自动提取教训 |
| 每个 AI 工具都是独立订阅 | 一个平台，24 个内置技能，18 个工具 |

---

## ✨ 核心特性

### 🔥 HubOS 的独特之处

大多数 AI 平台是单 Agent 聊天机器人。HubOS 从根本上不同：

| 特性 | 说明 |
|------|------|
| **多任务并发** | 多个用户可以同时对话 — 每个请求通过 FastAPI 异步机制创建独立的 Agent 会话。无需排队，互不阻塞。 |
| **Agent 实例池化** | LRU 缓存复用已初始化的 Agent 实例。同一 Agent 的第二次请求快 ~200 倍（无需重新注册 skill/MCP）。 |
| **子 Agent 派生** | 任何 Agent 都可以独立派生子 Agent（`spawn_subagents`）并行工作 — 子 Agent 还可以继续派生自己的子 Agent（深度可配置）。 |
| **DAG 工作流编排** | 复杂的多步骤依赖工作流：步骤 B 使用步骤 A 的输出，步骤 C+D 在 B 完成后并行执行。完整的流水线协调。 |
| **自进化记忆** | 每次任务完成后，系统自动反思、提取教训，合并为可复用的方法论卡片。越用越聪明。 |
| **多用户隔离** | 每个用户拥有独立的 workspace、会话和记忆。文件锁（`fcntl.flock`）防止写入冲突。Agent 沙箱防止跨用户访问。 |
| **模型热切换** | 按 Agent 配置模型 — 复杂推理用 GPT-4o，写作用 Claude，多模态用 Gemini，隐私场景用 Ollama 本地模型。用户自由选择供应商和模型，无需重启即可切换。 |
| **渠道多路复用** | 一个 Agent 团队同时服务所有渠道。微信和 Discord 上的消息访问同一个 Agent 和同一套记忆。 |

### 🤖 多 Agent 团队协作

9 个专业 Agent，各有自己的人格、技能和模型配置：

| Agent | 部门 | 职责 |
|-------|------|------|
| **HubOS** | 总经理 | 任务调度、协调、决策 |
| **Sam 张** | 销售 | 客户开发、报价、合同推进 |
| **Mavis 王** | 市场 | 内容创作、竞品分析、品牌传播 |
| **Iris 周** | 调研 | 市场调研、数据分析、行业报告 |
| **Rex 陈** | 技术 | 开发、系统维护、自动化 |
| **Felix 刘** | 财务 | 账单、财务报表、成本分析 |
| **Harper 赵** | 人力 | 文档管理、日程协调、流程规范 |
| **Clara 孙** | 客服 | 售后支持、反馈处理、FAQ 维护 |
| **Oscar 吴** | 运维 | 任务追踪、定时作业、监控 |

> 💡 **完全可定制**：内置的 9 个 Agent 只是起点。你可以创建任意数量的 Agent，自定义角色、人格、技能和模型配置，匹配你的业务需求 — 从单人助手到 50 人的数字公司都可以。

**三种协作模式：**
- **并行委派**（`spawn_subagents`）— 独立任务同时执行
- **流水线协作**（`coordinate_workflow`）— 有依赖的串行 DAG 工作流
- **后台任务**（`delegate_task`）— 长时间运行的任务，带进度追踪

### 📡 14+ 渠道接入

所有渠道统一消息格式，同一个 Agent 同时服务多个渠道：

**Web 控制台**（内置）· **飞书** · **微信** · **钉钉** · **Discord** · **Telegram** · **企业微信** · **QQ** · **Matrix** · **Mattermost** · **iMessage** · **MQTT** · **小蚁** · **语音**

### 🧠 三层记忆系统

| 层 | 加载方式 | 用途 |
|----|---------|------|
| **长期记忆**（MEMORY.md） | 每次自动加载 | 持久化知识、工具配置、经验教训 |
| **经验技巧**（Work Experience v4） | 按任务类型自动检索 | 方法论卡片 — 一卡一流程 |
| **每日笔记**（memory/） | 按需搜索 | 详细会话日志、踩坑记录 |

**Work Experience v4 — 自进化引擎：**
- 任务完成后自动反思 → 提取教训 → 合并去重到现有卡片
- LLM 语义匹配替代关键词匹配，准确度大幅提升
- 晋升管线：candidate → approved → mature
- 用户可在前端自选反思模型

### 🛠️ 24 个内置技能

| 类别 | 技能 |
|------|------|
| **文档处理** | PDF · Word · Excel · PowerPoint |
| **网络搜索** | 网页抓取 · Tavily 搜索 · 浏览器自动化 |
| **沟通协作** | 渠道消息 · 邮件 · 多 Agent 协作 |
| **业务工具** | 电商比价 · 新闻聚合 · 定时任务 |
| **系统管理** | 安装指南 · 前端设计 · 文件读取 |
| **平台集成** | 飞书（多维表格/文档/知识库/云盘）· 钉钉接入 |

### 🔒 安全体系

| 特性 | 说明 |
|------|------|
| **文件写锁** | `fcntl.flock` per-file 粒度，防止多用户写入冲突 |
| **Agent 沙箱** | 每个 Agent 只能写自己的 workspace |
| **工具防护** | 按风险级别控制工具调用，支持人机协同审批 |
| **RBAC** | 基于角色的访问控制（admin / user / viewer） |
| **JWT 认证** | Web API 和 WebSocket 认证 |

---

## 🆕 v1.1.0 更新

| 功能 | 说明 |
|------|------|
| 🎮 **实时引导打断** | 任务执行中随时发指令，Agent 立即转向 — 像和真人同事协作一样 |
| 📊 **任务流程可视化** | 全页任务监控 + 聊天内侧边面板，多 Agent 协作进度实时可见 |
| 🔧 **状态消息** | 上下文理解、经验匹配以 🔧 工具调用样式显示在每次响应前 |
| ⌨️ **快捷命令** | 24 个内置命令，中英双语，输入 `/` 即可呼出 |
| ⚡ **响应更快** | 经验卡片后台异步生成，上下文压缩使用独立轻量模型 |
| 🛡️ **稳定性提升** | 修复 async await 丢失、超时覆盖、取消报错等关键 bug |

<details>
<summary><strong>📋 完整更新日志</strong></summary>

**新模块**：运行控制 API · 任务监控（后端+前端）· 任务计划（生成/自动/执行/风控）· 快捷命令 · UI 语言模块

**改进**：压缩模型独立配置 · 经验卡片异步生成 · 检索器模糊匹配 · 反思 prompt 重写 · 超时 max() 语义 · MCP Streamable HTTP · stdio 绝对路径 · 桌面端按端口重启

**修复**：memory_compaction AttributeError · memory.add() 漏 await · 取消引导显示 AGENT_ERROR · 深色模式文字不可见 · delegate_task 超时覆盖 · SSE 心跳阻塞

</details>

---

## 🚀 快速开始

### 前提条件

- Python 3.10 - 3.13
- Node.js 18+（前端构建需要）
- 至少一个 LLM API key（智谱 GLM、MiniMax、OpenAI、Anthropic 等）

### 从源码安装

```bash
git clone https://github.com/hubos-ai/HubOS.git
cd HubOS

python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

pip install -e .

cd console
npm ci
npm run build
cd ..

hubos init
hubos app
```

打开 http://localhost:8088 开始使用。

### 首次配置

1. 打开 http://localhost:8088
2. 注册账号
3. 进入 **设置 → 模型**，配置 LLM 供应商（API key + 模型）
4. 进入 **设置 → Agent**，确认 Agent 配置
5. 开始和你的 AI 团队对话！

---

## 🤝 支持的 LLM 供应商

HubOS 支持多种 LLM 供应商，每个 Agent 可独立配置模型：

| 供应商 | 模型 | 适用场景 |
|--------|------|---------|
| **智谱（Zhipu）** | GLM-4, GLM-5.1 | 复杂推理、长上下文（200K） |
| **MiniMax** | M2.7-highspeed | 快速结构化任务 |
| **OpenAI** | GPT-4o, GPT-4-turbo | 通用场景 |
| **Anthropic** | Claude 3.5/4 | 分析、写作 |
| **Google** | Gemini 1.5/2.0 | 多模态任务 |
| **DeepSeek** | DeepSeek-V3 | 高性价比推理 |
| **通义千问** | Qwen 2.5/3 | 中文场景 |
| **Ollama** | 本地模型 | 隐私优先，无需 API |

---

## 🏗️ 系统架构

```
┌─────────────────────────────────────────────────────────┐
│                    用户接入层                              │
│  Web 控制台 │ 桌面应用 │ 飞书 │ 微信 │ 钉钉 │ 14+ 渠道    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               FastAPI 后端 (异步)                         │
│  认证/权限 │ 会话管理 │ 任务追踪 │ 文件锁                 │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Agent 调度层 (总经理)                         │
│  Agent 实例池 (LRU 缓存 + 并发安全)                       │
│  并行委派 │ 流水线协作 │ 后台任务                          │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              9 个专业 Agent (数字员工)                      │
│  每个 Agent: 身份 + 技能 + 记忆 + 模型配置                 │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              基础设施层                                    │
│  技能 │ LLM 路由 │ 记忆系统 │ 自进化 │ MCP │ 定时任务     │
│  安全防护 │ DAG 引擎 │ 工具防护 │ 文件防护                 │
└─────────────────────────────────────────────────────────┘
```

### 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.10+ · FastAPI · Uvicorn |
| Agent 框架 | AgentScope 1.0.18 |
| 前端 | React 18 · Vite · TypeScript · Ant Design 5 |
| 桌面端 | Electron |
| 向量存储 | ChromaDB (SQLite) |
| UI 组件 | [@agentscope-ai/chat](https://www.npmjs.com/package/@agentscope-ai/chat) · [@agentscope-ai/design](https://www.npmjs.com/package/@agentscope-ai/design) |

---

## 📄 许可证

HubOS 基于 [Apache License 2.0](LICENSE) 开源。

---

## 🙏 致谢

HubOS 基于以下优秀开源项目构建：

| 项目 | 用途 | 许可证 |
|------|------|--------|
| [AgentScope](https://github.com/modelscope/agentscope) | 核心 Agent 框架、模型路由、MCP 集成 | Apache-2.0 |
| [AgentScope Runtime](https://github.com/agentscope-ai/agentscope-runtime) | 前端 UI 组件 (@agentscope-ai/chat, @agentscape-ai/design) | Apache-2.0 |
| [FastAPI](https://github.com/tiangolo/fastapi) | 异步 Web 框架 | MIT |
| [React](https://github.com/facebook/react) | 前端 UI 库 | MIT |
| [Ant Design](https://github.com/ant-design/ant-design) | UI 组件库 | MIT |
| [Playwright](https://github.com/microsoft/playwright) | 浏览器自动化 | Apache-2.0 |
| [ChromaDB](https://github.com/chroma-core/chroma) | 向量存储 | Apache-2.0 |
| [ReMe](https://github.com/virtUOS/reme-ai) | 长期记忆管理 | MIT |
| [Uvicorn](https://github.com/encode/uvicorn) | ASGI 服务器 | BSD-3-Clause |
| [Electron](https://github.com/electron/electron) | 桌面应用框架 | MIT |

特别感谢开源社区的每一位贡献者，是你们让 AI 变得更加普惠。
