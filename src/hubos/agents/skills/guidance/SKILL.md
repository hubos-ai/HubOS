---
name: guidance
description: "回答用户关于 HubOS 安装与配置的问题：优先定位并阅读本地文档，再提炼答案；若本地信息不足，兜底访问官网文档。"
metadata:
  {
    "builtin_skill_version": "1.0",
    "hubos":
      {
        "emoji": "🧭",
        "requires": {}
      }
  }
---

# HubOS 安装与配置问答指南

当用户询问 **HubOS 的安装、初始化、环境配置、依赖要求、常见配置项** 时，使用本 skill。

核心原则：

- 先查本地文档，再回答
- 回答要基于已读到的内容，不臆测
- 回答语言与用户提问语言保持一致

## 标准流程


### 第一步：定位文档位置

**检查项目源码中的文档目录**

执行以下脚本确定 `$HUBOS_ROOT`，然后定位 `docs/` 目录：

```bash
# 获取 hubos 二进制绝对路径
HUBOS_BIN=$(which hubos 2>/dev/null || echo "")

# 推导项目根目录
# 安装方式 1：venv 安装  ~/.hubos/venv/bin/hubos → 上三级 = ~/.hubos 上一级（即项目根）
# 安装方式 2：pip editable  直接在项目 venv 里，用 pip show 更准
if [[ "$HUBOS_BIN" == *"/.hubos/venv/bin/hubos" ]]; then
    # editable install: hubos venv 就在项目旁
    HUBOS_ROOT=$(pip show hubos 2>/dev/null | grep "Location:" | awk '{print $2}' | sed 's|/src$||')
fi

# 兜底：尝试从 pip show 直接获取
if [ -z "$HUBOS_ROOT" ] || [ ! -d "$HUBOS_ROOT" ]; then
    HUBOS_ROOT=$(pip show hubos 2>/dev/null | grep "Location:" | awk '{print $2}' | sed 's|/src$||')
fi

echo "Detected HubOS Root: $HUBOS_ROOT"

# 标准文档路径
DOC_DIR="$HUBOS_ROOT/docs"

# 检查文档目录并列出
if [ -d "$DOC_DIR" ]; then
    find "$DOC_DIR" -type f -name "*.md" | head -n 50
else
    echo "docs/ not found at $DOC_DIR"
fi
```

**如果项目文档不存在，查找 README**

```bash
# README 兜底
find "$HUBOS_ROOT" -maxdepth 2 -name "README.md" | head -3
```

如果找到了文档目录，请你记录在 memory 中，格式为：

```markdown
# 文档目录
DOC_DIR = <doc_path>
```

### 第二步：文档检索与匹配

文档文件命名格式为 `<topic>.<lang>.md`（如 `config.zh.md`、`config.en.md`、`quickstart.zh.md`）。

使用 find 命令在目标目录中列出所有符合后缀的文档，并根据文件名关键字（如 install, env, setup）锁定目标作为 <doc_path>。

```bash
# 列出所有符合后缀的文档
find $DOC_DIR -type f -name "*.md"
```

如果没有合适的文档，则在下一步阅读所有文档内容。


### 第三步：阅读文档内容

找到候选文档后，读取并确认与问题相关的段落。可使用：

- `cat <doc_path>`
- `file_reader` skill（推荐用于更长文档或分段读取）

如果文档很长，优先读取和问题最相关的章节（安装步骤、配置项、示例命令、注意事项、版本要求）。

### 第四步：提取信息并作答

从文档中提取关键信息，组织成可执行答案：

- 先给直接结论
- 再给步骤/命令/配置示例
- 补充必要前置条件与常见坑

语言要求：回答语言必须与用户提问语言一致（中文问就中文答，英文问就英文答）。

### 第五步（可选）：官网检索

若前面步骤无法完成（本地无文档、文档缺失、信息不足），可查看以下位置作为兜底：

- 项目 `README.md`（`$HUBOS_ROOT/README.md`）
- 项目 `docs/` 目录下的架构文档（`architecture-*.md`）

明确告知用户该结论来自哪个文档。

## 输出质量要求

- 不编造不存在的配置项或命令
- 遇到版本差异时，明确标注“需以当前文档版本为准”
- 涉及路径、命令、配置键时，尽量给可复制的原文片段
- 若信息仍不足，明确缺口并告诉用户还需要哪类信息（例如操作系统、安装方式、报错日志）
