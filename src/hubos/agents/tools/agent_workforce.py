# -*- coding: utf-8 -*-
"""Multi-agent fan-out tool for the General Manager agent.

``spawn_subagents`` lets the GM dispatch N independent prompts to N (possibly
different) sibling agents in this same process, in parallel, and collect
their structured results in a single deterministic response.

Concept (vs the existing ``runtime_delegate.delegate_task``):

* ``delegate_task``      → "kick off ONE structured workflow inside hubos.core"
                           (uses TaskStore / EventStore lifecycle, can be
                           tracked / cancelled by task_id)
* ``spawn_subagents``    → "RPC group-call: ask K sibling agents K questions,
                           wait for all answers, return summary"
                           (stateless, no task IDs, no DAG)

Backend: always in-process. Requires the host to have wired a HostAgentRunner
via ``hubos.core.workers.set_host_agent_runner(...)`` at startup. Returns a
structured error if not wired (does NOT raise — keeps LLM error path clean).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from uuid import uuid4

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from hubos.core.workers.providers.host_agent import HostAgentWorker
from hubos.core.workers.registry import get_host_agent_runner

from .runtime_delegate import _current_runtime_ctx

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = 120
_MAX_CONCURRENCY = 8


def _err(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _ok(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


async def spawn_subagents(
    assignments: list[dict[str, Any]],
    timeout_seconds: int = _DEFAULT_TIMEOUT,
    max_concurrency: int = _MAX_CONCURRENCY,
) -> ToolResponse:
    """Dispatch K prompts to K sibling agents in parallel and aggregate.

    Use when the user's request decomposes into INDEPENDENT sub-questions
    that can be answered by different specialists at the same time. Each
    sub-agent runs in its own Workspace (per-agent memory, per-agent tools)
    so this is true fan-out — not just K calls to one model.

    DO NOT use when:
      * sub-tasks have ordering dependencies → use ``coordinate_workflow``
      * a single agent can handle the whole thing → just answer / call its tool
      * the work is heavy/long-running → use ``delegate_task`` (tracked in
        hubos.core TaskStore so you can ``track_task`` / ``cancel_task`` later)

    Args:
        assignments: list of ``{"agent_id": str, "prompt": str,
            "label": Optional[str]}``. ``label`` is a free-form key the GM
            can use to identify each sub-result in its summary.
        timeout_seconds: per-subagent timeout. Default 120.
        max_concurrency: cap on simultaneous sub-agent runs. Default 8.

    Returns:
        ``ToolResponse`` text containing a JSON object::

            {
              "spawned": <int>,
              "succeeded": <int>,
              "failed": <int>,
              "elapsed_ms": <int>,
              "results": [
                {"label": ..., "agent_id": ..., "success": bool,
                 "content": str | null, "error": str | null,
                 "execution_time_ms": int}
              ]
            }
    """
    if not isinstance(assignments, list) or not assignments:
        return _err("spawn_subagents: 'assignments' must be a non-empty list")

    cleaned: list[tuple[str, str, str]] = []
    for i, a in enumerate(assignments):
        if not isinstance(a, dict):
            return _err(f"spawn_subagents: assignment[{i}] must be an object")
        agent_id = (a.get("agent_id") or "").strip()
        prompt = (a.get("prompt") or "").strip()
        if not agent_id or not prompt:
            return _err(
                f"spawn_subagents: assignment[{i}] needs both 'agent_id' and 'prompt'",
            )
        label = str(a.get("label") or f"sub_{i}")
        cleaned.append((agent_id, prompt, label))

    runner = get_host_agent_runner()
    if runner is None:
        return _err(
            "❌ No HostAgentRunner registered. The host application must call "
            "hubos.core.workers.set_host_agent_runner(...) at startup to enable "
            "spawn_subagents.",
        )

    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    ctx = _current_runtime_ctx()
    parent_session = ctx.get("session_id") or ""
    parent_user = ctx.get("user_id") or ""

    async def _run_one(
        agent_id: str,
        prompt: str,
        label: str,
    ) -> dict[str, Any]:
        worker = HostAgentWorker(agent_id=agent_id, runner=runner)
        unit_id = uuid4()
        sub_ctx = {
            "session_id": (
                f"{parent_session}::sub::{label}"
                if parent_session
                else f"hubos.core:sub:{label}:{unit_id.hex[:8]}"
            ),
            "user_id": parent_user,
            "channel": ctx.get("channel") or "hubos_core_subagent",
            "parent_session_id": parent_session,
            "label": label,
        }
        async with sem:
            try:
                res = await worker.execute(
                    unit_id=unit_id,
                    input_data={"prompt": prompt, "context": sub_ctx},
                    timeout_seconds=timeout_seconds,
                )
                return {
                    "label": label,
                    "agent_id": agent_id,
                    "success": True,
                    "content": res.data.get("content"),
                    "error": None,
                    "execution_time_ms": res.execution_time_ms,
                }
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                return {
                    "label": label,
                    "agent_id": agent_id,
                    "success": False,
                    "content": None,
                    "error": f"{type(e).__name__}: {e}",
                    "execution_time_ms": 0,
                }

    start = time.time()
    logger.info(
        "spawn_subagents: dispatching %d sub-agents (concurrency=%d, timeout=%ds, parent_session=%s)",
        len(cleaned),
        max_concurrency,
        timeout_seconds,
        parent_session,
    )

    results = await asyncio.gather(
        *(_run_one(aid, pr, lab) for aid, pr, lab in cleaned),
        return_exceptions=False,
    )
    elapsed_ms = int((time.time() - start) * 1000)

    succeeded = sum(1 for r in results if r["success"])
    failed = len(results) - succeeded

    payload = {
        "spawned": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "elapsed_ms": elapsed_ms,
        "results": results,
    }
    return _ok(json.dumps(payload, ensure_ascii=False, indent=2))


# ===========================================================================
# Ad-hoc workflow DAG: coordinate_workflow / track_workflow / cancel_workflow
# ===========================================================================
#
# A lightweight, in-process DAG runner for GM-level "3-8 step" ad-hoc plans.
# Independent from hubos.core.execution TaskStore (which hosts preset workflows
# invoked by delegate_task) — workflow_id uses a distinct "wf-" prefix.
#
# Step prompt can reference upstream results via:
#     {{step_<id>.result}}      -> the upstream step's response text
# Unknown placeholders are left intact so the LLM can see what was missing.

import re  # noqa: E402
import threading  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from uuid import uuid4 as _uuid4  # noqa: E402

_WF_TERMINAL = {"done", "failed", "cancelled"}
_STEP_TERMINAL = {"done", "failed", "skipped", "cancelled"}
_WF_DEFAULT_STEP_TIMEOUT = 120
_WF_MAX_STEPS = 25
_WF_MAX_CONCURRENCY = 8


@dataclass
class _WorkflowStep:
    id: str
    agent_id: str
    prompt: str
    depends_on: list[str] = field(default_factory=list)
    status: str = "pending"
    started_at: float | None = None
    finished_at: float | None = None
    result_text: str | None = None
    error: str | None = None
    execution_time_ms: int = 0


@dataclass
class _WorkflowExecution:
    workflow_id: str
    session_id: str
    user_id: str
    title: str
    created_at: float
    started_at: float | None
    finished_at: float | None
    status: str  # pending | running | done | failed | cancelled
    steps: dict[str, _WorkflowStep]
    summary_step_id: str | None
    cancel_event: asyncio.Event
    max_concurrency: int
    step_timeout_seconds: int
    error: str | None = None


_wf_lock = threading.Lock()
_workflows: dict[str, _WorkflowExecution] = {}


def _now() -> float:
    return time.time()


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _new_workflow_id() -> str:
    return f"wf-{_uuid4().hex[:12]}"


def _render_step_summary(step: _WorkflowStep) -> dict[str, Any]:
    return {
        "id": step.id,
        "agent_id": step.agent_id,
        "depends_on": list(step.depends_on),
        "status": step.status,
        "started_at": _iso(step.started_at),
        "finished_at": _iso(step.finished_at),
        "execution_time_ms": step.execution_time_ms,
        "error": step.error,
        "has_result": step.result_text is not None,
    }


def _render_workflow_snapshot(
    exec_: _WorkflowExecution,
    include_results: bool,
) -> dict[str, Any]:
    steps = []
    for sid in exec_.steps:
        step = exec_.steps[sid]
        payload = _render_step_summary(step)
        if include_results:
            payload["result"] = step.result_text
        steps.append(payload)
    final_text = None
    if exec_.summary_step_id and exec_.status == "done":
        final_step = exec_.steps.get(exec_.summary_step_id)
        if final_step and final_step.result_text:
            final_text = final_step.result_text
    return {
        "workflow_id": exec_.workflow_id,
        "title": exec_.title,
        "status": exec_.status,
        "error": exec_.error,
        "created_at": _iso(exec_.created_at),
        "started_at": _iso(exec_.started_at),
        "finished_at": _iso(exec_.finished_at),
        "summary_step_id": exec_.summary_step_id,
        "final_response": final_text,
        "steps": steps,
    }


_PLACEHOLDER_RE = re.compile(r"\{\{\s*step_([A-Za-z0-9_\-]+)\.result\s*\}\}")


def _expand_prompt(prompt: str, exec_: _WorkflowExecution) -> str:
    def _sub(match: "re.Match[str]") -> str:
        sid = match.group(1)
        step = exec_.steps.get(sid)
        if step is None or step.result_text is None:
            return match.group(0)  # leave unknown placeholder visible
        return step.result_text

    return _PLACEHOLDER_RE.sub(_sub, prompt)


def _validate_plan(
    steps_input: list[dict[str, Any]],
) -> tuple[dict[str, _WorkflowStep], str | None]:
    """Build the step dict + validate: unique ids, deps exist, no cycles, ≤limit."""
    if not isinstance(steps_input, list) or not steps_input:
        return {}, "'steps' must be a non-empty list"
    if len(steps_input) > _WF_MAX_STEPS:
        return {}, f"too many steps ({len(steps_input)} > {_WF_MAX_STEPS})"

    steps: dict[str, _WorkflowStep] = {}
    for i, raw in enumerate(steps_input):
        if not isinstance(raw, dict):
            return {}, f"step[{i}] must be an object"
        sid = str(raw.get("id") or "").strip()
        agent_id = str(raw.get("agent_id") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        deps = raw.get("depends_on") or []
        if not sid or not agent_id or not prompt:
            return {}, f"step[{i}] needs non-empty 'id', 'agent_id', 'prompt'"
        if not re.fullmatch(r"[A-Za-z0-9_\-]+", sid):
            return {}, f"step[{i}].id {sid!r} must match [A-Za-z0-9_-]+"
        if sid in steps:
            return {}, f"duplicate step id: {sid!r}"
        if not isinstance(deps, list) or not all(
            isinstance(d, str) for d in deps
        ):
            return {}, f"step[{i}].depends_on must be list[str]"
        steps[sid] = _WorkflowStep(
            id=sid,
            agent_id=agent_id,
            prompt=prompt,
            depends_on=list(deps),
        )

    # All deps resolvable
    for sid, step in steps.items():
        for d in step.depends_on:
            if d not in steps:
                return {}, f"step {sid!r} depends on missing step {d!r}"
            if d == sid:
                return {}, f"step {sid!r} depends on itself"

    # Topological sort / cycle detection
    visited: dict[str, int] = {}  # 0=unseen,1=visiting,2=done

    def _dfs(sid: str) -> str | None:
        state = visited.get(sid, 0)
        if state == 1:
            return f"cycle detected involving step {sid!r}"
        if state == 2:
            return None
        visited[sid] = 1
        for d in steps[sid].depends_on:
            err = _dfs(d)
            if err:
                return err
        visited[sid] = 2
        return None

    for sid in steps:
        err = _dfs(sid)
        if err:
            return {}, err

    return steps, None


async def _run_step(
    exec_: _WorkflowExecution,
    step: _WorkflowStep,
    runner: "Any",
    sem: asyncio.Semaphore,
) -> None:
    """Execute a single step. Updates step.status + related fields in place."""
    if exec_.cancel_event.is_set():
        step.status = "cancelled"
        step.finished_at = _now()
        return

    # Skip if any required dep ended non-ok
    for d in step.depends_on:
        dep = exec_.steps.get(d)
        if dep is None or dep.status != "done":
            step.status = "skipped"
            step.error = f"upstream {d!r} ended with status {dep.status if dep else 'missing'}"
            step.finished_at = _now()
            return

    expanded_prompt = _expand_prompt(step.prompt, exec_)
    worker = HostAgentWorker(agent_id=step.agent_id, runner=runner)
    unit_id = _uuid4()
    sub_ctx = {
        "session_id": f"{exec_.session_id}::wf::{exec_.workflow_id}::{step.id}",
        "user_id": exec_.user_id,
        "channel": "hubos_core_workflow",
        "workflow_id": exec_.workflow_id,
        "step_id": step.id,
    }

    step.started_at = _now()
    step.status = "running"

    async with sem:
        if exec_.cancel_event.is_set():
            step.status = "cancelled"
            step.finished_at = _now()
            return
        try:
            res = await worker.execute(
                unit_id=unit_id,
                input_data={"prompt": expanded_prompt, "context": sub_ctx},
                timeout_seconds=exec_.step_timeout_seconds,
            )
            step.result_text = res.data.get("content")
            step.status = "done"
            step.execution_time_ms = res.execution_time_ms
        except asyncio.CancelledError:
            step.status = "cancelled"
            raise
        except Exception as e:  # noqa: BLE001
            step.status = "failed"
            step.error = f"{type(e).__name__}: {e}"
        finally:
            step.finished_at = _now()


async def _run_workflow(exec_: _WorkflowExecution, runner: "Any") -> None:
    """Drive the DAG until all steps are terminal or cancellation fires."""
    exec_.started_at = _now()
    exec_.status = "running"
    sem = asyncio.Semaphore(max(1, exec_.max_concurrency))

    try:
        while True:
            if exec_.cancel_event.is_set():
                # Mark still-pending steps as cancelled
                for s in exec_.steps.values():
                    if s.status == "pending":
                        s.status = "cancelled"
                        s.finished_at = _now()
                break

            # All terminal?
            if all(s.status in _STEP_TERMINAL for s in exec_.steps.values()):
                break

            # Collect ready steps
            ready = [
                s
                for s in exec_.steps.values()
                if s.status == "pending"
                and all(
                    exec_.steps[d].status != "pending" for d in s.depends_on
                )
            ]
            if not ready:
                # No ready steps → wait briefly for running ones
                await asyncio.sleep(0.02)
                continue

            # Dispatch all ready in parallel. _run_step is robust — never raises.
            await asyncio.gather(
                *(_run_step(exec_, s, runner, sem) for s in ready),
                return_exceptions=False,
            )

        # Finalize
        if exec_.cancel_event.is_set() and any(
            s.status == "cancelled" for s in exec_.steps.values()
        ):
            exec_.status = "cancelled"
        elif any(s.status == "failed" for s in exec_.steps.values()):
            exec_.status = "failed"
            exec_.error = "one or more steps failed"
        else:
            exec_.status = "done"
    except asyncio.CancelledError:
        exec_.status = "cancelled"
        for s in exec_.steps.values():
            if s.status in {"pending", "running"}:
                s.status = "cancelled"
                s.finished_at = _now()
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("workflow %s crashed unexpectedly", exec_.workflow_id)
        exec_.status = "failed"
        exec_.error = f"runner crashed: {type(e).__name__}: {e}"
    finally:
        exec_.finished_at = _now()


async def coordinate_workflow(
    steps: list[dict[str, Any]],
    title: str = "",
    summary_step_id: str | None = None,
    wait: bool = True,
    timeout_seconds: int = 600,
    step_timeout_seconds: int = _WF_DEFAULT_STEP_TIMEOUT,
    max_concurrency: int = _WF_MAX_CONCURRENCY,
) -> ToolResponse:
    """Coordinate a multi-step DAG of sibling host agents.

    Use for NON-TRIVIAL multi-agent plans with ORDERING / DEPENDENCIES between
    sub-tasks, e.g. "research → (draft + fact-check) in parallel → final
    summary". For flat fan-out without ordering, use ``spawn_subagents``.
    For a single preset workflow inside hubos.core, use ``delegate_task``.

    Prompt templating: any step's ``prompt`` can reference the finished text
    of an upstream step via ``{{step_<id>.result}}``. Placeholders for
    unknown / not-yet-finished steps are left verbatim.

    A step is SKIPPED (not failed) if any of its dependencies ended in a
    non-``done`` state; downstream skipped steps cascade.

    Args:
        steps: list of ``{"id": str, "agent_id": str, "prompt": str,
            "depends_on": Optional[list[str]]}``. Max 25 steps.
        title: free-form label shown in track_workflow output.
        summary_step_id: if set and the workflow ends ``done``, this step's
            ``result`` becomes ``final_response`` in ``track_workflow``.
        wait: block until the workflow reaches a terminal state. Default True.
        timeout_seconds: max seconds to wait when ``wait=True``. Default 600.
        step_timeout_seconds: per-step timeout. Default 120.
        max_concurrency: cap on simultaneously-running steps. Default 8.

    Returns:
        ``ToolResponse`` with a workflow snapshot JSON; always contains
        ``workflow_id`` (prefix ``wf-``) for subsequent track_workflow /
        cancel_workflow calls.
    """
    runner = get_host_agent_runner()
    if runner is None:
        return _err(
            "❌ No HostAgentRunner registered. The host application must call "
            "hubos.core.workers.set_host_agent_runner(...) at startup to enable "
            "coordinate_workflow.",
        )

    step_map, err = _validate_plan(steps)
    if err:
        return _err(f"coordinate_workflow: {err}")

    if summary_step_id is not None and summary_step_id not in step_map:
        return _err(
            f"coordinate_workflow: summary_step_id {summary_step_id!r} not in steps",
        )

    ctx = _current_runtime_ctx()
    wf_id = _new_workflow_id()
    exec_ = _WorkflowExecution(
        workflow_id=wf_id,
        session_id=ctx.get("session_id") or "",
        user_id=ctx.get("user_id") or "",
        title=title or "(untitled workflow)",
        created_at=_now(),
        started_at=None,
        finished_at=None,
        status="pending",
        steps=step_map,
        summary_step_id=summary_step_id,
        cancel_event=asyncio.Event(),
        max_concurrency=max(1, int(max_concurrency)),
        step_timeout_seconds=max(1, int(step_timeout_seconds)),
    )
    with _wf_lock:
        _workflows[wf_id] = exec_

    run_task = asyncio.create_task(
        _run_workflow(exec_, runner),
        name=f"hubos.core-workflow-{wf_id}",
    )
    # Hold a reference on the execution so track/cancel can see it.
    exec_._run_task = run_task  # type: ignore[attr-defined]

    logger.info(
        "coordinate_workflow started: id=%s steps=%d session=%s wait=%s",
        wf_id,
        len(step_map),
        exec_.session_id,
        wait,
    )

    if not wait:
        return _ok(
            json.dumps(
                {
                    "workflow_id": wf_id,
                    "status": "running",
                    "steps_total": len(step_map),
                    "tip": f'Use track_workflow("{wf_id}") to check progress.',
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    try:
        await asyncio.wait_for(
            asyncio.shield(run_task),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        snap = _render_workflow_snapshot(exec_, include_results=False)
        snap["note"] = (
            f"⏰ Wait timed out after {timeout_seconds}s; workflow is still "
            f'running. Use track_workflow("{wf_id}") later.'
        )
        return _ok(json.dumps(snap, ensure_ascii=False, indent=2))

    return _ok(
        json.dumps(
            _render_workflow_snapshot(exec_, include_results=True),
            ensure_ascii=False,
            indent=2,
        ),
    )


async def track_workflow(
    workflow_id: str,
    follow: bool = False,
    timeout_seconds: int = 300,
    include_results: bool = True,
) -> ToolResponse:
    """Inspect a workflow's status and step-level progress.

    Args:
        workflow_id: id returned by ``coordinate_workflow``.
        follow: if True and workflow is not terminal, block until it is (or
            ``timeout_seconds`` elapses). Default False (single snapshot).
        timeout_seconds: max seconds to wait when ``follow=True``. Default 300.
        include_results: if True, include step.result text in the snapshot
            (can be large). Default True.

    Returns:
        ``ToolResponse`` with workflow snapshot JSON; same shape as
        ``coordinate_workflow``'s return value.
    """
    if not workflow_id or not workflow_id.strip():
        return _err("track_workflow: workflow_id is required")

    exec_ = _workflows.get(workflow_id.strip())
    if exec_ is None:
        return _err(f"❌ workflow not found: {workflow_id}")

    # Owner check
    ctx = _current_runtime_ctx()
    if (
        exec_.user_id
        and ctx.get("user_id")
        and ctx["user_id"] != exec_.user_id
    ):
        return _err(
            f"❌ workflow {workflow_id} belongs to a different user; access denied",
        )

    if exec_.status in _WF_TERMINAL or not follow:
        return _ok(
            json.dumps(
                _render_workflow_snapshot(exec_, include_results),
                ensure_ascii=False,
                indent=2,
            ),
        )

    run_task = getattr(exec_, "_run_task", None)
    if run_task is None:
        # Fallback: poll
        deadline = asyncio.get_event_loop().time() + timeout_seconds
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.05)
            if exec_.status in _WF_TERMINAL:
                break
        return _ok(
            json.dumps(
                _render_workflow_snapshot(exec_, include_results),
                ensure_ascii=False,
                indent=2,
            ),
        )

    try:
        await asyncio.wait_for(
            asyncio.shield(run_task),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        snap = _render_workflow_snapshot(exec_, include_results)
        snap[
            "note"
        ] = f"⏰ follow timed out after {timeout_seconds}s; still running."
        return _ok(json.dumps(snap, ensure_ascii=False, indent=2))

    return _ok(
        json.dumps(
            _render_workflow_snapshot(exec_, include_results),
            ensure_ascii=False,
            indent=2,
        ),
    )


async def cancel_workflow(workflow_id: str) -> ToolResponse:
    """Cancel a running workflow cooperatively.

    Sets the cancel flag; pending steps are marked ``cancelled`` immediately,
    in-flight steps finish their current sub-agent call (cancellation of the
    sub-call is best-effort via ``asyncio.Task.cancel``). Already-terminal
    workflows return their last snapshot unchanged.

    Args:
        workflow_id: id returned by ``coordinate_workflow``.

    Returns:
        ``ToolResponse`` with workflow snapshot JSON at the moment of cancel.
    """
    if not workflow_id or not workflow_id.strip():
        return _err("cancel_workflow: workflow_id is required")

    exec_ = _workflows.get(workflow_id.strip())
    if exec_ is None:
        return _err(f"❌ workflow not found: {workflow_id}")

    ctx = _current_runtime_ctx()
    if (
        exec_.user_id
        and ctx.get("user_id")
        and ctx["user_id"] != exec_.user_id
    ):
        return _err(
            f"❌ workflow {workflow_id} belongs to a different user; access denied",
        )

    if exec_.status in _WF_TERMINAL:
        return _ok(
            json.dumps(
                {
                    "workflow_id": workflow_id,
                    "status": exec_.status,
                    "note": "already terminal; nothing to cancel.",
                    "snapshot": _render_workflow_snapshot(
                        exec_,
                        include_results=False,
                    ),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    exec_.cancel_event.set()
    run_task = getattr(exec_, "_run_task", None)
    if run_task is not None and not run_task.done():
        run_task.cancel()

    # Give the runner a tick to propagate the cancel into step state.
    try:
        await asyncio.wait_for(
            asyncio.shield(run_task) if run_task else asyncio.sleep(0.05),
            timeout=2,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        pass

    return _ok(
        json.dumps(
            {
                "workflow_id": workflow_id,
                "status": exec_.status,
                "note": "cancel signalled",
                "snapshot": _render_workflow_snapshot(
                    exec_,
                    include_results=False,
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


__all__ = [
    "spawn_subagents",
    "coordinate_workflow",
    "track_workflow",
    "cancel_workflow",
]
