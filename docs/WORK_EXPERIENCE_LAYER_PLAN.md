# Work Experience Layer — Phase 0–3 Plan

## 1. Overview

**Purpose:** After each task completes, extract a durable "experience card" that captures what happened, why, and how to do it again better. These cards are stored persistently and retrieved proactively before similar future tasks run.

**Relation to existing stack:**

| Component | Role | Feeds Into |
|---|---|---|
| `ReflectionEngine` | Produces `ReflectionReport` from `TaskContext` | `WorkExperienceExtractor` |
| `EpisodicMemory` | Records raw event trace | `WorkExperienceExtractor` |
| `LearnedPolicy` | Route-hint-level action snippet | (existing — not replaced) |
| **WorkExperience** | **Post-task reusable experience card** | **`WorkExperienceRetriever`** |

WorkExperience is orthogonal to `LearnedPolicy`:
- `LearnedPolicy` → controls routing/execution parameters (worker priority, timeout, retry)
- `WorkExperience` → captures task-level lessons (what to do, what to avoid, how to handle edge cases)

**Hard constraints:**

1. Does not modify `SkillsManager`, `SkillsHub`, or any skill-loading path
2. Does not auto-participate in execution — no prompt injection, no automatic context extension
3. All new capabilities guarded by a feature flag `enable_work_experience_layer`
4. Feature flag defaults to `False` (off)
5. Follows existing `LocalMemoryStore` file layout and atomic-write conventions
6. All tests pass; build must not break

---

## 2. Architecture

```
TaskContext  ──►  ReflectionEngine  ──►  ReflectionReport
                                                   │
                                          WorkExperienceExtractor
                                                   │
                                                   ▼
                                          WorkExperience  (card)
                                                   │
                                          LocalWorkExperienceStore
                                          (~/.hubos/work_experience/)
                                                   │
                                          WorkExperienceRetriever
                                          (scope + keyword + trigger)
```

**Data flow:**
1. Task completes → `TaskContext` is available
2. `ReflectionEngine.reflect(context)` → `ReflectionReport`
3. If `enable_work_experience_layer=True`: `WorkExperienceExtractor.extract(report, context)` → `WorkExperience`
4. `LocalWorkExperienceStore.save(experience)` persists the card to `~/.hubos/work_experience/`
5. Before a future task runs: `WorkExperienceRetriever.retrieve(scope, keywords, trigger_hint)` → list of relevant `WorkExperience` cards

---

## 3. Phase 0 — Module Scaffold

**Location:** `src/hubos/core/work_experience/`

```
work_experience/
├── __init__.py          # Public exports
├── schemas.py           # WorkExperience, WorkExperienceScope, WorkExperienceStore (Protocol)
├── store.py             # LocalWorkExperienceStore implementation
├── extractor.py         # WorkExperienceExtractor
├── retriever.py         # WorkExperienceRetriever
└── feature_flag.py     # enable_work_experience_layer flag access
```

**Feature flag:** `enable_work_experience_layer` (bool, default `False`)

---

## 4. Phase 1 — Data Model (`schemas.py`)

### `WorkExperienceScope` (Enum)

```python
class WorkExperienceScope(str, Enum):
    GLOBAL = "global"        # System-wide lesson
    USER = "user"            # Per-user lesson
    PROJECT = "project"      # Per-project lesson
    SESSION = "session"      # Per-session lesson
```

### `WorkExperience`

```python
@dataclass
class WorkExperience:
    experience_id: UUID
    scope: WorkExperienceScope
    trigger_keywords: list[str]          # Extracted keywords for retrieval
    trigger_hint: str                    # Short trigger pattern (e.g. "file:python:csv")
    title: str                           # One-line lesson title
    what_happened: str                   # Narrative description
    what_worked: list[str]               # Bullet list
    what_failed: list[str]              # Bullet list
    guidance: str                       # Actionable guidance for next time
    avoidance: str                       # What to avoid
    confidence: float                    # 0.0–1.0
    source_task_id: str
    source_session_id: str
    source_trace_id: str
    applicability_tags: list[str]         # e.g. ["python", "csv", "file-io"]
    hit_count: int = 0
    last_retrieved_at: Optional[datetime] = None
    disabled: bool = False
    created_at: datetime
    updated_at: datetime
```

### `WorkExperienceStore` (Protocol)

```python
@runtime_checkable
class WorkExperienceStore(Protocol):
    def save(self, experience: WorkExperience) -> None: ...
    def get(self, experience_id: UUID) -> Optional[WorkExperience]: ...
    def list_all(self) -> list[WorkExperience]: ...
    def disable(self, experience_id: UUID) -> bool: ...
    def increment_hit(self, experience_id: UUID) -> None: ...
```

---

## 5. Phase 2 — Local File Store (`store.py`)

**Root:** `~/.hubos/work_experience/`

**Layout:**

```
~/.hubos/work_experience/
├── index.jsonl                    # Append-only index of all card metadata
├── by_scope/
│   ├── global/{experience_id}.json
│   ├── user/{experience_id}.json
│   ├── project/{experience_id}.json
│   └── session/{experience_id}.json
└── keywords/
    └── {keyword}.jsonl            # Inverted index: keyword -> experience_ids
```

**Atomic write pattern** (identical to `LocalMemoryStore`):

```python
tmp = path.with_suffix(".tmp")
tmp.write_text(json.dumps(payload), encoding="utf-8")
shutil.move(str(tmp), str(path))
```

**Key methods:**

```python
class LocalWorkExperienceStore:
    def __init__(self, root: Optional[Path] = None) -> None: ...

    def save(self, experience: WorkExperience) -> None: ...
    def get(self, experience_id: UUID) -> Optional[WorkExperience]: ...
    def list_all(self) -> list[WorkExperience]: ...
    def list_by_scope(self, scope: WorkExperienceScope) -> list[WorkExperience]: ...
    def disable(self, experience_id: UUID) -> bool: ...
    def increment_hit(self, experience_id: UUID) -> None: ...
    def rebuild_keyword_index(self) -> None: ...  # Offline rebuild utility
```

---

## 6. Phase 3 — Extractor (`extractor.py`)

```python
class WorkExperienceExtractor:
    def __init__(
        self,
        store: WorkExperienceStore,
        min_confidence: float = 0.5,
    ) -> None: ...

    def extract(
        self,
        report: ReflectionReport,
        context: TaskContext,
    ) -> Optional[WorkExperience]:
        """
        Convert a ReflectionReport + TaskContext into a WorkExperience card.

        Returns None if confidence < min_confidence or no useful content.
        """
```

**Extraction heuristics:**

1. **title** — Concatenate first `what_worked` item + task type hint
2. **trigger_keywords** — Extract nouns/verbs from `task_input`, `what_worked`, `what_failed`
3. **trigger_hint** — `{first_key}:{second_key}:{value_hash}` pattern (e.g. `type:file:csv`)
4. **what_happened** — Narrative built from `what_worked` + `what_failed` lists
5. **guidance** — Derived from `next_time_strategy` in `ReflectionReport`
6. **avoidance** — Derived from `what_failed` items with root-cause context
7. **applicability_tags** — Extracted from execution trace tool names + task_input keys

---

## 7. Phase 4 (deferred) — Retriever (`retriever.py`)

```python
class WorkExperienceRetriever:
    def __init__(
        self,
        store: WorkExperienceStore,
        max_results: int = 5,
    ) -> None: ...

    def retrieve(
        self,
        scope: Optional[WorkExperienceScope] = None,
        keywords: Optional[list[str]] = None,
        trigger_hint: Optional[str] = None,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """
        Retrieve experience cards matching:
        - scope filter (exact match)
        - keyword overlap (at least one keyword matches)
        - trigger_hint prefix match
        Sorted by: scope priority (GLOBAL > USER > PROJECT > SESSION) then hit_count desc
        """
```

**Matching algorithm:**
1. Filter by scope (if provided)
2. Score each card: `keyword_overlap_score = len(keywords ∩ card.trigger_keywords) / max(len(keywords), 1)`
3. Filter by trigger_hint prefix (if provided)
4. Sort: scope priority (global > user > project > session), then `hit_count` descending

---

## 8. Feature Flag Integration

**Flag name:** `ENABLE_WORK_EXPERIENCE_LAYER`

**Default:** `False`

**Location added:** `src/hubos/core/infra/feature_flags.py`

**Usage in extractor call site (Phase 5, not implemented this round):**

```python
from hubos.core.infra.feature_flags import get_feature_flags

if get_feature_flags().enable_work_experience_layer:
    extractor = WorkExperienceExtractor(store)
    card = extractor.extract(report, context)
    if card:
        store.save(card)
```

---

## 9. Testing Strategy

**Test file:** `tests/core/test_work_experience.py`

**Fixtures:**
- `store` — temporary directory-backed `LocalWorkExperienceStore`
- `sample_context` — `TaskContext` with known `task_input`, `execution_trace`, `task_result`
- `sample_report` — `ReflectionReport` with known `what_worked`, `what_failed`, `root_cause`, `next_time_strategy`

**Test cases:**

| Test | What it verifies |
|---|---|
| `test_store_save_and_get` | Round-trip save/get |
| `test_store_list_all` | Returns all saved cards |
| `test_store_list_by_scope` | Scope filtering works |
| `test_store_disable` | Disabled card excluded from default retrieval |
| `test_store_increment_hit` | hit_count incremented |
| `test_extractor_extracts_full_card` | All fields populated from report |
| `test_extractor_returns_none_low_confidence` | Skips cards below threshold |
| `test_extractor_extracts_keywords` | Keywords from task_input extracted |
| `test_retriever_scope_filter` | Scope filtering in retrieve |
| `test_retriever_keyword_filter` | Keyword overlap scoring works |
| `test_retriever_trigger_hint_filter` | Prefix matching works |
| `test_retriever_sorted_by_scope_priority` | Global > User > Project > Session |
| `test_retriever_excludes_disabled_by_default` | Disabled cards hidden |

---

## 10. Out of Scope for Phase 0–3

- Phase 4 integration (calling extractor after `ReflectionEngine.reflect()`)
- LLM-powered extraction (uses heuristic rules only in Phase 0–3)
- Automatic prompt injection of retrieved cards
- Keyword inverted-index persistence (keyword index rebuilt on startup)
- Archive/eviction policy
- Cross-user experience sharing
