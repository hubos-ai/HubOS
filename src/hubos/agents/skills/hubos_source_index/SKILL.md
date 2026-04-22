---
name: hubos_source_index
description: "将用户问题中的主题、关键词映射到 HubOS 官方文档路径与常见源码入口，减少盲目搜索。适用于内置 QA Agent 在回答安装、配置、技能、MCP、多智能体、记忆、CLI 等问题时快速选定要读的文件。"
metadata:
  {
    "builtin_skill_version": "1.0",
    "hubos":
      {
        "emoji": "🗂️",
        "requires": {}
      }
  }
---

# HubOS 文档与源码速查

回答 **安装、配置、行为原理** 类问题时，先 **按关键词归类**，再按下表 **打开 1～2 个最可能命中的路径** 阅读，避免长时间无目的遍历。

## 使用步骤

1. 从用户问题中提取主题（对照下表左列或同类词）。
2. 解析 **`$HUBOS_ROOT`**：使用 `pip show hubos | grep Location` 获取安装路径，去掉末尾 `/src` 即为项目根目录。
3. **先读文档** `$HUBOS_ROOT/docs/<文件>.md`，仍不足再读表中 **源码入口**。

```bash
# 快速定位 HUBOS_ROOT
HUBOS_ROOT=$(pip show hubos 2>/dev/null | grep "Location:" | awk '{print $2}' | sed 's|/src$||')
echo "HUBOS_ROOT=$HUBOS_ROOT"
ls "$HUBOS_ROOT/docs/"
```

## 主题 / 关键词 → 优先文档与源码

| 主题或关键词（示例） | 优先文档（`$HUBOS_ROOT/docs/`） | 常见源码入口（相对 `$HUBOS_ROOT`） |
|---------------------|--------------------------------|-----------------------------------|
| 安装、依赖、首次使用 | `README.md`（项目根） | `src/hubos/cli/`、`pyproject.toml` |
| 配置、环境变量 | — | `src/hubos/config/config.py`、`src/hubos/constant.py` |
| 技能、SKILL、skill_pool | `SKILLS_UI_AND_PERMISSION_MODEL.md` | `src/hubos/agents/skills_manager.py`、`src/hubos/agents/skills/` |
| MCP、插件 | — | `src/hubos/app/routers/`（grep `mcp`） |
| 多智能体、工作区、agent | `architecture-session-isolation.md` | `src/hubos/app/routers/agents.py`、`src/hubos/app/migration.py` |
| 记忆、MEMORY、memory_search | `architecture-memory-layers.md` | `src/hubos/agents/tools/memory_recall.py`、`src/hubos/core/memory/` |
| 状态机、任务状态 | `architecture-state-machines.md` | `src/hubos/core/` |
| 会话隔离、多用户 | `architecture-session-isolation.md` | `src/hubos/app/routers/admin_sessions.py` |
| 控制台、前端 | — | `console/src/` |
| 命令行、子命令 | — | `src/hubos/cli/` |
| 频道、接入 | — | `src/hubos/app/routers/` grep `channel` |
| 模型、API Key、LLM | — | `src/hubos/config/config.py` |
| 安全、权限 | `SECURITY.md`（项目根） | `src/hubos/security/` |
| 桌面客户端 | — | `desktop/main.js`、`desktop/package.json` |
| 报错、常见问题 | — | 先 grep 错误信息，再看 `src/hubos/app/` 相关路由 |
| API 兼容性 | `COPAW_API_COMPATIBILITY_MATRIX.md` | `src/hubos/app/routers/` |
| Sessions API、产品规划 | `HUBOS_SESSIONS_PRODUCT_AND_API_PLAN.md` | `src/hubos/app/routers/admin_sessions.py` |

## 约定

- 文档路径：`$HUBOS_ROOT/docs/<文件>.md`
- 表中 **源码入口** 为起点；用 `read_file` 或 `grep` 缩小到具体符号，不要通读整个目录。

## 注意

- 本 skill **不替代** `read_file`：锁定候选路径后应立即读取并核对。
- 若某路径在本地不存在（例如未带源码的安装树），以 **已安装的文档包** 或用户提供的根目录为准，并明确告知依据路径。
