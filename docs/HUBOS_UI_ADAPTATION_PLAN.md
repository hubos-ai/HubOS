# HubOS HubOS UI Adaptation Plan

> **Purpose**: Document how the HubOS console frontend (`/HubOS-WebUI/console`) is adapted to run against the HubOS/XClaw backend (`/HubOS/services/gateway/`), page by page.
>
> **Guiding principle**: HubOS UI is the canonical frontend baseline. HubOS backend/XClaw gateway is the capability baseline. All pages preserve HubOS's original structure; only data access and capability-boundary expression are adapted.

---

## Guiding Rules

1. **HubOS UI is the canonical frontend** — do not redesign page skeletons, do not rewrite component trees
2. **HubOS backend / XClaw gateway is the capability baseline** — pages call whatever the backend provides
3. **Honest capability boundaries** — if a backend route doesn't exist, mark it honest partial/stub, do not fake it
4. **Only permitted changes**:
   - `VITE_API_BASE_URL` env var (API base URL)
   - API adapter layer (request config, endpoint mapping)
   - `honest partial` / `honest stub` UI messaging
   - HubOS branding strings
   - Auth token / routing接入
5. **Prohibited changes**:
   - Redesigning HubOS page structures
   - Rewriting HubOS component trees
   - Creating new HubOS-specific page layouts
   - Touching legacy live backend (8001) or nginx

---

## Page Classification

Each page is classified as:

| Classification | Meaning |
|---------------|--------|
| `READY_TO_ADAPT` | HubOS page structure is intact; HubOS backend has the required API routes |
| `NEEDS_API_ADAPTER` | HubOS backend has the API routes but path/shape differs; needs adapter-layer mapping |
| `NEEDS_BACKEND_CAPABILITY` | HubOS backend lacks required routes; page will show honest partial/stub |
| `KEEP_AS_IS_FOR_NOW` | Page works as-is against HubOS backend (no changes needed) |

### Page Readiness Table

| Page | Route | Classification | Notes |
|------|-------|---------------|-------|
| **Login** | `/login` | `NEEDS_API_ADAPTER` | Auth endpoints (`/auth/login`, `/auth/register`, `/auth/status`) match exactly. Adapter may need token-storage key change (`hubos_auth_token` → HubOS token key). |
| **Chat** | `/chat/*` | `NEEDS_API_ADAPTER` | HubOS uses `@agentscope-ai/chat` + `/api/chat/` routes. HubOS has `chat_hubos` router. Needs adapter mapping for chat + session endpoints. |
| **Control/Channels** | `/channels` | `READY_TO_ADAPT` | HubOS/XClaw has full `/api/channels` CRUD via `channels.py` router. Adapter needed for `channelApi` mapping. |
| **Control/Sessions** | `/sessions` | `NEEDS_BACKEND_CAPABILITY` | HubOS has no `/api/sessions` router. Will be honest stub. |
| **Control/CronJobs** | `/cron-jobs` | `READY_TO_ADAPT` | HubOS has `jobs.py` router at `/api/jobs` with full CRUD. Adapter needed for `cronJobApi` mapping. |
| **Control/Heartbeat** | `/heartbeat` | `READY_TO_ADAPT` | HubOS has `heartbeat.py` router at `/api/heartbeat`. Adapter needed for `heartbeatApi` mapping. |
| **Agent/Tools** | `/tools` | `READY_TO_ADAPT` | HubOS has `tools.py` router at `/api/tools`. Adapter needed. |
| **Agent/MCP** | `/mcp` | `READY_TO_ADAPT` | HubOS has `mcp.py` router at `/api/mcp`. Adapter needed. |
| **Agent/Skills** | `/skills` | `READY_TO_ADAPT` | HubOS has `skills.py` router at `/api/skills`. Adapter needed. |
| **Agent/SkillPool** | `/skill-pool` | `NEEDS_API_ADAPTER` | HubOS has `skill_pool.py` router at `/api/skill-pool`. Route path differs from HubOS (`/skill-pool` vs `/skills/pool`). Needs adapter mapping. |
| **Agent/Workspace** | `/workspace` | `NEEDS_BACKEND_CAPABILITY` | HubOS has `/api/workspace` (zip download/upload) but no collection API. `workspaces.py` added in CAP-1 provides `/api/workspaces` collection. Gap: `/api/workspace/download` zip routes differ. |
| **Settings/Agents** | `/agents` | `READY_TO_ADAPT` | HubOS has `/api/agents` CRUD via `agents.py`. Adapter needed for `agentsApi` mapping. |
| **Settings/Models** | `/models` | `READY_TO_ADAPT` | HubOS has `provider.py`, `models.py` routers at `/api/providers`, `/api/models`. Adapter needed for `providerApi` mapping. |
| **Settings/Security** | `/security` | `NEEDS_BACKEND_CAPABILITY` | HubOS has `security.py` router at `/api/security`. Shape may differ from HubOS's expected fields. Needs honest comparison. |
| **Settings/Environments** | `/environments` | `READY_TO_ADAPT` | HubOS has `envs.py` router at `/api/envs`. Adapter needed for `envApi` mapping. |
| **Settings/TokenUsage** | `/token-usage` | `NEEDS_BACKEND_CAPABILITY` | HubOS has `token_usage.py` router at `/api/token-usage`. Shape check needed. |
| **Settings/VoiceTranscription** | `/voice-transcription` | `NEEDS_BACKEND_CAPABILITY` | HubOS has `transcription.py` router at `/api/transcription`. Shape check needed. |

---

## Phase 1: Minimal Runnable Baseline

**Goal**: Get HubOS console frontend running against HubOS backend (8012) with only env var changes.

**What was done in Phase 1**:
- Created `console/.env` with `VITE_API_BASE_URL=http://localhost:8012`
- Frontend dev server starts on port 5173 (unchanged)
- Backend remains HubOS port 8012

**Phase 1 outcome**:
- Frontend starts ✅
- Auth flow: `POST /auth/login`, `POST /auth/register`, `GET /auth/status` — all exact route matches ✅
- Pages that call exact-matching routes work immediately
- Pages calling non-matching routes get honest 404 → adapter work needed

---

## Phase 2: Per-Page Adapter Work (Priority Order)

### Priority 1: High-value, low-risk pages

**Settings/Models** (`/models`) — `READY_TO_ADAPT`
- HubOS calls: `providerApi.listProviders()`, `providerApi.getProvider()`, `providerApi.updateProvider()`
- HubOS has: `/api/providers`, `/api/models` routers
- Action: Map `providerApi` to HubOS `/api/providers` endpoints; verify response shapes match
- Expected effort: Low — routes exist, shape check needed

**Control/Channels** (`/channels`) — `READY_TO_ADAPT`
- HubOS calls: `channelApi.listChannels()`, `channelApi.saveConfig()`, etc.
- HubOS has: `channels.py` router at `/api/channels`
- Action: Map `channelApi` to HubOS endpoints
- Expected effort: Low — routes exist

**Control/CronJobs** (`/cron-jobs`) — `READY_TO_ADAPT`
- HubOS calls: `cronJobApi.listJobs()`, `cronJobApi.createJob()`, etc.
- HubOS has: `jobs.py` router at `/api/jobs`
- Action: Map `cronJobApi` to HubOS `/api/jobs` endpoints
- Expected effort: Low

### Priority 2: Medium-effort pages

**Chat** (`/chat/*`) — `NEEDS_API_ADAPTER`
- HubOS uses `@agentscope-ai/chat` + `/api/chat/` + `/api/sessions/`
- HubOS has `chat_hubos.py` router at `/api/chat-hubos`
- Action: Map chat and session endpoints; verify `@agentscope-ai/chat` component compatibility
- Expected effort: Medium — route name differs

**Settings/Agents** (`/agents`) — `READY_TO_ADAPT`
- HubOS calls: `agentsApi.listAgents()`, `agentsApi.createAgent()`, etc.
- HubOS has: `agents.py` router at `/api/agents`
- Action: Map `agentsApi` to HubOS endpoints
- Expected effort: Low

**Agent/SkillPool** (`/skill-pool`) — `NEEDS_API_ADAPTER`
- HubOS calls: `GET /api/skills/pool`
- HubOS has: `skill_pool.py` router at `/api/skill-pool`
- Action: Map HubOS's `/api/skills/pool` → HubOS `/api/skill-pool`
- Expected effort: Medium — route path differs

### Priority 3: Pages needing backend capability work

**Control/Sessions** (`/sessions`) — `NEEDS_BACKEND_CAPABILITY`
- HubOS has no sessions router
- Action: Either implement sessions router in HubOS, or mark as honest partial
- Expected effort: High (if implementing) / Low (if honest partial)

**Agent/Workspace** (`/workspace`) — `NEEDS_BACKEND_CAPABILITY`
- HubOS has zip download/upload only (`/api/workspace/download`, `/api/workspace/upload`)
- CAP-1 added `/api/workspaces` collection API
- Action: Compare HubOS's workspace frontend needs vs HubOS's zip-only capability
- Expected effort: Gap analysis needed

**Settings/Security** (`/security`) — `NEEDS_BACKEND_CAPABILITY`
- HubOS has `security.py` router but shape check needed
- Action: Verify response shapes match HubOS's expected `RuleTable`/`RuleModal` data model
- Expected effort: Medium (shape check + potential gap)

### Priority 4: Lower-priority pages

- `Settings/Environments`: HubOS has `envs.py` — check shape
- `Settings/TokenUsage`: HubOS has `token_usage.py` — check shape
- `Settings/VoiceTranscription`: HubOS has `transcription.py` — check shape
- `Agent/Tools`, `Agent/MCP`, `Agent/Skills`: HubOS has routers — check shape

---

## Adapter Pattern

When adapting, follow this pattern:

```
HubOS frontend API call                    HubOS backend route
────────────────────────────────────────────────────────────────
GET /api/providers                          GET /api/providers  ✅
POST /api/channels/:id/config              POST /api/channels/:id/config  ✅
GET /api/agents                           GET /api/agents  ✅
GET /api/sessions                         → 404 → honest partial
```

**Adapter file location**: `src/api/adapters/` in the HubOS console, or inline remapping in `src/api/config.ts`.

---

## Storage Conventions

HubOS backend uses local JSON file storage in `{base_dir}/` (`~/.xclaw/`):

| File | Purpose |
|------|---------|
| `cronjobs.json` | Cron jobs CRUD |
| `heartbeat_config.json` | Heartbeat config |
| `workspaces.json` | Workspace collection (CAP-1) |
| `skill_pool.json` | Skill pool |
| `memory.json` | Memory store |

This is the same pattern HubOS uses. No database needed.

---

*Document created: 2026-04-19*
*Phase 1 baseline: HubOS console frontend running against HubOS backend (8012)*
