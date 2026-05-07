# HubOS 更新日志

## v0.1.0 (2026-04)

### 🚀 新功能

- **多频道接入**：支持微信、钉钉、飞书、Discord、Telegram、QQ、iMessage 等主流渠道同时接入
- **多 Agent 协作**：基于 L1–L4 分层记忆架构，支持 spawn_subagents 调度子 Agent 协同完成复杂任务
- **Skill 系统**：内置技能库 + 运行时 skill_pool，支持文档读写、浏览器自动化、定时任务等 10+ 技能
- **MCP 集成**：支持接入任意 MCP（Model Context Protocol）服务，含 Tavily 搜索、MiniMax 多模态等
- **Admin 管理台**：可视化管理会话、Channels、Agent 配置、Skill 池、工具权限与安全策略
- **桌面客户端**：Electron 打包，支持 macOS 菜单栏自动启动与刷新/重启服务
- **多模态支持（MiniMax）**：文生图（image-01）、文生视频（Hailuo-2.3）、TTS HD、音乐生成

### 🔧 改进

- 前端全面适配深色/浅色主题，修复多处 Ant Design 主题变量覆盖问题
- Admin 会话页面 Session ID 列宽加宽，支持完整显示
- 侧边栏菜单间距优化，信息密度提升
- Header Logo 支持透明背景，深色/浅色模式自动切换

### 🔐 安全

- 文件预览接口添加路径遍历防护
- Skill 运行时 write_scope 限制 Agent 仅能写入工作目录
- 子 Agent 审计日志，记录 spawn_subagents 调用链

---

## 关于 HubOS

HubOS 是一个自托管的多 Agent 协作平台，支持多频道消息接入、技能扩展与 MCP 工具集成。

- **GitHub**：https://github.com/hubos-ai/HubOS
- **问题反馈**：https://github.com/hubos-ai/HubOS/issues
