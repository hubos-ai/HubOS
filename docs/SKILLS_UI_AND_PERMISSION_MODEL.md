# Skills UI 与权限模型设计文档

> **日期**: 2026-04-19
>
> **项目根目录**: `/Users/allen/HubOS/HubOS-WebUI`
>
> **当前 Backend**: `http://localhost:8001` (XClaw live)
>
> **目的**: 明确 Skills 页面的职责边界、权限分离模型、HubOS 复用策略，以及 HubOS/XClaw 目标 API 设计。

---

## 1. 核心原则

### 1.1 Skills 页面负责什么

**技能资产管理（Skill Asset Management）**

- 查看技能列表（名称、描述、启用状态、来源）
- 创建、编辑、删除技能（含 content 内容管理）
- 从 Hub/远程 URL 导入技能
- 上传 zip 包导入技能
- 启用/禁用技能
- 批量操作（enable/disable/delete）
- 技能内容优化（AI optimize stream）
- 技能配置（config）管理

### 1.2 Skills 页面不负责什么

**技能使用权限（Skill Usage Policy）**

- 哪个 agent 可以使用哪些 skills — 这属于 Agent 配置
- 哪个 channel 可以使用哪些 skills — 这属于 Channel 配置
- 哪个用户/角色可以查看/配置/使用某些 skills — 这属于权限/角色系统
- 技能在特定 agent 下的可见性 — 由 agent-level skill binding 决定

### 1.3 为什么不能混在一起

把"资产管理"和"权限分配"混在一起的典型问题：

| 症状 | 说明 |
|------|------|
| **权限粒度不清** | 在 SkillCard 上toggle enabled，是"禁用这个技能"还是"这个 agent 不能用这个技能"？ |
| **多 agent 场景** | 同一个 skill 在 agent-A 下应该启用，在 agent-B 下应该禁用 — 资产管理页无法表达这个 |
| **Channel 级限制** | 运维想在 channel-微信 禁用某个 skill，但不能影响 agent 配置 |
| **权限委派困难** | 普通用户应该能编辑 skill 内容，但不能改变哪些 agent 可用这个 skill |

**结论**: 技能是系统资产（asset），使用权（policy）是另一层关注点。资产管理 UI 和权限配置 UI 必须分离。

---

## 2. 页面职责划分

### 2.1 Skills 页面 (`/skills`)

**定位**: 技能资产管理器（Skill Asset Manager）

**职责**:
- 技能的 CRUD（内容 + 元数据）
- Hub 远程导入
- zip 上传导入
- AI 内容优化
- 技能列表展示

**不负责**:
- 某个 agent 是否启用该 skill — agent 自己的配置页管
- channel 级别的 skill 使用限制 — channel 配置页管
- 用户对 skill 的操作权限 — 权限系统管

### 2.2 Agent 页面 (`/agents`)

**定位**: Agent 级别的技能绑定（Agent Skill Binding）

**职责**:
- 列出当前 agent 可用的 skills 列表（来自全局 skill 注册 + agent override）
- 配置哪些 skills 对当前 agent 可用/不可用
- Agent-level skill 参数覆盖（config override）

**说明**: 这是 agent 配置页的一部分（类似 `model` 配置、`channel` 配置）。在 HubOS 中没有这个能力，XClaw 的 `PUT /api/agents/{name}` 也没有 skill binding 字段。这是需要新增的 backend capability。

### 2.3 Channels 页面 (`/channels`)

**定位**: Channel 级别的 skill 使用限制

**职责**:
- 配置 channel 允许/禁止使用哪些 skills
- Channel 级 skill whitelist / blacklist

**说明**: 当前 HubOS 的 SkillScanner 是全局安全扫描，不是 channel 级限制。XClaw 也没有 channel-skill-policy 端点。这是需要新增的 backend capability。

### 2.4 用户权限

| 角色 | 可做什么 |
|------|---------|
| 管理员 | 资产管理（CRUD）+ 权限配置（agent/channel 级） |
| 普通用户 | 资产管理（只读/编辑自己创建的）+ 不可见权限配置 |
| Agent | 只受 agent-skill-binding 约束，不直接操作 UI |

---

## 3. HubOS Skills 页面可直接复用的部分

### 3.1 可直接沿用的 UI/交互

| 组件 | 状态 | 说明 |
|------|------|------|
| 技能列表（SkillCard 网格） | ✅ 可复用 | 纯展示，接收 `SkillSpec[]` 即可 |
| 启用/禁用 Toggle | ✅ 可复用 | 调用 `toggleEnabled` → `PUT /api/skills/{name}` |
| 批量操作按钮 | ✅ 可复用 | `batchEnable/batchDisable/batchDelete` → adapter |
| 刷新按钮 | ✅ 可复用 | `refreshSkills` → `GET /api/skills` |
| 上传 zip 弹窗 | ✅ 可复用 | `uploadSkill` → `POST /skills/upload`（需 XClaw 有此端点）|
| Hub 导入弹窗 | ⚠️ 需 adapter | HubOS `POST /skills/hub/install/start` → XClaw 无对应 |
| AI Optimize 流式窗口 | ⚠️ 需 adapter | HubOS `POST /skills/ai/optimize/stream` → XClaw 有此端点 |

### 3.2 SkillDrawer（编辑技能内容）

| 字段 | HubOS 预期 | XClaw 现状 | 处理方式 |
|------|-----------|-----------|---------|
| name | SkillSpec.name | ✅ | 直接映射 |
| description | SkillSpec.description | ✅ | 直接映射 |
| content | SkillSpec.content | ❌ XClaw 不存储 content | **Honest partial** — content 编辑后不落地，需 warning |
| source | SkillSpec.source | ✅ | 直接映射 |
| enabled | SkillSpec.enabled | ✅ | 直接映射 |
| channels | SkillSpec.channels | ⚠️ XClaw 无 channels 字段 | **Honest partial** — 写入丢失，需 warning |
| config | SkillSpec.config | ⚠️ XClaw 无 config | **Honest partial** — config 不落地 |

### 3.3 需要 Honest Partial 的功能

| 功能 | HubOS 操作 | XClaw 结果 | Honest 处理方式 |
|------|-----------|-----------|---------------|
| **编辑 skill content** | `PUT /skills/save` body 含 content | XClaw 不存储 content，保存后 content 丢失 | 保存后 show `message.warning` 告知 "Skill content is not persisted by XClaw backend" |
| **创建新 skill** | `POST /skills` 含 content | XClaw 无此端点，或不存储 content | `Promise.reject` honest error |
| **skill channels 配置** | `PUT /skills/{name}/channels` | XClaw 无此字段 | `Promise.reject` honest error |
| **skill config 管理** | `GET/PUT/DELETE /skills/{name}/config` | XClaw 无此端点 | `Promise.reject` honest error |
| **Hub 导入** | `POST /skills/hub/install/start` | XClaw 无此端点 | `Promise.reject` honest error |
| **zip 上传** | `POST /skills/upload` | XClaw 无此端点 | `Promise.reject` honest error |
| **pool CRUD** | pool 相关大量端点 | XClaw `/api/skill-pool` 只有 GET/DELETE 部分 | 见 SkillPool 小节 |

### 3.4 SkillPool 页面 (`/skill-pool`)

SkillPool 是 skill 的共享仓库（类似 npm registry）。

| 功能 | XClaw 现状 | 处理方式 |
|------|-----------|---------|
| `GET /api/skill-pool` | ✅ 有，list 已有 | adapter 接入 |
| `DELETE /api/skill-pool/{name}` | ✅ 有 | adapter 接入 |
| `POST /api/skill-pool/search` | ✅ 有（搜索）| adapter 接入 |
| `POST /api/skill-pool/install` | ✅ 有（从 URL/thread 安装）| adapter 接入 |
| zip 上传 | ❌ XClaw 无 | honest reject |
| broadcast / download to workspaces | ❌ XClaw 无 | honest reject |
| builtin-sources / import-builtin | ❌ XClaw 无 | honest reject |
| pool skill content 编辑 | ❌ XClaw 无 content 存储 | honest partial |

---

## 4. HubOS/XClaw 目标权限模型

### 4.1 概念澄清

| 概念 | HubOS 语义 | HubOS/XClaw 目标语义 |
|------|-----------|-------------------|
| Skill | 完整文本技能（content + metadata） | 轻量元数据引用（无 content）+ 全局注册 |
| SkillBinding | 无 | Agent 可用 skill 列表的绑定记录 |
| SkillPolicy | 无 | Channel 级 whitelist/blacklist |
| SkillPermission | 无 | User/Role 级别的操作权限 |

### 4.2 三层权限模型

```
User/Role
    │
    ├── can_view_skills      → 能否在 Skills 列表中看到这个 skill
    ├── can_edit_skill       → 能否编辑 skill 内容/配置
    ├── can_delete_skill     → 能否删除 skill
    │
    ▼
Agent
    │
    └── skill_bindings: [skill_name, ...]   → 这个 agent 可用哪些 skills
    │
    ▼
Channel
    │
    └── skill_policy: { allowed: [...], denied: [...] }  → channel 级限制
```

### 4.3 Backend 权限过滤原则

**所有 skill 操作必须经过 backend 权限过滤，前端不能只靠隐藏按钮。**

- `GET /api/skills` — backend 根据 user role 过滤可见 skills
- `PUT /api/agents/{name}` — backend 检查 user 是否有权修改此 agent 的 skill bindings
- `PUT /api/channels/{name}/config` — backend 检查 user 是否有权修改此 channel 的 skill policy

**前端职责**: 仅负责 UI 表达和诚实地传递用户意图。
**Backend 职责**: 负责实际的权限校验和数据过滤。

---

## 5. API 草案

### A. Skills 资产管理 API

#### 已有（XClaw）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skills` | 列表，返回 `{skills: [{name, description, license, category, enabled}]}` |
| PUT | `/api/skills/{skill_name}` | 更新 enabled 状态 |
| POST | `/api/skills/batch-enable` | 批量启用 |
| POST | `/api/skills/batch-disable` | 批量禁用 |
| POST | `/api/skills/batch-delete` | 批量删除 |
| POST | `/api/skills/ai/optimize/stream` | AI 优化（streaming） |
| GET | `/api/skills/scan/blocked-history` | 安全扫描 blocked 历史 |
| DELETE | `/api/skills/scan/blocked-history` | 清除 blocked 历史 |

#### 缺失，需新增

| 方法 | 路径 | 说明 | 优先级 |
|------|------|------|--------|
| POST | `/api/skills` | 创建 skill（含 content） | 高 |
| GET | `/api/skills/{name}` | 获取单个 skill 详情（含 content） | 高 |
| DELETE | `/api/skills/{name}` | 删除 skill | 高 |
| POST | `/api/skills/install` | 从 .skill 文件或 URL 安装 | 中 |
| PUT | `/api/skills/{name}/channels` | 更新 skill 允许的 channels | 低 |
| GET | `/api/skills/{name}/config` | 获取 skill config | 低 |
| PUT | `/api/skills/{name}/config` | 更新 skill config | 低 |

### B. Skill Pool API

#### 已有（XClaw）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/skill-pool` | 列表 |
| POST | `/api/skill-pool/search` | 搜索 |
| POST | `/api/skill-pool/install` | 安装（从 URL 或 thread virtual path）|
| DELETE | `/api/skill-pool/{skill_name}` | 删除 |

#### 缺失，需新增

| 方法 | 路径 | 说明 | 优先级 |
|------|------|------|--------|
| POST | `/api/skill-pool/upload` | 上传 zip | 低 |
| GET | `/api/skill-pool/builtin-sources` | 内置源列表 | 低 |
| POST | `/api/skill-pool/import-builtin` | 导入内置 | 低 |
| POST | `/api/skill-pool/download` | 下载到 workspace | 低 |

### C. Agent Skill Binding API（新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents/{name}/skills` | 获取 agent 的 skill binding 列表 |
| PUT | `/api/agents/{name}/skills` | 全量更新 agent 的 skill binding 列表 |
| PATCH | `/api/agents/{name}/skills/{skill_name}` | 单个 skill binding 的 enable/disable |

**Request/Response 草案**:

```json
// GET /api/agents/{name}/skills
{
  "agent_name": "my-agent",
  "skills": [
    { "name": "skill-a", "enabled": true },
    { "name": "skill-b", "enabled": false }
  ]
}

// PUT /api/agents/{name}/skills
{
  "skills": [
    { "name": "skill-a", "enabled": true },
    { "name": "skill-b", "enabled": false }
  ]
}
```

### D. Channel Skill Policy API（新增）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/channels/{name}/skills-policy` | 获取 channel skill policy |
| PUT | `/api/channels/{name}/skills-policy` | 更新 channel skill policy |

**Request/Response 草案**:

```json
// GET /api/channels/{name}/skills-policy
{
  "channel": "weixin",
  "policy": {
    "mode": "allowlist",          // "allowlist" | "denylist" | "none"
    "skills": ["skill-a", "skill-c"]  // mode=allowlist 时：只有这些可用；mode=denylist 时：这些禁用
  }
}
```

---

## 6. 推荐实施顺序

### Phase 1: Skills 资产管理页接入（当前可做）

**目标**: 把 HubOS Skills 页面按"资产管理页"接入 XClaw，后端不支持的功能 honest partial。

**具体任务**:
1. 创建 `skillAdapter` 替换 `skillApi`
2. `listSkills` → `GET /api/skills` — unwrap `{skills: []}` → `SkillSpec[]`
3. `toggleEnabled` → `PUT /api/skills/{name}` (body: `{enabled: bool}`)
4. `deleteSkill` → `POST /api/skills/batch-delete` (单个 name)
5. `streamOptimizeSkill` → `POST /api/skills/ai/optimize/stream` (XClaw 已有)
6. **Honest partial**:
   - `createSkill` → `Promise.reject("创建含 content 的 skill 需要 XClaw 新增 POST /api/skkills")`
   - `saveSkill` (content 编辑) → 保存后 show warning "content 不落地"
   - `uploadSkill` → `Promise.reject`
   - `importFromHub` → `Promise.reject`
   - `updateSkillChannels` → `Promise.reject`
   - `getSkillConfig` / `updateSkillConfig` → `Promise.reject`
7. SkillDrawer: content 编辑框保留，但保存时 show warning 说明 content 不存储
8. 不改 UI 结构，只换数据源

**产出**: Skills 资产管理页可用，显示 XClaw 注册的 skill 列表，toggle/delete 可用，内容编辑/创建/导入 honest partial。

---

### Phase 2: Agent Skill Binding 配置能力（需 backend 新增）

**目标**: 在 Agent 配置页添加 skill binding 配置能力。

**前置条件**: XClaw 新增 `GET/PUT /api/agents/{name}/skills` 端点。

**具体任务**:
1. Agent 配置页新增 "Skills" Tab 或内嵌配置区
2. 显示当前 agent 的 skill binding 列表
3. 支持 toggle 单个 skill binding
4. 后端负责权限过滤，前端仅 UI 表达

**说明**: 此阶段属于 backend capability 补充，前端 adapter 工作量小。

---

### Phase 3: SkillPool 接入 + Hub/Pool 高级能力（需 backend 补能力）

**目标**: 接入 SkillPool 页面，补充 pool 功能。

**前置条件**: XClaw 补充 pool 相关端点（upload-zip, download, builtin-sources 等）。

**具体任务**:
1. SkillPool 页面 adapter — list + search + delete 已有，可以接入
2. broadcast/upload-zip/import-builtin 等缺失端点 honest partial
3. 评估 pool skill content 编辑需求

---

## 7. 结论

### 是否建议现在继续做 Skills 页面？

**建议：可以做，但需要分清楚"接入什么"和"什么是 honest partial"。**

Skills 资产管理页面的核心 list + toggle + delete 可以直接接入 XClaw（adapter 工作量低）。
SkillDrawer 的内容编辑功能在 XClaw 不存储 content，需要 honest warning。

### 是否建议先只做"资产管理子集"？

**建议：是的，先做资产管理子集。**

理由：
1. XClaw `/api/skills` 有真实的 list + toggle + batch delete 能力
2. 内容编辑/创建/导入等高级功能 XClaw 没有，不应该强行 fake
3. Agent-level 和 channel-level skill binding 属于独立的权限配置维度，不在 Skills 页面解决
4. Phase 1 工作量低，风险可控，可以快速验证

### Agent/Channel 页面需要承担的权限配置职责

| 页面 | 新增职责 |
|------|---------|
| Agent 配置页 | Agent Skill Binding — 配置当前 agent 可用哪些 skills |
| Channel 配置页 | Channel Skill Policy — 配置 channel 允许/禁止哪些 skills |

这两个能力目前 XClaw 完全没有，属于 **NEEDS_BACKEND_CAPABILITY**。前端 adapter 准备好接收数据即可，真正的权限逻辑在 backend。

---

*文档版本: 1.0*
*创建日期: 2026-04-19*
