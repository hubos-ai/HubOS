# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

#### Customer Development
- Multi-phase B2B lead discovery pipeline
- Government procurement intelligence (EduBid methodology)
- Brazilian Compras.gov.br API integration
- Email discovery and verification
- Feishu Bitable CRM with 95 customers across 7 countries

#### LLM Providers
- Multi-provider routing (Zhipu GLM, MiniMax, OpenAI, Anthropic, Google Gemini, DeepSeek, Qwen, etc.)
- Per-agent model selection
- Streaming SSE output with heartbeat keepalive
- Automatic fallback and retry logic
