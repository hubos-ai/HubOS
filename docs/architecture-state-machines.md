# HubOS State Machines — Canonical Boundaries

> Status: **normative**. Last updated at end of Stage B / S6.
>
> Scope: defines every independent state machine that exists inside
> `HubOS-WebUI` today, what each one owns, who can mutate it, and how they
> relate to each other. Written to eliminate the recurring confusion caused
> by overlapping terms (e.g. both machines have a `failed` state but mean
> different things).

---

## 1. Why more than one?

HubOS runs two fundamentally different kinds of work, at two different
altitudes, and on two different timescales. A single monolithic state
enum for both would be wrong — they have different invariants, different
owners, and different consumers.

| # | Machine | Lives in | Unit of work | Scheduler | Persisted? |
|---|---|---|---|---|---|
| **A** | `TaskState` | `hubos.core/schemas/state.py` | `TaskUnit` (Coordinator's atomic task) | `hubos.core.orchestrator.Coordinator` | yes — `TaskStore` / `EventStore` |
| **B** | Workflow / Step `status` | `hubos/agents/tools/agent_workforce.py` | GM-level multi-agent DAG and its steps | in-process `_WorkflowExecution` runner | no — in-memory only |

A third machine, `dag/models.py` DAG `status`, exists for the legacy
`DagScheduler` path and is **not** used by the current `coordinate_workflow`
tool. It stays valid for workflow-preset execution inside
`execution/orchestrator.py`, but is out of scope here (see §5).

---

## 2. Machine A — `TaskState` (authoritative, Coordinator-owned)

**Purpose.** Lifecycle of a single Coordinator `TaskUnit`: everything from
"we received an event" to "we persisted a reply". This is the canonical
state machine of `hubos.core`; every state transition is logged to
`EventStore`, survives restarts, and is observable via
`track_task(task_id)`.

**States.** 11 total, 8 normal + 3 terminal, all defined by `TaskState`
in `hubos.core/schemas/state.py` and gated by `LEGAL_TRANSITIONS`:

```
RECEIVED → NORMALIZED → PLANNED → DISPATCHED → RUNNING → MERGING → RESPONDED → PERSISTED
                                                 ↓↑              ↘
                                             RETRYING          NEEDS_HUMAN
                                                 ↓                  ·
                                              FAILED                ·
```

**Terminal.** `PERSISTED`, `FAILED`, `NEEDS_HUMAN`.

**Writer.** Only `Coordinator`. Every transition goes through
`TaskStateMachine.transition(target)`, which raises
`InvalidStateTransitionError` if the transition is not in
`LEGAL_TRANSITIONS`. This is a **strict** state machine — illegal writes
are hard errors, not warnings.

**Readers.** Anyone: `track_task`, `/v1/tasks/{id}` SSE, audit/archive
jobs, admin views, reflection engine, etc.

**Invariants.**
- Exactly one `TaskState` per `TaskUnit` at any wall-clock instant.
- Terminal transitions are one-way: nothing leaves a terminal state.
- `RETRYING → RUNNING` is the only cycle; bounded by retry budget held
  outside this machine.

---

## 3. Machine B — Workflow / Step `status` (ephemeral, GM-owned)

**Purpose.** Progress tracking for a single invocation of the GM's
`coordinate_workflow` tool. A workflow is a small (≤ `_WF_MAX_STEPS`)
DAG of sibling host-agent calls, each step dispatching through a
`HostAgentWorker`. This machine exists *only* while the workflow
`asyncio.Task` is alive; it is never persisted.

**States.** Two nested levels, both simple enums encoded as strings:

```
Workflow-level
┌───────────────────────────────────────────────────────┐
│ pending → running → done                              │
│             ↓                                         │
│             ├─→ failed       (any step → failed)      │
│             └─→ cancelled    (cancel_event set + any  │
│                               step → cancelled)       │
└───────────────────────────────────────────────────────┘

Step-level
┌───────────────────────────────────────────────────────┐
│ pending → running → done                              │
│                    ↘                                  │
│                     failed                            │
│             ↓  (cancel_event before start)            │
│             ├─→ cancelled                             │
│             ├─→ skipped       (upstream failed)       │
│             └─→ cancelled     (in-flight cancel hits) │
└───────────────────────────────────────────────────────┘
```

Source of truth: `_WF_TERMINAL = {"done", "failed", "cancelled"}` and
`_STEP_TERMINAL = {"done", "failed", "skipped", "cancelled"}` in
`agent_workforce.py`.

**Writer.** The `_run_workflow()` coroutine launched by
`coordinate_workflow`. One writer per workflow — no shared state across
workflows. Transitions are made by plain assignment
(`step.status = "done"`) because the machine is single-threaded,
process-local, and not persisted. There is no `LEGAL_TRANSITIONS` table
and no exception type; we trust the single writer.

**Readers.** `track_workflow`, `cancel_workflow`, `coordinate_workflow`
itself (when `wait=True`).

**Invariants.**
- Workflow `status` is derived from its step statuses at finalize time:
  - any `cancelled` + cancel requested ⇒ `cancelled`
  - any `failed` (no cancel) ⇒ `failed`
  - otherwise ⇒ `done`
- Steps with unmet dependencies whose dependency ended in
  `{failed, cancelled, skipped}` transition to `skipped` without ever
  running. (Silent cascade — we do not raise.)
- `skipped` exists only at the step level. There is no workflow-level
  `skipped`.

---

## 4. Term collision table

Both machines use the same words for different ideas. Always qualify
with the machine name in logs, error messages, and UI.

| Word | `TaskState` (A) | Workflow/Step `status` (B) |
|---|---|---|
| `failed` | `TaskUnit` terminal: retries exhausted or fatal error in coordinator pipeline | Step hit an exception from `HostAgentWorker`, or wall-clock > `step_timeout_seconds`. Workflow `failed` = **any** step `failed` and not cancelled. |
| `cancelled` | ✗ (A has no `cancelled`; aborted tasks go to `FAILED` with reason) | `cancel_workflow` was called before the step finished, or a pending step was dropped when the workflow was cancelled. |
| `done` | ✗ (A uses `PERSISTED` for "fully complete") | Step returned a non-empty result text. Workflow `done` = every step ended in `{done, skipped}`, none `failed`, no cancel. |
| `running` | `RUNNING` — worker provider is executing the task | Step's `HostAgentWorker.execute()` is in-flight, or workflow's `_run_workflow` has started iterating its ready set. |
| `pending` | ✗ (A's equivalent pre-dispatch is `NORMALIZED` / `PLANNED`) | Step not yet scheduled — either waiting on dependencies or semaphore. |

Translation rule when bridging logs across machines:
*"workflow-failed" ≠ "TaskState.FAILED"*. A `coordinate_workflow`
invocation does not produce a `TaskUnit`; it is a **tool call** that
happens *inside* a GM task. If the GM task wraps a workflow that ended
`failed`, the GM can decide whether to retry, escalate via
`NEEDS_HUMAN`, or continue — but the GM's own `TaskState` is untouched
by B.

---

## 5. Relationship between A and B

```
┌──────────────────── Machine A (TaskState) ────────────────────┐
│  RECEIVED → NORMALIZED → PLANNED → DISPATCHED → RUNNING ──┐   │
│                                                           │   │
│                                                           ▼   │
│                          GM agent executes, may call:         │
│                                                               │
│              ┌──────── Machine B — one workflow ────────┐     │
│              │ pending → running → {done,failed,cancel} │     │
│              │   └─ per-step: pending → running → ...   │     │
│              └──────────────────────────────────────────┘     │
│                                                           ▼   │
│                                                       MERGING │
│                                                           ▼   │
│                                                       RESPONDED│
│                                                           ▼   │
│                                                       PERSISTED│
└───────────────────────────────────────────────────────────────┘
```

Key rules at the seam:

1. **B is scoped inside A.** A workflow exists for the duration of a
   tool-call frame within one GM task. Multiple workflows per task are
   allowed but unusual.
2. **B does not mutate A.** Step failures do not bubble up into
   `TaskState.FAILED`. The GM reads B's final snapshot and decides how
   to continue A. This keeps reasoning local: every TaskState
   transition has exactly one reason.
3. **A does not mutate B.** Cancelling a task at the A level
   (`cancel_task(task_id)`) does not automatically cancel in-flight
   workflows. The GM must explicitly call `cancel_workflow(wf_id)` in
   its shutdown handler. TODO (Stage C): auto-cancel B when A enters a
   terminal state.
4. **No shared IDs.** `TaskState`'s unit is `task_id` (uuid).
   Workflows use `wf-<hex12>` prefix (`_new_workflow_id`). The two
   namespaces never collide; mixing them in an API response is a bug.

### Legacy third machine (`dag/models.py`)

`DagNode.status` (`waiting / ready / started / completed / failed /
timeout`) and `DagGraph.status` (`initialized / running / completed /
failed / cancelled`) are used only by the workflow-preset pipeline
under `hubos.core/execution/orchestrator.py` + `hubos.core/workflow/`. That
path is invoked when a `TaskUnit` has `workflow_preset` set, and
executes entirely *inside* `TaskState.RUNNING`. Its terminology
overlaps with both A and B but it is an **implementation detail of the
Coordinator**, not a user-visible surface. When triaging a bug, first
decide which of the three machines the confused field belongs to — do
not attempt to align them.

---

## 6. Checklist when adding a new state or status

Before inventing a new state on either machine, confirm:

- [ ] It is strictly necessary — can an existing state + a metadata
      field carry the information instead?
- [ ] It belongs to exactly one machine; if it could arguably belong to
      either, it belongs to **A** (the persistent one) by default.
- [ ] For A: the new transition is added to `LEGAL_TRANSITIONS` AND
      unit tests in `tests/hubos.core/schemas/` cover both the happy path
      and the `InvalidStateTransitionError` path.
- [ ] For B: the new status is added to `_WF_TERMINAL` or
      `_STEP_TERMINAL` if it is terminal, AND the
      `_render_workflow_snapshot` / `_render_step_summary` renderers
      still return a consistent shape.
- [ ] No collision with a word already used on the other machine; if
      unavoidable, the term collision table in §4 is updated.
- [ ] Docs under `docs/architecture-*.md` reference the new state where
      relevant.

---

## 7. Open items deferred to Stage C

- Auto-cancel workflows (B) when the owning task (A) transitions to any
  terminal state. Requires a back-reference from `_WorkflowExecution` to
  its owning `task_id` and a cancellation hook in
  `Coordinator.transition_to_failed`.
- Persist workflow snapshots. B is currently memory-only; an audit-
  archive writer under `hubos.core/execution/` could persist a terminal
  snapshot blob when a workflow ends, keyed by the GM's `task_id`.
- Formalise B with a `LEGAL_TRANSITIONS` table once it becomes
  multi-writer (e.g. if we let retry workers mutate step status).
