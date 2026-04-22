---
name: tavily_search
description: "AI 优化的联网搜索与网页内容提取。当需要查询最新资讯、实时信息、市场数据、当前事件，或需要从指定 URL 提取正文内容时使用。支持普通搜索、深度研究模式和新闻专项搜索。需要配置 TAVILY_API_KEY 环境变量，或在 HubOS 设置中启用 tavily MCP。"
metadata:
  {
    "builtin_skill_version": "1.0",
    "hubos":
      {
        "emoji": "🔍",
        "requires": { "bins": ["node"], "envs": ["TAVILY_API_KEY"] }
      }
  }
---

> **脚本路径约定**：所有 `scripts/` 路径相对于本 skill 目录。
> 调用方式：`node {skill_dir}/scripts/search.mjs "query"`
> 需要 `TAVILY_API_KEY` 环境变量已设置。

# Tavily Search — 联网搜索与内容提取

AI 优化的网络搜索，专为 Agent 设计，返回干净、相关的摘要而非原始 HTML。

---

## 前置条件

需要 `TAVILY_API_KEY`。获取地址：https://app.tavily.com

验证配置：
```bash
echo $TAVILY_API_KEY
```

---

## 接口一：网页搜索

```bash
node scripts/search.mjs "搜索关键词"
node scripts/search.mjs "搜索关键词" -n 10
node scripts/search.mjs "搜索关键词" --deep
node scripts/search.mjs "最新AI新闻" --topic news
node scripts/search.mjs "最新AI新闻" --topic news --days 3
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `"query"` | 搜索关键词（必填） | — |
| `-n <count>` | 返回结果数量（最大 20） | 5 |
| `--deep` | 深度搜索模式（更慢，更全面） | 关闭 |
| `--topic general\|news` | 搜索类型：通用 / 新闻 | general |
| `--days <n>` | 新闻模式：限制最近 N 天 | — |

### 使用场景

```bash
# 查询最新信息
node scripts/search.mjs "Claude 4 最新功能" -n 5

# 深度研究（适合复杂问题）
node scripts/search.mjs "A股量化交易策略 2026" --deep -n 8

# 新闻搜索
node scripts/search.mjs "人民币汇率" --topic news --days 7
```

---

## 接口二：URL 内容提取

从指定 URL 提取正文内容（去除广告、导航等噪音）：

```bash
node scripts/extract.mjs "https://example.com/article"
node scripts/extract.mjs "https://url1.com" "https://url2.com"
```

### 使用场景

```bash
# 阅读某篇文章的完整内容
node scripts/extract.mjs "https://techcrunch.com/2026/04/21/some-article"

# 批量提取多个页面
node scripts/extract.mjs "https://a.com" "https://b.com" "https://c.com"
```

---

## 工作流示例

### 场景 1：研究某个话题

```
用户: 帮我了解一下最近 Agent 框架的发展趋势
助手:
  1. node scripts/search.mjs "AI Agent framework 2026 趋势" --deep -n 8
  2. 从结果中找到关键文章 URL
  3. node scripts/extract.mjs "<url1>" "<url2>"（深入阅读）
  4. 整理摘要给用户
```

### 场景 2：实时数据查询

```
用户: 今天 A 股市场怎么样？
助手:
  1. node scripts/search.mjs "A股市场今日行情" --topic news --days 1 -n 5
  2. 汇总关键数据回答
```

### 场景 3：验证信息

```
用户: X 公司最新发布了什么产品？
助手:
  1. node scripts/search.mjs "X公司 最新产品发布 2026" -n 5
  2. node scripts/extract.mjs "<官方公告URL>"（获取原文）
```

---

## 注意事项

1. **API 限额**：免费账户有每月请求限制，深度搜索消耗更多配额
2. **结果时效**：Tavily 索引通常延迟数小时到一天，极实时数据需结合 browser 工具
3. **语言**：直接用中文搜索效果良好，无需翻译
4. **与 MCP 的关系**：HubOS 也支持 tavily MCP（在 Agent 设置中启用），功能相同；MCP 方式无需手动调用脚本，AI 自动调用
