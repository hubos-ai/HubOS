# Changelog

## V1.2.0 — 知识管理与架构升级

### ✨ 新增功能

- **Knowledge Injection 统一注入层**：自动语义检索 `memory/knowledge/*.md`，五维评分（标题/实体/摘要/标签/置信度），按任务深度自动分配 token 预算（Light=0/Normal=300/Deep=600/参考历史=1000）
- **RunPolicy + AutoRouter 任务决策引擎**：LIGHT/NORMAL/DEEP 三级前置分类，简单问答跳过管线节省 2 次 LLM 调用，纯正则驱动零额外开销
- **工具输出归档（refs 系统）**：长工具输出外置到 `refs/{session_id}/`，三级自动清理（4h 活跃保护 + 7天过期 + 500MB 容量淘汰）
- **知识库每日维护**：`knowledge_pending/` 自动分类、敏感信息过滤、合并到正式知识库文件
- **经验卡片 Schema v4.1**：新增 `experience_type`（11 种类型白名单）+ `entities`（实体提取去重）字段
- **Cron 模型覆盖机制**：ContextVar 传递 model_override，cron 任务独立指定模型，不影响 default agent 日常对话
- **前端 StatusToolCard 组件**：状态卡前端渲染

### ⚡ 优化

- **Runner 架构重构**：Agent 池化复用（避免重复构建工具集）、三阶段前置管线标准化
- **ContextVar 上下文系统**：统一 session_id / agent_id / model_override 传递，子 Agent 写文件范围约束
- **Work Experience 追溯增强**：经验卡带 ref_session_id + ref_agent_id，支持前端跳回来源会话
- **reasoning_content 支持**：适配 DeepSeek thinking 块、GLM extended thinking

### 🐛 修复

- **内部状态卡协议修复**：从 `tool_use` 伪协议改为独立 `hubos_status` 协议，三层防火墙隔离，消除模型幻觉调用伪工具的根因
- **Cron 执行器 input 格式兼容**：`_normalize_runner_input()` 解决 legacy string 到 dict 格式兼容，旧 cron 任务恢复运行
- **cleanup_refs() 账目 bug**：删除成功才扣账，失败时 session 保留
- **ContextVar finally 清理**：跨任务 session_id 泄漏风险修复
- **GLM MCP 配置适配**：SSE → Streamable HTTP 协议迁移
