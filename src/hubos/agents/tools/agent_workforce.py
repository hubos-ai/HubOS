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
import contextlib
import json
import logging
import time
from collections import Counter, deque
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from hubos.core.workers.providers.host_agent import HostAgentWorker
from hubos.core.workers.registry import get_host_agent_runner

from hubos.app.task_monitor import TaskEventType, TaskStatus
from hubos.app.task_monitor_helpers import (
    register_cancel_handler,
    safe_add_event,
    safe_create_task,
    safe_update_task,
    unregister_cancel_handler,
)

from .runtime_delegate import _current_runtime_ctx

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = 120
_MAX_CONCURRENCY = 8
_PARENT_RUN_HEARTBEAT_SECONDS = 30.0
_USER_PROGRESS_INITIAL_SECONDS = 30.0
_USER_PROGRESS_INTERVAL_SECONDS = 120.0
_PROGRESS_CHANNELS = {"feishu", "weixin", "wecom"}


def _get_task_modes_config() -> Any:
    """Load task_modes config for the current agent."""
    try:
        from hubos.config.config import load_agent_config

        cfg = load_agent_config("default")
        return cfg.task_modes
    except Exception:
        return None


def _err(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _ok(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _nested_dispatch_allowed(ctx: dict[str, Any]) -> bool:
    value = ctx.get("allow_nested_delegate")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _reject_nested_orchestration(tool_name: str) -> ToolResponse | None:
    """Block delegated sub-agents from recursively orchestrating siblings."""
    ctx = _current_runtime_ctx()
    if not ctx.get("parent_session_id"):
        return None
    if _nested_dispatch_allowed(ctx):
        return None
    agent_id = str(ctx.get("agent_id") or "sub-agent")
    return _err(
        f"{tool_name}: nested orchestration is disabled for delegated "
        f"sub-agents (current agent={agent_id}). Execute directly or return "
        "a concise reroute suggestion to the parent agent.",
    )


def _format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    if minutes <= 0:
        return f"{sec} 秒"
    return f"{minutes} 分 {sec} 秒"


def _shorten(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _domain_from_url(value: str) -> str:
    try:
        parsed = urlparse(value)
        return parsed.netloc or value
    except Exception:  # noqa: BLE001
        return value


def _summarize_assignments(
    cleaned: list[tuple[str, str, str]],
    child_tasks: list[asyncio.Task[dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    running: list[str] = []
    done: list[str] = []
    for idx, (agent_id, _prompt, label) in enumerate(cleaned):
        name = f"{label}/{agent_id}" if label else agent_id
        if idx < len(child_tasks) and child_tasks[idx].done():
            done.append(name)
        else:
            running.append(name)
    return running, done


def _completed_result_summaries(
    cleaned: list[tuple[str, str, str]],
    child_tasks: list[asyncio.Task[dict[str, Any]]],
) -> list[str]:
    summaries: list[str] = []
    for idx, (agent_id, _prompt, label) in enumerate(cleaned):
        if idx >= len(child_tasks):
            continue
        task = child_tasks[idx]
        if not task.done() or task.cancelled():
            continue
        try:
            result = task.result()
        except Exception:  # noqa: BLE001
            continue
        if not result.get("success"):
            continue
        content = _shorten(result.get("content"), limit=140)
        if content:
            name = f"{label}/{agent_id}" if label else agent_id
            summaries.append(f"{name}：{content}")
    return summaries[:2]


def _extract_tool_work_hint(tool_input: Any) -> str:
    if not isinstance(tool_input, dict):
        return ""

    for key in ("search_query", "query", "q", "keyword", "keywords"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return f"检索“{_shorten(value, 70)}”"
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str) and first.strip():
                return f"检索“{_shorten(first, 70)}”"

    urls = tool_input.get("urls") or tool_input.get("url")
    if isinstance(urls, str) and urls.strip():
        return f"读取 {_shorten(_domain_from_url(urls), 70)}"
    if isinstance(urls, list) and urls:
        domains = [
            _domain_from_url(str(url))
            for url in urls[:3]
            if str(url or "").strip()
        ]
        if domains:
            return f"读取 {', '.join(domains)}"

    path = tool_input.get("path") or tool_input.get("file_path")
    if isinstance(path, str) and path.strip():
        return f"查看文件 {_shorten(path, 70)}"

    return ""


def _classify_tool_work(tool_name: str, tool_input: Any) -> str:
    name = (tool_name or "").lower()
    if any(
        token in name
        for token in ("search", "google", "bing", "glm", "minimax")
    ):
        return "search"
    if any(
        token in name for token in ("extract", "browser", "crawl", "read_url")
    ):
        return "read_web"
    if any(token in name for token in ("file", "read", "view_text")):
        return "read_file"
    if any(token in name for token in ("write", "edit", "save")):
        return "write"
    if isinstance(tool_input, dict):
        if tool_input.get("urls") or tool_input.get("url"):
            return "read_web"
        if tool_input.get("path") or tool_input.get("file_path"):
            return "read_file"
        if any(
            tool_input.get(key)
            for key in ("search_query", "query", "q", "keyword", "keywords")
        ):
            return "search"
    return "work"


def _format_work_counts(counts: Counter) -> str:
    labels = {
        "search": "相关搜索",
        "read_web": "网页/资料阅读",
        "read_file": "文件查看",
        "write": "整理写入",
        "work": "分析处理",
    }
    ordered_keys = ("search", "read_web", "read_file", "write", "work")
    parts = [
        f"{labels[key]} {counts[key]} 次"
        for key in ordered_keys
        if counts.get(key, 0) > 0
    ]
    return "，".join(parts)


def _read_subagent_audit_records(
    parent_session: str,
    max_lines: int = 240,
) -> list[dict[str, Any]]:
    if not parent_session:
        return []
    try:
        from hubos.integrations.host_agent_runner import _audit_log_path

        path = _audit_log_path(parent_session)
        if not path.is_file():
            return []
        lines: deque[str] = deque(maxlen=max_lines)
        with open(path, encoding="utf-8") as file:
            for line in file:
                lines.append(line)
        records: list[dict[str, Any]] = []
        for line in lines:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
        return records
    except Exception:  # noqa: BLE001
        logger.debug("sub-agent audit read failed", exc_info=True)
        return []


def _audit_work_summary(parent_session: str) -> str:
    records = _read_subagent_audit_records(parent_session)
    if not records:
        return ""

    per_agent: dict[str, dict[str, Any]] = {}
    for record in records:
        agent_id = str(record.get("agent_id") or "").strip()
        if not agent_id:
            continue
        info = per_agent.setdefault(
            agent_id,
            {
                "work_counts": Counter(),
                "hints": [],
                "finished": False,
                "error": "",
            },
        )
        event = record.get("event")
        if event == "subagent_tool_use":
            tool_name = str(record.get("name") or "tool")
            tool_input = record.get("input")
            work_type = _classify_tool_work(tool_name, tool_input)
            info["work_counts"][work_type] += 1
            hint = _extract_tool_work_hint(tool_input)
            if hint and hint not in info["hints"]:
                info["hints"].append(hint)
        elif event == "subagent_finished":
            info["finished"] = True
            info["error"] = str(record.get("error") or "")

    phrases: list[str] = []
    for agent_id, info in per_agent.items():
        work_counts: Counter = info["work_counts"]
        total_work = sum(work_counts.values())
        if total_work <= 0:
            continue
        work_count_text = _format_work_counts(work_counts)
        hints = info["hints"][:2]
        phrase = f"{agent_id} 已完成 {work_count_text or f'{total_work} 步处理'}"
        if hints:
            phrase += "，" + "；".join(hints)
        phrases.append(_shorten(phrase, limit=180))

    if not phrases:
        return ""
    return "已执行：" + "；".join(phrases[:2]) + "。"


def _build_work_progress_summary(
    *,
    parent_session: str,
    cleaned: list[tuple[str, str, str]],
    child_tasks: list[asyncio.Task[dict[str, Any]]],
) -> str:
    lines: list[str] = []
    completed = _completed_result_summaries(cleaned, child_tasks)
    if completed:
        lines.append("已完成结果：" + "；".join(completed) + "。")
    audit_summary = _audit_work_summary(parent_session)
    if audit_summary:
        lines.append(audit_summary)
    return "\n".join(lines)


async def _touch_parent_run(reason: str) -> bool:
    """Best-effort heartbeat for the outer channel TaskTracker run."""
    try:
        from hubos.app.channels.delivery_context import (
            get_current_delivery_context,
        )

        delivery_ctx = get_current_delivery_context()
        if (
            delivery_ctx is None
            or delivery_ctx.task_tracker is None
            or not delivery_ctx.run_key
        ):
            return False
        touch = getattr(delivery_ctx.task_tracker, "touch", None)
        if touch is None:
            return False
        return bool(await touch(delivery_ctx.run_key, reason=reason))
    except Exception:  # noqa: BLE001
        logger.debug("parent run heartbeat failed", exc_info=True)
        return False


async def _send_user_progress_summary(text: str) -> bool:
    """Send a short progress message to external IM channels, if available."""
    try:
        from agentscope_runtime.engine.schemas.agent_schemas import (
            ContentType,
            TextContent,
        )
        from hubos.app.channels.delivery_context import (
            get_current_delivery_context,
        )

        delivery_ctx = get_current_delivery_context()
        if delivery_ctx is None:
            return False
        channel = (delivery_ctx.channel or "").lower()
        if channel not in _PROGRESS_CHANNELS:
            return False
        await delivery_ctx.send_progress_parts(
            [TextContent(type=ContentType.TEXT, text=text)],
            min_interval_seconds=_USER_PROGRESS_INTERVAL_SECONDS,
        )
        return True
    except Exception:  # noqa: BLE001
        logger.debug("user progress summary send failed", exc_info=True)
        return False


async def _spawn_progress_loop(
    *,
    parent_session: str,
    cleaned: list[tuple[str, str, str]],
    child_tasks: list[asyncio.Task[dict[str, Any]]],
    started_at: float,
) -> None:
    """Keep the parent run alive while sub-agents work, with sparse IM updates."""
    first_user_notice_sent = False
    last_user_notice_at = started_at

    while True:
        pending = [task for task in child_tasks if not task.done()]
        if not pending:
            return

        await _touch_parent_run("spawn_subagents waiting for child agents")

        now = time.time()
        elapsed = now - started_at
        should_send_initial = (
            not first_user_notice_sent
            and elapsed >= _USER_PROGRESS_INITIAL_SECONDS
        )
        should_send_followup = (
            first_user_notice_sent
            and now - last_user_notice_at >= _USER_PROGRESS_INTERVAL_SECONDS
        )

        if should_send_initial or should_send_followup:
            running, done = _summarize_assignments(cleaned, child_tasks)
            running_preview = "、".join(running[:3]) or "子任务"
            if len(running) > 3:
                running_preview += f" 等 {len(running)} 个子任务"
            done_part = f"，已完成 {len(done)} 个" if done else ""
            text = (
                "我还在处理这项任务。"
                f"已运行 {_format_elapsed(elapsed)}，"
                f"当前等待 {len(running)}/{len(cleaned)} 个子任务："
                f"{running_preview}{done_part}。"
            )
            work_summary = _build_work_progress_summary(
                parent_session=parent_session,
                cleaned=cleaned,
                child_tasks=child_tasks,
            )
            if work_summary:
                text += "\n" + work_summary
            if await _send_user_progress_summary(text):
                first_user_notice_sent = True
                last_user_notice_at = now

        await asyncio.sleep(_PARENT_RUN_HEARTBEAT_SECONDS)


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
            "label": Optional[str], "context": Optional[str],
            "constraints": Optional[list[str]],
            "artifacts": Optional[list[str]]}``. ``context`` carries facts
            already known by the parent; constraints and artifacts make the
            handoff explicit instead of forcing the child to rediscover them.
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
    nested_error = _reject_nested_orchestration("spawn_subagents")
    if nested_error is not None:
        return nested_error

    if not isinstance(assignments, list) or not assignments:
        return _err("spawn_subagents: 'assignments' must be a non-empty list")

    # Apply task_modes config: configured values are treated as minimum
    # guarantees so the user setting always takes effect over the LLM's
    # passed parameter (which may be a short default like 120s).
    tm = _get_task_modes_config()
    if tm is not None:
        sc = tm.spawn_subagents
        max_concurrency = min(max_concurrency, sc.max_concurrency)
        timeout_seconds = max(timeout_seconds, sc.timeout_seconds)
        if len(assignments) > sc.max_subagents:
            return _err(
                f"spawn_subagents: {len(assignments)} assignments exceeds "
                f"max_subagents limit ({sc.max_subagents})",
            )
        if not sc.allow_nesting:
            ctx = _current_runtime_ctx()
            if ctx.get("parent_session_id"):
                return _err(
                    "spawn_subagents: nesting not allowed by task mode config",
                )

    cleaned: list[tuple[str, str, str]] = []
    handoffs: list[dict[str, Any]] = []
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
        handoffs.append(
            {
                "objective": prompt,
                "known_context": str(a.get("context") or "").strip(),
                "constraints": [
                    str(item)
                    for item in (a.get("constraints") or [])
                    if str(item).strip()
                ][:12],
                "artifacts": [
                    str(item)
                    for item in (a.get("artifacts") or [])
                    if str(item).strip()
                ][:12],
            },
        )

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
    parent_workspace_dir = ctx.get("workspace_dir") or ""

    # -- TaskMonitor: create monitoring task --------------------------------
    _agent_labels = [c[2] for c in cleaned]
    _monitor_title = f"spawn_subagents: {', '.join(_agent_labels[:3])}{'…' if len(_agent_labels) > 3 else ''}"
    _monitor_task_id = await safe_create_task(
        session_id=parent_session or "unknown",
        source="tool",
        title=_monitor_title,
        tool_name="spawn_subagents",
    )
    await safe_update_task(_monitor_task_id, status=TaskStatus.RUNNING)
    cancel_event = asyncio.Event()
    child_tasks: list[asyncio.Task[dict[str, Any]]] = []

    def _cancel_spawn() -> None:
        cancel_event.set()
        for task in child_tasks:
            if not task.done():
                task.cancel()

    register_cancel_handler(_monitor_task_id, _cancel_spawn)

    # -- RunControl: register for unified cancel ----------------------------
    _spawn_run_id: str | None = None
    try:
        from hubos.app.run_control import (
            get_run_control_store,
            RunEntry,
            RunType,
            get_current_run_id,
        )

        _parent_run_id = get_current_run_id()
        _spawn_run_id = await get_run_control_store().register(
            RunEntry(
                run_id="",
                run_type=RunType.SPAWN,
                session_id=parent_session or "unknown",
                monitor_task_id=_monitor_task_id,
                parent_run_id=_parent_run_id,
            ),
        )
    except Exception:  # noqa: BLE001
        pass

    async def _run_one(
        agent_id: str,
        prompt: str,
        label: str,
        handoff: dict[str, Any],
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
            "parent_workspace_dir": parent_workspace_dir,
            "handoff": handoff,
            "label": label,
        }
        step_start = time.time()
        if cancel_event.is_set():
            return {
                "label": label,
                "agent_id": agent_id,
                "success": False,
                "content": None,
                "error": "cancelled",
                "execution_time_ms": 0,
            }
        await safe_add_event(
            _monitor_task_id,
            TaskEventType.STAGE_STARTED,
            f"Starting subagent: {agent_id}",
            stage=label,
            agent_id=agent_id,
        )
        async with sem:
            if cancel_event.is_set():
                return {
                    "label": label,
                    "agent_id": agent_id,
                    "success": False,
                    "content": None,
                    "error": "cancelled",
                    "execution_time_ms": 0,
                }
            try:
                res = await worker.execute(
                    unit_id=unit_id,
                    input_data={"prompt": prompt, "context": sub_ctx},
                    timeout_seconds=timeout_seconds,
                )
                step_ms = int((time.time() - step_start) * 1000)
                await safe_add_event(
                    _monitor_task_id,
                    TaskEventType.STAGE_COMPLETED,
                    f"Subagent {agent_id} completed ({step_ms}ms)",
                    stage=label,
                    agent_id=agent_id,
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
                await safe_add_event(
                    _monitor_task_id,
                    TaskEventType.TASK_CANCELLED,
                    f"Subagent {agent_id} cancelled",
                    stage=label,
                    agent_id=agent_id,
                )
                raise
            except Exception as e:  # noqa: BLE001
                step_ms = int((time.time() - step_start) * 1000)
                await safe_add_event(
                    _monitor_task_id,
                    TaskEventType.ERROR,
                    f"Subagent {agent_id} failed: {type(e).__name__}: {e} ({step_ms}ms)",
                    stage=label,
                    agent_id=agent_id,
                )
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

    child_tasks = [
        asyncio.create_task(
            _run_one(aid, pr, lab, handoff),
            name=f"hubos.spawn-subagent-{lab}",
        )
        for (aid, pr, lab), handoff in zip(cleaned, handoffs)
    ]
    progress_task = asyncio.create_task(
        _spawn_progress_loop(
            parent_session=parent_session,
            cleaned=cleaned,
            child_tasks=child_tasks,
            started_at=start,
        ),
        name="hubos.spawn-subagents-progress",
    )
    was_cancelled = False
    try:
        results = await asyncio.gather(
            *child_tasks,
            return_exceptions=False,
        )
    except asyncio.CancelledError:
        was_cancelled = True
        if not cancel_event.is_set():
            await safe_update_task(
                _monitor_task_id,
                status=TaskStatus.CANCELLED,
                result_summary="spawn_subagents cancelled",
            )
            unregister_cancel_handler(_monitor_task_id)
            # RunControl: mark cancelled
            if _spawn_run_id:
                try:
                    from hubos.app.run_control import get_run_control_store

                    await get_run_control_store().update_status(
                        _spawn_run_id,
                        "cancelled",
                    )
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "spawn: RunControl status update (cancelled) failed for %s",
                        _spawn_run_id,
                    )

        elapsed_ms = int((time.time() - start) * 1000)
        results = []
        for i, task in enumerate(child_tasks):
            agent_id, _prompt, label = cleaned[i]
            if task.done() and not task.cancelled():
                try:
                    results.append(task.result())
                    continue
                except Exception as e:  # noqa: BLE001
                    results.append(
                        {
                            "label": label,
                            "agent_id": agent_id,
                            "success": False,
                            "content": None,
                            "error": f"{type(e).__name__}: {e}",
                            "execution_time_ms": 0,
                        },
                    )
                    continue
            results.append(
                {
                    "label": label,
                    "agent_id": agent_id,
                    "success": False,
                    "content": None,
                    "error": "cancelled",
                    "execution_time_ms": 0,
                },
            )
        await safe_update_task(
            _monitor_task_id,
            status=TaskStatus.CANCELLED,
            progress=100,
            result_summary=f"cancelled after {elapsed_ms}ms",
        )
        payload = {
            "spawned": len(results),
            "succeeded": sum(1 for r in results if r["success"]),
            "failed": sum(1 for r in results if not r["success"]),
            "elapsed_ms": elapsed_ms,
            "cancelled": True,
            "results": results,
        }
        unregister_cancel_handler(_monitor_task_id)
        # RunControl: mark cancelled
        if _spawn_run_id:
            try:
                from hubos.app.run_control import get_run_control_store

                await get_run_control_store().update_status(
                    _spawn_run_id,
                    "cancelled",
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "spawn: RunControl status update failed for %s",
                    _spawn_run_id,
                )
    finally:
        if progress_task:
            if not progress_task.done():
                progress_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await progress_task
            else:
                with contextlib.suppress(Exception):
                    progress_task.result()
    elapsed_ms = int((time.time() - start) * 1000)

    succeeded = sum(1 for r in results if r["success"])
    failed = len(results) - succeeded

    # -- TaskMonitor: finalize ----------------------------------------------
    if was_cancelled:
        _final_status = TaskStatus.CANCELLED
    else:
        _final_status = TaskStatus.DONE if failed == 0 else TaskStatus.FAILED
    _summary = (
        f"succeeded={succeeded}, failed={failed}, elapsed={elapsed_ms}ms"
    )
    await safe_update_task(
        _monitor_task_id,
        status=_final_status,
        progress=100,
        result_summary=_summary,
    )
    unregister_cancel_handler(_monitor_task_id)

    # RunControl: mark done/failed
    if _spawn_run_id:
        try:
            from hubos.app.run_control import get_run_control_store

            await get_run_control_store().update_status(
                _spawn_run_id,
                "done" if failed == 0 else "failed",
            )
        except Exception:  # noqa: BLE001
            pass

    payload = {
        "spawned": len(results),
        "succeeded": succeeded,
        "failed": failed,
        "elapsed_ms": elapsed_ms,
        "results": results,
    }
    if was_cancelled:
        payload["cancelled"] = True
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
    known_context: str = ""
    constraints: list[str] = field(default_factory=list)
    artifacts: list[str] = field(default_factory=list)
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
    workspace_dir: str
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
            known_context=str(raw.get("context") or "").strip(),
            constraints=[
                str(item)
                for item in (raw.get("constraints") or [])
                if str(item).strip()
            ][:12],
            artifacts=[
                str(item)
                for item in (raw.get("artifacts") or [])
                if str(item).strip()
            ][:12],
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
    monitor_task_id: str | None = None,
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
        "parent_session_id": exec_.session_id,
        "parent_workspace_dir": exec_.workspace_dir,
        "handoff": {
            "objective": expanded_prompt,
            "known_context": step.known_context,
            "constraints": list(step.constraints),
            "artifacts": list(step.artifacts),
        },
    }

    step.started_at = _now()
    step.status = "running"
    await safe_add_event(
        monitor_task_id,
        TaskEventType.STAGE_STARTED,
        f"Step {step.id} started (agent={step.agent_id})",
        stage=step.id,
        agent_id=step.agent_id,
    )

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
            await safe_add_event(
                monitor_task_id,
                TaskEventType.STAGE_COMPLETED,
                f"Step {step.id} done ({step.execution_time_ms}ms)",
                stage=step.id,
                agent_id=step.agent_id,
            )
        except asyncio.CancelledError:
            step.status = "cancelled"
            raise
        except Exception as e:  # noqa: BLE001
            step.status = "failed"
            step.error = f"{type(e).__name__}: {e}"
            await safe_add_event(
                monitor_task_id,
                TaskEventType.ERROR,
                f"Step {step.id} failed: {e}",
                stage=step.id,
                agent_id=step.agent_id,
            )
        finally:
            step.finished_at = _now()


async def _run_workflow(
    exec_: _WorkflowExecution,
    runner: "Any",
    monitor_task_id: str | None = None,
) -> None:
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
                *(
                    _run_step(exec_, s, runner, sem, monitor_task_id)
                    for s in ready
                ),
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
        # -- TaskMonitor: finalize workflow ---------------------------------
        _wf_final_status = {
            "failed": TaskStatus.FAILED,
            "cancelled": TaskStatus.CANCELLED,
        }.get(exec_.status, TaskStatus.DONE)
        await safe_update_task(
            monitor_task_id,
            status=_wf_final_status,
            progress=100,
            current_stage=None,
            result_summary=f"workflow {exec_.workflow_id}: {exec_.status}",
            error=exec_.error,
        )
    except asyncio.CancelledError:
        exec_.status = "cancelled"
        for s in exec_.steps.values():
            if s.status in {"pending", "running"}:
                s.status = "cancelled"
                s.finished_at = _now()
        await safe_update_task(
            monitor_task_id,
            status=TaskStatus.CANCELLED,
            result_summary=f"workflow {exec_.workflow_id}: cancelled",
        )
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("workflow %s crashed unexpectedly", exec_.workflow_id)
        exec_.status = "failed"
        exec_.error = f"runner crashed: {type(e).__name__}: {e}"
        await safe_update_task(
            monitor_task_id,
            status=TaskStatus.FAILED,
            error=exec_.error,
        )
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
            "depends_on": Optional[list[str]], "context": Optional[str],
            "constraints": Optional[list[str]],
            "artifacts": Optional[list[str]]}``. Max 25 steps.
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
    nested_error = _reject_nested_orchestration("coordinate_workflow")
    if nested_error is not None:
        return nested_error

    runner = get_host_agent_runner()
    if runner is None:
        return _err(
            "❌ No HostAgentRunner registered. The host application must call "
            "hubos.core.workers.set_host_agent_runner(...) at startup to enable "
            "coordinate_workflow.",
        )

    # Apply task_modes config: configured values are treated as minimum
    # guarantees so the user setting always takes effect over the LLM's
    # passed parameter (which may be a short default like 120s).
    tm = _get_task_modes_config()
    if tm is not None:
        cw = tm.coordinate_workflow
        max_concurrency = min(max_concurrency, cw.max_concurrency)
        timeout_seconds = max(timeout_seconds, cw.timeout_seconds)
        step_timeout_seconds = max(
            step_timeout_seconds,
            cw.step_timeout_seconds,
        )
        if len(steps) > cw.max_steps:
            return _err(
                f"coordinate_workflow: {len(steps)} steps exceeds "
                f"max_steps limit ({cw.max_steps})",
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
        workspace_dir=ctx.get("workspace_dir") or "",
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

    # -- TaskMonitor: create monitoring task for workflow -------------------
    _wf_monitor_id = await safe_create_task(
        session_id=exec_.session_id or "unknown",
        source="tool",
        title=title or f"workflow {wf_id}",
        tool_name="coordinate_workflow",
        metadata={"workflow_id": wf_id},
    )
    await safe_update_task(_wf_monitor_id, status=TaskStatus.RUNNING)

    run_task = asyncio.create_task(
        _run_workflow(exec_, runner, _wf_monitor_id),
        name=f"hubos.core-workflow-{wf_id}",
    )
    # Hold a reference on the execution so track/cancel can see it.
    exec_._run_task = run_task  # type: ignore[attr-defined]

    def _cancel_workflow_monitor() -> None:
        exec_.cancel_event.set()
        if not run_task.done():
            run_task.cancel()

    register_cancel_handler(_wf_monitor_id, _cancel_workflow_monitor)
    run_task.add_done_callback(
        lambda _task: unregister_cancel_handler(_wf_monitor_id),
    )

    # -- RunControl: register for unified cancel ----------------------------
    _wf_run_id: str | None = None
    try:
        from hubos.app.run_control import (
            get_run_control_store,
            RunEntry,
            RunType,
            get_current_run_id,
        )

        _parent_run_id = get_current_run_id()
        _wf_run_id = await get_run_control_store().register(
            RunEntry(
                run_id="",
                run_type=RunType.WORKFLOW,
                session_id=exec_.session_id or "unknown",
                monitor_task_id=_wf_monitor_id,
                workflow_id=wf_id,
                parent_run_id=_parent_run_id,
            ),
        )
    except Exception:  # noqa: BLE001
        pass

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
    except asyncio.CancelledError:
        # RunControl: mark cancelled
        if _wf_run_id:
            try:
                from hubos.app.run_control import get_run_control_store

                await get_run_control_store().update_status(
                    _wf_run_id,
                    "cancelled",
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "workflow: RunControl status update (cancelled) failed for %s",
                    _wf_run_id,
                )
        raise

    # RunControl: mark done/failed based on workflow state
    if _wf_run_id:
        try:
            from hubos.app.run_control import get_run_control_store

            wf_snap = _render_workflow_snapshot(exec_, include_results=False)
            _wf_status = (
                "done" if wf_snap.get("status") == "done" else "failed"
            )
            await get_run_control_store().update_status(_wf_run_id, _wf_status)
        except Exception:  # noqa: BLE001
            logger.warning(
                "workflow: RunControl status update (%s) failed for %s",
                _wf_status,
                _wf_run_id,
            )

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
