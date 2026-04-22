---
name: super_crawler
description: "深度客户开发工具集。当需要：发现企业邮箱/联系人、验证邮箱有效性、深度爬取公司网站（含 JS 渲染）、搜索潜在客户公司列表、查询贸易统计数据（OEC/WTO/UN Comtrade）时使用。依赖本地 super-crawler 工具（~/projects/super-crawler），适合 B2B 销售、外贸开发、竞争对手调研等深度场景。日常搜索请优先使用 web_crawl 或 tavily_search；本技能用于需要获取联系人和贸易情报的深度客户开发场景。"
metadata:
  {
    "builtin_skill_version": "1.0",
    "hubos":
      {
        "emoji": "🔍",
        "requires":
          {
            "bins": ["node"],
            "local_tools": ["~/projects/super-crawler"],
            "envs_optional": ["HUNTER_API_KEY"]
          }
      }
  }
---

> **前提**：本 skill 依赖 `~/projects/super-crawler`（本地 Node.js 工具集）。
> API Key（Hunter.io）读取 super-crawler 自身配置，通常无需额外设置。

# super_crawler — 深度客户开发工具集

基于 `~/projects/super-crawler/src/openclaw-tools.js` 的封装，集成了：
- **深度网页爬虫**：支持递归爬取、SPA 渲染、结构化选择器提取
- **企业邮箱发现**：通过域名批量发现企业联系人
- **邮箱验证与生成**：验证邮箱有效性，预测邮件格式
- **公司搜索与情报**：按关键词/行业/国家搜索公司
- **贸易数据查询**：OEC / WTO / UN Comtrade 公开贸易统计

---

## 快速调用方式

所有工具通过统一入口调用：

```bash
bash scripts/call.sh <tool> '<json_params>'
```

或直接调用：

```bash
node ~/projects/super-crawler/src/openclaw-tools.js call <tool> '<json_params>'
```

列出所有可用工具：
```bash
bash scripts/call.sh list
```

---

## 工具详情

### 1. `web_crawl` — 深度网页爬虫

> ⚠️ 注意：这里的 `web_crawl` 是 super-crawler 内置的浏览器爬虫，
> 比 web_crawl skill 的 xcrawl 更重，支持更深层的结构化提取。
> 轻量场景请用 web_crawl skill。

```bash
bash scripts/call.sh web_crawl '{"url":"https://example.com","depth":2}'
bash scripts/call.sh web_crawl '{"url":"https://app.example.com","depth":1,"spa":true}'
bash scripts/call.sh web_crawl '{"url":"https://example.com","selectors":"h2:title,.email:contact_email"}'
```

| 参数 | 类型 | 说明 | 默认 |
|------|------|------|------|
| `url` | string | 目标 URL | 必填 |
| `depth` | int | 递归爬取深度 | 2 |
| `spa` | bool | 是否用无头浏览器（SPA/React） | false |
| `selectors` | string | CSS 选择器，格式 `selector:名称,...` | — |

---

### 2. `hunter_domain` — 发现域名下的企业邮箱

通过 Hunter.io API 查找某域名关联的所有公开邮箱和联系人。

```bash
bash scripts/call.sh hunter_domain '{"domain":"apple.com"}'
bash scripts/call.sh hunter_domain '{"domain":"tesla.com","limit":20}'
bash scripts/call.sh hunter_domain '{"domain":"example.com","department":"sales","seniority":"senior"}'
```

| 参数 | 类型 | 说明 | 默认 |
|------|------|------|------|
| `domain` | string | 目标域名（不含 http） | 必填 |
| `limit` | int | 最多返回邮箱数 | 10 |
| `department` | string | 部门筛选（sales/engineering/hr 等） | — |
| `seniority` | string | 级别（senior/executive/junior） | — |

**返回示例：**
```json
{
  "domain": "example.com",
  "total": 42,
  "pattern": "{first}.{last}@example.com",
  "emails": [
    {"value": "john.doe@example.com", "first_name": "John", "last_name": "Doe",
     "position": "CEO", "confidence": 94}
  ]
}
```

---

### 3. `hunter_verify` — 验证邮箱有效性

调用 Hunter.io 验证某邮箱是否真实存在（DNS + SMTP 验证）。

```bash
bash scripts/call.sh hunter_verify '{"email":"ceo@example.com"}'
```

**返回关键字段：**
- `result`: `deliverable` / `undeliverable` / `risky`
- `score`: 可信度分（0-100）
- `regexp`: 是否符合常见格式
- `mx_records`: 域名是否有 MX 记录

---

### 4. `hunter_generate` — 生成预测邮箱

基于域名邮箱格式规则，推测目标人物的邮箱。

```bash
bash scripts/call.sh hunter_generate '{"domain":"example.com","first_name":"Elon","last_name":"Musk"}'
```

**返回：**
```json
{
  "email": "elon.musk@example.com",
  "score": 85,
  "pattern": "{first}.{last}@{domain}"
}
```

---

### 5. `hunter_company` — 获取公司基本信息

```bash
bash scripts/call.sh hunter_company '{"domain":"openai.com"}'
```

返回公司名、行业、规模、社交媒体链接等。

---

### 6. `company_search` — 搜索目标公司列表

按关键词、行业、国家搜索匹配公司，用于批量客户开发。

```bash
bash scripts/call.sh company_search '{"keywords":"outdoor furniture manufacturer","country":"US","max_results":20}'
bash scripts/call.sh company_search '{"keywords":"steel pipe supplier","industry":"manufacturing","country":"DE"}'
bash scripts/call.sh company_search '{"keywords":"LED lighting wholesale","country":"CN","max_results":30}'
```

| 参数 | 类型 | 说明 | 默认 |
|------|------|------|------|
| `keywords` | string | 搜索关键词 | 必填 |
| `country` | string | 国家代码（US/DE/CN/...） | — |
| `industry` | string | 行业筛选 | — |
| `max_results` | int | 最多返回数量 | 30 |
| `mode` | string | `api`（快速）/ `crawl`（爬虫，更多） | api |

---

### 7. `trade_stats` — 贸易统计数据

从 OEC / WTO / UN Comtrade 查询公开贸易数据，用于市场研究。

```bash
bash scripts/call.sh trade_stats '{"product":"solar panels","year":2024}'
bash scripts/call.sh trade_stats '{"product":"steel","importer":"US","exporter":"CN","year":2023}'
bash scripts/call.sh trade_stats '{"hs_code":"940360","year":2024}'
```

| 参数 | 类型 | 说明 |
|------|------|------|
| `product` | string | 产品名称（英文） |
| `hs_code` | string | HS 编码（优先） |
| `importer` | string | 进口国代码 |
| `exporter` | string | 出口国代码 |
| `year` | int | 年份 |

---

## 典型工作流

### 场景 1：B2B 外贸客户开发

```
步骤 1 - 找目标公司：
  bash scripts/call.sh company_search \
    '{"keywords":"furniture retailer","country":"US","max_results":30}'

步骤 2 - 发现邮箱：
  bash scripts/call.sh hunter_domain '{"domain":"targetcompany.com","department":"purchasing"}'

步骤 3 - 生成/验证邮箱：
  bash scripts/call.sh hunter_generate '{"domain":"targetcompany.com","first_name":"John","last_name":"Smith"}'
  bash scripts/call.sh hunter_verify '{"email":"john.smith@targetcompany.com"}'

步骤 4 - 深度了解公司：
  bash scripts/call.sh web_crawl '{"url":"https://targetcompany.com","depth":2}'
  bash scripts/call.sh hunter_company '{"domain":"targetcompany.com"}'
```

### 场景 2：市场进入分析

```
步骤 1 - 贸易量了解：
  bash scripts/call.sh trade_stats '{"product":"solar panel","importer":"DE","year":2024}'

步骤 2 - 找进口商：
  bash scripts/call.sh company_search '{"keywords":"solar panel importer","country":"DE"}'

步骤 3 - 联系人发现：
  bash scripts/call.sh hunter_domain '{"domain":"<target>.com"}'
```

### 场景 3：竞争对手深度调研

```
步骤 1 - 爬取网站：
  bash scripts/call.sh web_crawl '{"url":"https://competitor.com","depth":3}'

步骤 2 - 团队成员：
  bash scripts/call.sh hunter_domain '{"domain":"competitor.com","seniority":"senior"}'

步骤 3 - 公司规模情报：
  bash scripts/call.sh hunter_company '{"domain":"competitor.com"}'
```

---

## 与 web_crawl skill 的分工

| 需求 | 工具 |
|------|------|
| 搜索最新资讯 | `web_crawl` (xcrawl search) |
| 抓取单个/多个页面内容 | `web_crawl` (xcrawl scrape) |
| 深度爬取+结构化提取 | `super_crawler` web_crawl |
| 发现企业邮箱/联系人 | `super_crawler` hunter_domain |
| 验证/生成邮箱 | `super_crawler` hunter_verify/generate |
| 批量搜索目标公司 | `super_crawler` company_search |
| 贸易数据研究 | `super_crawler` trade_stats |

---

## 注意事项

1. **依赖 Hunter.io API**：hunter_* 工具需要有效的 Hunter.io API Key，免费额度有限
2. **本地工具**：super-crawler 需在 `~/projects/super-crawler/` 存在，不随 HubOS 打包
3. **数据来源声明**：贸易数据来自 OEC/WTO/UN Comtrade 公开数据，存在滞后（通常 6-12 个月）
4. **合规使用**：所有工具均用于合法商业目的，遵守目标网站 robots.txt 规则
