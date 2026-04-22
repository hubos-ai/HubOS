---
name: web_crawl
description: "通用网页搜索与内容抓取。当需要：联网搜索最新信息、从指定 URL 抓取正文（包括 JS 渲染的 SPA 页面）、批量获取多个页面内容时使用。由 xcrawl API 驱动，支持深度搜索模式和无头浏览器渲染。比 tavily_search 多了网页抓取（scrape）能力；tavily 更适合快速问答式搜索，web_crawl 更适合需要获取完整页面内容的场景。"
metadata:
  {
    "builtin_skill_version": "1.0",
    "hubos":
      {
        "emoji": "🕷️",
        "requires": { "bins": ["node"], "envs": ["XCRAWL_API_KEY"] }
      }
  }
---

> **脚本路径约定**：所有脚本相对于本 skill 目录。
> API Key 自动读取 `~/.xcrawl/config.json` 中的 `XCRAWL_API_KEY`，无需手动传递。

# web_crawl — 网页搜索与抓取

由 [xcrawl](https://xcrawl.com) 驱动的通用爬虫工具，提供：
- **搜索**：返回结果列表 + AI 综合答案
- **抓取**：将任意网页转为干净的 Markdown，支持 SPA/JS 渲染

---

## 接口一：网页搜索

```bash
node scripts/search.mjs "搜索词"
node scripts/search.mjs "搜索词" -n 10
node scripts/search.mjs "搜索词" --deep
node scripts/search.mjs "搜索词" --json     # 原始 JSON 输出
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `"query"` | 搜索关键词（必填） | — |
| `-n <count>` | 返回结果数（最大 20） | 5 |
| `--deep` | 深度搜索（更慢、更全面） | 关闭 |
| `--json` | 输出原始 JSON（含 credits 消耗） | 关闭 |

**示例：**
```bash
# 查询最新信息
node scripts/search.mjs "Claude 4 发布时间" -n 8

# 深度研究
node scripts/search.mjs "A股量化策略 2026" --deep -n 10

# 获取结构化 JSON
node scripts/search.mjs "AI Agent 框架对比" --json
```

---

## 接口二：网页抓取（URL → Markdown）

```bash
node scripts/scrape.mjs "https://example.com"
node scripts/scrape.mjs "https://example.com" --spa         # JS渲染页面
node scripts/scrape.mjs "https://example.com" --depth 2     # 爬取子页面
node scripts/scrape.mjs "https://a.com" "https://b.com"     # 批量
node scripts/scrape.mjs "https://example.com" --json        # 原始JSON
```

| 参数 | 说明 | 默认 |
|------|------|------|
| `"url"` | 目标 URL（可多个） | — |
| `--spa` | 使用无头浏览器渲染（React/Vue/Angular 等） | 关闭 |
| `--depth <n>` | 递归爬取深度（1=当前页+一级子链接） | 0（仅当前页） |
| `--json` | 输出原始 JSON | 关闭 |

**示例：**
```bash
# 抓取普通页面
node scripts/scrape.mjs "https://docs.python.org/3/library/asyncio.html"

# 抓取 SPA 应用（React/Next.js 等）
node scripts/scrape.mjs "https://app.example.com/page" --spa

# 批量抓取多个页面
node scripts/scrape.mjs "https://a.com/contact" "https://b.com/about" "https://c.com"

# 抓取整个小站（含子链接）
node scripts/scrape.mjs "https://example.com" --depth 2
```

---

## 与 tavily_search 的选择建议

| 场景 | 推荐工具 |
|------|---------|
| 快速搜索、直接问答 | `tavily_search` |
| 需要完整页面内容 | `web_crawl scrape` |
| JS 渲染页面 | `web_crawl scrape --spa` |
| 研究型深度搜索 | 两者都可，`web_crawl --deep` 更全 |
| 批量抓取多个 URL | `web_crawl scrape`（支持多 URL） |

---

## 典型工作流

### 场景 1：研究某个技术话题

```
1. node scripts/search.mjs "Rust async 最佳实践 2026" -n 8
2. 从结果中找到权威文章 URL
3. node scripts/scrape.mjs "<url1>" "<url2>"（读完整内容）
4. 整理给用户
```

### 场景 2：竞品分析

```
1. node scripts/search.mjs "competitor.com 产品功能" -n 5
2. node scripts/scrape.mjs "https://competitor.com/pricing"
3. node scripts/scrape.mjs "https://competitor.com/features" --spa
```

### 场景 3：实时数据追踪

```
1. node scripts/search.mjs "人民币汇率今日" --json
2. 解析 JSON 提取 URL
3. node scripts/scrape.mjs "<具体数据页面>"
```

---

## 注意事项

1. **API 配额**：搜索每次约消耗 2 credits，抓取约 3-5 credits
2. **SPA 页面**：`--spa` 使用异步模式，响应时间约 5-15 秒
3. **内容截断**：抓取结果超过 12,000 字符时自动截断，如需完整内容用 `--json` 查看 chars 字段
4. **反爬限制**：部分网站有 WAF/IP 封锁，xcrawl 有一定绕过能力但无法保证 100% 成功
