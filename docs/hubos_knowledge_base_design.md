# HubOS 自动知识管理设计方案

## 核心目标

HubOS 的知识系统不应该只是“存资料”，而应该形成一个闭环：

```text
任务开始前：分析任务 → 检索相关经验/知识 → 压缩注入给主模型
任务执行中：按需补充事实/工具信息
任务结束后：沉淀经验卡片或知识库条目
```

核心原则是：

> 知识库可以很大，但每次只给模型一张“小抄”。

也就是说，重点不是少存，而是少注入、准注入。

---

## 一、经验卡片和知识库的分工

HubOS 应该把“经验卡片”和“知识库”分开。

| 类型 | 主要存什么 | 例子 |
|---|---|---|
| 经验卡片 | 做事方法、流程、避坑、策略 | 客户开发先查政府采购/中标结果，不要先泛搜 distributor |
| 知识库 | 事实、配置、项目资料、API 细节 | 巴西 CNPJ 查询必须带标点；Compras API 参数限制 |
| 每日笔记 | 原始过程记录、当天工作流水 | 今天测试 Tavily 额度用完，webReader 可用 |
| MEMORY.md | 少量必须常驻的核心规则 | 回答/执行前优先检索相关经验和知识 |

一句话：

> 经验卡是操作系统，知识库是资料库，MEMORY.md 是启动配置。

---

## 二、经验卡片增加经验类型

当前经验卡片应该增加结构化字段，用于任务开始前精准匹配。

建议字段：

```json
{
  "experience_type": "customer_development",
  "subtype": "government_procurement",
  "entities": ["Brazil", "FNDE", "Compras.gov.br", "CNPJ"],
  "applies_to": ["找客户", "供应商开发", "教育设备"],
  "avoidance": ["不要先泛搜 distributor"],
  "guidance": "优先找政府采购/中标结果，再反查供应商联系方式",
  "confidence": "high",
  "updated_at": "2026-05-12"
}
```

### 建议的经验类型

第一版可以先用这些类型：

```text
customer_development   客户开发
web_search             搜索/网页抓取
code_fix               代码修复
ui_design              前端/UI
deployment             部署/CI/GitHub
data_import            数据导入/飞书/CRM
tool_usage             工具使用经验
system_debug           HubOS 自身故障排查
agent_workflow         多 agent 协作
knowledge_memory       记忆/经验系统
```

### 任务开始前的经验匹配流程

```text
用户任务
  ↓
分析任务类型 + 实体 + 意图
  ↓
只在同类型经验卡中检索
  ↓
选 1-2 条最高相关经验
  ↓
压缩成短提示词
  ↓
注入给主模型执行
```

---

## 三、知识库目录结构

知识库不要做成一个大文档，建议分层：

```text
memory/
  core/
    identity.md          # HubOS 身份、长期偏好、最高优先级规则
    operating_rules.md   # 工作方式、审批、安全边界

  knowledge/
    tools.md             # 工具/API/key/可用性/失败记录
    business.md          # 客户开发、国家市场、行业信息
    dev.md               # 代码结构、部署、CI、Docker
    ui.md                # UI 风格、设计规则
    system.md            # HubOS 架构、记忆系统、RunControl

  notes/
    2026-05-12.md        # 每日工作日志

  archive/
    old/
```

`MEMORY.md` 只保留：

- 当前最重要的常驻规则
- 知识库索引
- 最近活跃事项
- 不超过 4KB 的高优先级信息

不要继续把每日总结无限追加到 `MEMORY.md`。

---

## 四、知识库条目格式

知识库条目应该是结构化小块，不要写成长篇散文。

示例：

```md
## 巴西教育客户开发

type: customer_development
entities: Brazil, FNDE, Compras.gov.br, CNPJ
updated: 2026-04-27
confidence: high

Summary:
巴西教育类客户优先从政府采购/中标记录找供应商，不要先泛搜 distributor。

Use when:
- 用户要找巴西客户
- 用户要找教育设备供应商
- 用户要查中标公司

Details:
- FNDE 不在 Comprasnet
- CNPJ 查询必须带标点
- Compras API tamanhoPagina 必须 10-500
```

这样检索时可以优先看：

- `type`
- `entities`
- `Use when`
- `Summary`

而不是整篇乱搜。

---

## 五、命中判断机制

“命中”不应该只靠关键词，建议用多信号打分。

| 信号 | 含义 | 权重 |
|---|---|---|
| 语义相似度 | 当前任务和知识/经验是否表达同类问题 | 40% |
| 关键词重叠 | 是否出现相同实体、工具、国家、业务词 | 20% |
| 任务类型 | 是否属于同一任务类型 | 20% |
| 最近有效性 | 最近是否用过、是否仍有效 | 10% |
| 避坑优先级 | 是否包含“不要做/已失败/额度用完/API不可用”等强避坑信息 | 10% |

建议公式：

```text
score = 0.4 * semantic
      + 0.2 * keyword
      + 0.2 * task_type
      + 0.1 * recency
      + 0.1 * avoidance
```

建议阈值：

```text
score >= 0.75  → 自动注入
0.55 - 0.75   → 候选，预算有余量才注入
< 0.55        → 不注入
```

---

## 六、知识注入减法策略

为了控制 token 成本，注入必须有硬预算。

建议默认配置：

```yaml
knowledge_injection_budget:
  max_items: 3
  max_tokens: 400
  min_confidence: 0.72
  include_full_card: false
  inject_mode: summary_only
```

建议分配：

```text
经验卡片：1-2 条
知识库事实：0-1 条
```

没有高置信度命中就不注入。

---

## 七、注入内容格式

不要注入原文，注入压缩后的小抄。

示例：

```text
Relevant guidance:
- 任务类型：客户开发。
- 历史经验：优先查政府采购/中标结果，再反查供应商，不要先泛搜 distributor。
- 相关事实：巴西 CNPJ 查询必须带标点；Tavily/xcrawl 可能额度耗尽。
```

控制在 `300-500 tokens` 内。

---

## 八、任务结束后的沉淀规则

任务结束后，HubOS 不应该简单追加到 `MEMORY.md`。

应该先判断内容类型：

```text
新方法/流程/避坑 → 写入或更新经验卡片
事实/API/配置 → 写入 knowledge/*.md
过程流水 → 写入 notes/YYYY-MM-DD.md
长期核心规则 → 才允许进入 MEMORY.md
```

每日笔记提炼时也应该是“更新已有知识”，不是无限追加。

```text
每日笔记里发现新经验
  ↓
匹配已有经验卡/知识条目
  ├─ 能匹配 → 更新已有条目
  └─ 不能匹配 → 新建分类知识条目或经验卡
```

---

## 九、建议实施顺序

### 第一步：经验卡片加类型字段

给现有经验卡补充：

- `experience_type`
- `subtype`
- `entities`
- `applies_to`
- `confidence`
- `updated_at`

### 第二步：建立 `memory/knowledge/` 分类知识库

先建：

```text
memory/knowledge/tools.md
memory/knowledge/business.md
memory/knowledge/dev.md
memory/knowledge/ui.md
memory/knowledge/system.md
```

### 第三步：实现 `knowledge_retrieve(task)`

输入用户任务，输出最多 3 条短摘要：

```json
{
  "task_type": "customer_development",
  "matched_experience": [...],
  "matched_knowledge": [...],
  "injection_text": "Relevant guidance: ..."
}
```

### 第四步：任务开始前统一生成 Relevant guidance

主模型执行前注入：

```text
Relevant guidance:
...
```

### 第五步：任务结束后分类沉淀

不要乱追加 `MEMORY.md`，而是更新经验卡或知识库。

---

## 十、最终目标

HubOS 的自动知识管理应该形成这个闭环：

```text
用户任务
  ↓
任务类型识别
  ↓
经验卡片匹配
  ↓
知识库检索
  ↓
预算内压缩注入
  ↓
主模型执行
  ↓
任务结束总结
  ↓
更新经验卡/知识库/每日笔记
```

最终效果：

- HubOS 不会每次从零开始。
- HubOS 不会把所有历史都塞进上下文。
- HubOS 能在任务开始前带着正确经验工作。
- HubOS 的知识库会长期增长，但每次注入仍然很小。
