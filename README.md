# HubOS — 多用户 AI 员工管理平台

> **一句话**：把 AI 变成你的数字员工团队，接入你已有的沟通工具，自动完成从客户开发到财务报表的全部工作。

## 系统定位

HubOS 是一个**多用户、多渠道、多 Agent** 的 AI 控制平台。它不是一个聊天机器人，而是一个**数字员工管理系统**——每个 Agent 有自己的身份、技能、记忆和工作职责，通过统一的调度中枢协调完成复杂任务。

**核心特点：完全本地运行，数据不离开你的机器。**

---

## 系统架构概览

```
┌─────────────────────────────────────────────────────────┐
│                    用户接入层                              │
│  Web UI │ Electron 桌面 │ 飞书 │ 微信 │ 钉钉 │ 14+ 渠道    │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│               FastAPI 后端 (异步)                         │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  ┌────────┐  │
│  │ 认证/权限  │  │ 会话管理   │  │ 任务追踪  │  │ 文件锁  │  │
│  └──────────┘  └───────────┘  └──────────┘  └────────┘  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              Agent 调度层 (总经理)                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Agent 实例池 (LRU 缓存 + 并发安全)                  │   │
│  └──────────────────────────────────────────────────┘   │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐                   │
│  │并行委派   │ │流水线协作│ │后台任务   │  ← 三种任务模式   │
│  └─────────┘ └─────────┘ └─────────┘                   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              9 个专业 Agent (数字员工)                      │
│  销售│市场│调研│技术│财务│人力│客服│运营 ← 由总经理调度      │
│  每个 Agent 有: 独立身份/技能/记忆/模型配置                  │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│              基础设施层                                    │
│  ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌─────────────┐  │
│  │ 24 技能  │ │ LLM 路由  │ │ 记忆系统 │ │ Work Exp v4 │  │
│  └─────────┘ └──────────┘ └─────────┘ └─────────────┘  │
│  ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌─────────────┐  │
│  │ MCP 集成 │ │ 定时任务  │ │ 安全防护 │ │ DAG 引擎    │  │
│  └─────────┘ └──────────┘ └─────────┘ └─────────────┘  │
└─────────────────────────────────────────────────────────┘
```

---

## 核心特性

### 1. 多 Agent 团队协作

| 特性 | 说明 |
|------|------|
| **9 个专业 Agent** | 销售(Sam)、市场(Mavis)、调研(Iris)、技术(Rex)、财务(Felix)、人力(Harper)、客服(Clara)、运营(Oscar)、总经理(HubOS) |
| **人格化身份** | 每个 Agent 有 PROFILE.md（身份）、SOUL.md（价值观）、AGENTS.md（工作手册）|
| **智能调度** | 总经理根据任务类型自动路由到对应部门，支持并行、串行、DAG 三种协作模式 |
| **独立模型配置** | 复杂推理用 GLM-5.1，结构化任务用 MiniMax-M2.7-highspeed，按角色分配模型 |
| **Agent 实例池化** | LRU 缓存 + 并发借用保护，二次请求跳过 skill/MCP 注册，响应速度提升 ~200× |

### 2. 多渠道接入（14+）

| 渠道 | 类型 | 渠道 | 类型 |
|------|------|------|------|
| Web Console | 内置 | 飞书 (Feishu) | IM |
| 微信 (WeChat) | IM | 钉钉 (DingTalk) | IM |
| Discord | IM | Telegram | IM |
| 企业微信 (WeCom) | IM | QQ | IM |
| Matrix | IM | Mattermost | IM |
| iMessage | IM | MQTT | IoT |
| 小蚁 (XiaoYi) | 语音 | 语音频道 | 语音 |

所有渠道统一消息格式，同一个 Agent 可以同时服务多个渠道。

### 3. 三层记忆系统

| 层 | 加载方式 | 内容 | 用途 |
|----|---------|------|------|
| **长期记忆 (MEMORY.md)** | 每次自动加载 | 工具配置、API 经验、供应商清单 | 持久化知识 |
| **经验技巧 (Work Experience v4)** | 自动检索+注入 | 方法论卡片（一卡一流程）| 自我进化 |
| **每日笔记 (memory/)** | 按需检索 | 详细踩坑记录、完整会话上下文 | 原始日志 |

**Work Experience v4（自我进化引擎）**：
- 任务完成后自动反思 → 提取教训 → 合并去重到现有卡片
- LLM 语义匹配替代关键词匹配，准确度大幅提升
- 用户可在前端自选反思模型（GLM-5.1 / MiniMax）
- 种子卡片 + 自动晋升机制（candidate → approved → mature）

### 4. 24 个内置技能

| 类别 | 技能 |
|------|------|
| **文档处理** | PDF、Word (.docx)、Excel (.xlsx)、PPT (.pptx) |
| **网络搜索** | web_crawl (xcrawl)、tavily_search、super_crawler (深度 B2B) |
| **浏览器控制** | browser_cdp (CDP 调试)、browser_visible (可见浏览器) |
| **飞书全家桶** | feishu_bitable (多维表格)、feishu_doc (文档)、feishu_wiki (知识库)、feishu_drive (云盘) |
| **客户开发** | 电商平台比价、客户线索发现 (find_customer_leads) |
| **系统管理** | cron (定时任务)、channel_message (渠道消息)、multi_agent_collaboration |
| **其他** | 新闻聚合、邮件 (himalaya)、前端设计、文件读取、HubOS 安装指南 |

### 5. 内置工具（Agent 直接调用）

| 工具 | 功能 |
|------|------|
| `execute_shell_command` | Shell 命令执行 |
| `read_file / write_file / edit_file` | 文件读写（带并发安全文件锁） |
| `grep_search / glob_search` | 文件内容搜索 |
| `browser_use` | 浏览器自动化（Playwright） |
| `desktop_screenshot` | 桌面截图 |
| `delegate_task` | 后台任务委派 |
| `spawn_subagents` | 并行多 Agent 调度 |
| `coordinate_workflow` | 串行/DAG 工作流 |
| `find_customer_leads` | B2B 客户开发管线 |
| `memory_search / recall_long_term` | 记忆检索 |
| `get_current_time / get_token_usage` | 时间和用量查询 |
| `send_file_to_user` | 文件发送 |

### 6. 任务模式系统（Task Modes）

| 模式 | 配置项 | 说明 |
|------|--------|------|
| **并行委派** | max_concurrency, max_subagents, allow_nesting | 多个独立任务同时执行 |
| **流水线协作** | max_steps, step_timeout, allow_nesting | 有依赖的多步 DAG 工作流 |
| **后台任务** | timeout_seconds | 长时间运行的后台任务 |

每个 Agent 可以独立配置不同的任务模式参数。

### 7. 安全体系

| 特性 | 说明 |
|------|------|
| **文件写锁** | `fcntl.flock` per-file 粒度，30s 超时，防止多用户写入冲突 |
| **Agent 沙箱** | 每个 Agent 只能写自己的 workspace（除身份文件锁死）|
| **写白名单** | 可配置 Agent 写入外部目录（如 Rex → ~/HubOS/）|
| **工具防护 (Tool Guard)** | 按风险级别控制工具调用，支持人机协同审批 |
| **文件防护 (File Guard)** | 限制 Agent 可读写的文件路径 |
| **RBAC** | 基于角色的访问控制（admin/user/viewer）|
| **JWT 认证** | Web API 和 Socket 认证 |
| **Agent 工具权限注册表** | 每个 Agent 注册可用工具列表，越权调用被拒绝 |

### 8. 智能调度引擎

| 特性 | 说明 |
|------|------|
| **DAG 原生引擎** | 有向无环图任务编排，支持条件边、自适应并行 |
| **自适应并行度** | 根据任务特征自动调整并行度 |
| **执行器选择** | 智能选择最优执行器（LLM/代码/人工） |
| **策略学习** | 从历史执行数据中学习优化调度策略 |
| **Task Tracker** | 异步任务追踪，SSE 实时推送进度，支持断线重连 |

### 9. 记忆管理

| 特性 | 说明 |
|------|------|
| **上下文压缩** | 超长对话自动压缩，保留关键信息（独立高速模型 MiniMax-M2.7） |
| **200K 上下文** | GLM-5.1 支持 200K token 上下文窗口 |
| **Session 状态持久化** | per-session JSON 文件，线程安全 |
| **对话存储** | ChromaDB (SQLite) 向量化存储，支持语义搜索 |
| **ReMe 记忆** | 基于 reme-ai 的长期记忆管理 |
| **Memory Compaction** | 可配置的压缩阈值和摘要模型 |

### 10. MCP (Model Context Protocol) 集成

- 支持 MCP 客户端热重载
- 已接入智谱 MCP Server（视觉、搜索、webReader、ZRead）
- 可通过 Web UI 配置和管理 MCP 客户端
- 每个技能可以独立定义 MCP 依赖

### 11. 定时任务 (Cron)

- 支持标准 cron 表达式
- 多种推送目标（console、飞书、微信等）
- 任务启用/禁用/手动触发
- 时区感知调度

### 12. 技能市场 (Hub)

- 内置技能搜索和安装
- AI 辅助技能优化（流式输出）
- 技能池管理（导入/导出/广播）
- 内置技能源和自定义源

---

## 前端功能（28 个页面）

### 聊天
- 💬 智能对话（SSE 流式输出）
- 🤖 模型选择器（per-session 切换模型）
- 📋 会话管理（创建/删除/切换）
- 📎 文件上传和富文本展示

### Agent 管理
- ⚙️ Agent 配置（运行参数、记忆压缩、LLM 限速/重试）
- 🛠️ Agent 工具列表
- 📚 技能池（24 个技能管理）
- 🔌 MCP 客户端配置
- 📁 Workspace 文件编辑器

### 系统设置
- 👥 Agent 管理器（创建/编辑 Agent，模型选择）
- 🔑 模型供应商配置
- 🎯 Task Modes（工作流模式配置）
- 🔒 安全设置
- 🌐 环境变量管理
- 🎙️ 语音转写配置
- 📊 Token 用量统计

### 运维控制
- 📡 渠道管理（14+ 渠道配置）
- ⏰ 定时任务管理
- 💓 心跳监控
- 📋 会话列表（管理员视图）

### 经验系统
- 🧠 Work Experience 卡片管理
- 📈 卡片级别和统计

### 其他
- 🔐 登录/注册
- 🌍 四语国际化（中文/英文/日文/俄文）

---

## 后端架构

### 技术栈
- **语言**: Python 3.10-3.13
- **框架**: FastAPI + Uvicorn (异步)
- **Agent 框架**: AgentScope 1.0.18
- **向量存储**: ChromaDB (SQLite)
- **前端**: React 18 + Vite + TypeScript + Ant Design 5
- **桌面**: Electron

### API 规模
- **170 个 API 端点**
- **118,000+ 行 Python**
- **32,000+ 行 TypeScript**

### 核心目录结构
```
src/hubos/
├── app/                    # 应用层
│   ├── _app.py            # FastAPI 入口
│   ├── runner/             # Agent 运行器（含实例池化）
│   ├── routers/            # 22 个 API 路由模块
│   ├── channels/           # 14+ 渠道适配器
│   ├── mcp/                # MCP 客户端管理
│   └── workspace/          # Workspace 生命周期管理
├── agents/                 # Agent 核心
│   ├── react_agent.py     # HubOSAgent (ReAct 推理)
│   ├── tools/              # 18 个内置工具
│   ├── skills/             # 24 个技能定义
│   ├── hooks/              # Agent 生命周期钩子
│   └── memory/             # 记忆管理器
├── config/                 # 配置系统
│   └── config.py           # 45+ 配置类
├── core/                   # 核心引擎
│   ├── llm/                # LLM 提供商路由
│   ├── memory/             # 记忆存储层
│   ├── work_experience/    # 经验技巧系统 (v4)
│   ├── execution/          # 任务执行引擎
│   ├── orchestrator/       # 多 Agent 协调
│   ├── dag/                # DAG 调度引擎
│   ├── workflow/           # 工作流状态管理
│   ├── workers/            # Worker 提供商
│   └── infra/              # 基础设施（RBAC/FF/metrics）
└── cli/                    # CLI 工具集
```

### CLI 命令
```
hubos app        # 启动 FastAPI 服务
hubos desktop    # 启动 Electron 桌面应用
hubos init       # 初始化工作目录
hubos agent      # Agent 管理和通信
hubos cron       # 定时任务管理
hubos channel    # 渠道配置
hubos models     # 模型供应商配置
hubos skills     # 技能管理
hubos auth       # 认证管理
hubos daemon     # 守护进程管理
hubos shutdown   # 强制停止
hubos clean      # 清理工作目录
```

---

## 性能与并发

| 特性 | 实现 |
|------|------|
| **异步架构** | FastAPI + uvicorn，每个请求独立 asyncio.Task |
| **Agent 实例池化** | LRU 缓存，二次请求跳过 skill/MCP 注册，~200× 提速 |
| **文件写锁** | fcntl.flock per-file，防止多用户写入冲突 |
| **SSE 心跳** | 15 秒 ping，防止代理超时断开 |
| **上下文压缩** | 200K token 窗口，超出自动摘要压缩 |

---

## 部署

### 快速部署（Mac）
```bash
git clone https://github.com/allenzh0115/HubOS.git
cd HubOS
bash scripts/deploy_mac_private.sh
```

### 访问
- 本机：`http://localhost:8088`
- 局域网：`http://<Mac-IP>:8088`
- 桌面：`hubos desktop`

---

## 项目数据

| 指标 | 数值 |
|------|------|
| 代码行数 | 150,000+ (Python + TypeScript) |
| Git 提交 | 44 |
| Python 包 | 118,000+ 行 |
| TypeScript | 32,000+ 行 |
| API 端点 | 170 |
| 内置工具 | 18 |
| 技能 | 24 |
| 渠道 | 14+ |
| 前端页面 | 28 |
| 测试文件 | 2,448 |
| 国际化语言 | 4 (中/英/日/俄) |
| Agent 角色 | 9 |
