# -*- coding: utf-8 -*-
"""HubOS-Runtime delegation tools.

总经理 agent (HubOS-WebUI) 通过这三个工具把结构化任务派给 HubOS Runtime：

    delegate_task(goal, ...)   把任务委派出去（默认阻塞等结果）
    track_task(task_id, ...)   查询/订阅一个已委派任务的进度
    cancel_task(task_id)       取消一个任务

执行后端可切换：
- ``HUBOS_RUNTIME_MODE=inprocess`` (默认)
    直接调用同进程 ``hubos.core`` 库（``ExecutionOrchestrator`` / ``TaskStore`` /
    ``EventStore``）。零网络开销，启动即可用。
- ``HUBOS_RUNTIME_MODE=http``
    通过 HTTP+SSE 调远端 Runtime（兼容老部署，由 ``HUBOS_RUNTIME_URL``
    控制基址，默认 ``http://127.0.0.1:8089``）。

设计要点:
- 调用失败一律返回结构化错误（不抛），让 LLM 能告诉用户 "Runtime 不可用"
  而不是把 stack trace 暴出去
- ``delegate_task`` 返回的 text 里始终包含 ``Task ID: <id>``，便于 LLM 后续
  ``track_task`` / ``cancel_task`` 时引用
- 通过 :func:`set_runtime_request_context` 由 HubOSAgent 在 __init__ 时注入
  当前 session_id / user_id / channel，让 Runtime 能做审计与多租户隔离
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

import httpx
from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

logger = logging.getLogger(__name__)


_DEFAULT_RUNTIME_URL = "http://127.0.0.1:8089"
_DEFAULT_WAIT_TIMEOUT = 180
_SUBMIT_TIMEOUT = 10
_TERMINAL_EVENT_TYPES = {"task_completed", "task_failed", "done"}
_PROGRESS_EVENT_TYPES = {
    "task_received",
    "stage_started",
    "stage_completed",
    "task_completed",
    "task_failed",
}
_MAX_PROGRESS_LINES_IN_RESPONSE = 30
_TERMINAL_TASK_STATUSES = {"done", "failed", "cancelled"}


# ==================== 模式选择 ====================


def _get_runtime_mode() -> str:
    """``agent_bridge`` | ``inprocess`` (default) | ``http``.

    Priority: ``HUBOS_DELEGATE_AGENT_BRIDGE`` > ``HUBOS_RUNTIME_MODE``.
    Read on every call so flag can be flipped at runtime.
    """
    if os.environ.get(
        "HUBOS_DELEGATE_AGENT_BRIDGE",
        "",
    ).strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return "agent_bridge"
    return (
        os.environ.get(
            "HUBOS_RUNTIME_MODE",
            "inprocess",
        )
        .strip()
        .lower()
    )


# ==================== In-process 单例 ====================


_inproc_lock = threading.Lock()
_inproc_orchestrator: Any = None
_inproc_task_store: Any = None
_inproc_event_store: Any = None


def _get_inprocess_components() -> tuple[Any, Any, Any]:
    """Lazy-initialize ``hubos.core`` orchestrator/task_store/event_store singletons.

    Thread-safe (double-checked lock) so concurrent sessions don't race
    on first import.
    """
    global _inproc_orchestrator, _inproc_task_store, _inproc_event_store
    if _inproc_orchestrator is not None:
        return _inproc_orchestrator, _inproc_task_store, _inproc_event_store
    with _inproc_lock:
        if _inproc_orchestrator is not None:
            return (
                _inproc_orchestrator,
                _inproc_task_store,
                _inproc_event_store,
            )

        # Execution loop must be enabled before hubos.core.execution wires the
        # orchestrator singleton.
        os.environ.setdefault("ENABLE_EXECUTION_LOOP_MVP", "true")

        from hubos.core.execution import (
            get_event_store,
            get_orchestrator,
            get_task_store,
        )
        from hubos.core.infra.feature_flags import get_feature_flags

        flags = get_feature_flags()
        if not flags.enable_execution_loop_mvp:
            flags.enable_execution_loop_mvp = True
            logger.info(
                "Force-enabled enable_execution_loop_mvp in this process",
            )

        _inproc_task_store = get_task_store()
        _inproc_event_store = get_event_store()
        _inproc_orchestrator = get_orchestrator()
        logger.info(
            "hubos.core in-process runtime initialized: %s / %s / %s",
            type(_inproc_orchestrator).__name__,
            type(_inproc_task_store).__name__,
            type(_inproc_event_store).__name__,
        )
    return _inproc_orchestrator, _inproc_task_store, _inproc_event_store


# ==================== Agent Bridge 状态 ====================

_bridge_lock = threading.Lock()
_bridge_tasks: dict[str, dict[str, Any]] = {}
_BRIDGE_FALLBACK_AGENT = "rd"


# ==================== Runtime 请求上下文 ====================


_runtime_request_ctx: ContextVar[dict[str, str] | None] = ContextVar(
    "hubos_runtime_request_ctx",
    default=None,
)


def set_runtime_request_context(ctx: dict[str, str] | None) -> None:
    """由 HubOSAgent 调用，把当前 (session_id, user_id, channel) 注入。

    在 query_handler 或 HubOSAgent.__init__ 里调用一次即可。"""
    _runtime_request_ctx.set(dict(ctx) if ctx else None)


def _current_runtime_ctx() -> dict[str, str]:
    return _runtime_request_ctx.get() or {}


def _get_runtime_base_url() -> str:
    return os.environ.get("HUBOS_RUNTIME_URL", _DEFAULT_RUNTIME_URL).rstrip(
        "/",
    )


def _new_client(timeout: Any = _SUBMIT_TIMEOUT) -> httpx.AsyncClient:
    """All Runtime calls bypass system proxies.

    Runtime is a sibling service (typically loopback or same-VPC), and any
    HTTP_PROXY / HTTPS_PROXY honored by httpx's default ``trust_env=True`` would
    incorrectly tunnel local calls through corporate proxies → mysterious 502/503.
    """
    return httpx.AsyncClient(timeout=timeout, trust_env=False)


# ==================== 公共助手 ====================


def _err(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _ok(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _format_progress_lines(events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for ev in events:
        ev_type = ev.get("event_type") or ev.get("event") or "?"
        if ev_type not in _PROGRESS_EVENT_TYPES and ev_type != "done":
            continue
        ts = ev.get("timestamp", "")
        stage = ev.get("stage", "") or ev.get("data", {}).get("stage", "")
        summary = ev.get("summary") or ev.get("data", {}).get("summary") or ""
        prefix = f"[{ts}]" if ts else ""
        body = f"{ev_type}"
        if stage:
            body += f" {stage}"
        if summary:
            body += f" — {summary}"
        line = f"{prefix} {body}".strip()
        lines.append(line)
    return lines


# ==================== Tool 1: delegate_task ====================


async def delegate_task(
    goal: str,
    priority: str = "normal",
    workflow: str = "one_person_default",
    wait: bool = True,
    timeout_seconds: int = _DEFAULT_WAIT_TIMEOUT,
    extra_context: dict[str, Any] | None = None,
) -> ToolResponse:
    """Delegate a task to HubOS Runtime for autonomous execution.

    Use this when the user asks for something that requires non-trivial
    execution (research, multi-step analysis, code generation, batch
    processing, anything that benefits from the Runtime's worker pool /
    multi-agent collaboration).

    DO NOT use this for trivial Q&A you can answer yourself, or for
    actions that need a tool that you already have (file ops, shell,
    browser, etc.) — call those tools directly.

    Backend selected via ``HUBOS_RUNTIME_MODE`` env var
    (``inprocess`` default | ``http``).

    Args:
        goal: Natural language description of what the task must achieve.
            Be specific and self-contained — Runtime worker will not have
            chat history.
        priority: ``"low"`` | ``"normal"`` | ``"high"``. Default ``"normal"``.
        workflow: Which Runtime workflow preset to use. Default
            ``"one_person_default"``. Leave default unless you know what you
            are doing.
        wait: If True (default), block until task finishes and return the
            final result inline. If False, return immediately with the
            task_id so caller can ``track_task`` later.
        timeout_seconds: Max seconds to wait when ``wait=True``. Default 180.
        extra_context: Optional additional structured data to forward to
            Runtime (e.g. ``{"locale": "zh-CN", "max_tokens": 2000}``).

    Returns:
        ``ToolResponse`` text containing the ``Task ID``, current status,
        and (if ``wait=True``) the final response from Runtime. On failure
        returns a structured error message — does NOT raise.
    """
    if not goal or not goal.strip():
        return _err("delegate_task: goal cannot be empty")

    mode = _get_runtime_mode()
    if mode == "agent_bridge":
        return await _delegate_task_agent_bridge(
            goal,
            priority,
            workflow,
            wait,
            timeout_seconds,
            extra_context,
        )
    if mode == "inprocess":
        return await _delegate_task_inprocess(
            goal,
            priority,
            workflow,
            wait,
            timeout_seconds,
            extra_context,
        )
    if mode == "http":
        return await _delegate_task_http(
            goal,
            priority,
            workflow,
            wait,
            timeout_seconds,
            extra_context,
        )
    return _err(
        f"❌ Unknown HUBOS_RUNTIME_MODE: {mode!r} "
        f"(expected 'agent_bridge' | 'inprocess' | 'http')",
    )


async def _delegate_task_http(
    goal: str,
    priority: str,
    workflow: str,
    wait: bool,
    timeout_seconds: int,
    extra_context: dict[str, Any] | None,
) -> ToolResponse:
    """HTTP fallback path: keeps the original cross-process behaviour intact."""
    base_url = _get_runtime_base_url()
    ctx = _current_runtime_ctx()
    body = {
        "input_text": goal.strip(),
        "session_id": ctx.get("session_id"),
        "channel": ctx.get("channel") or "web_ui",
        "user_id": ctx.get("user_id"),
        "priority": priority,
        "requested_workflow": workflow,
        "metadata": extra_context or None,
    }
    body = {k: v for k, v in body.items() if v is not None}

    try:
        async with _new_client() as client:
            resp = await client.post(f"{base_url}/v1/tasks", json=body)
    except httpx.RequestError as e:
        return _err(
            f"❌ HubOS Runtime unreachable at {base_url}: {e}\n"
            f"提示：要么把 HUBOS_RUNTIME_MODE 设为 'inprocess'，要么先启动 Runtime HTTP 服务。",
        )

    if resp.status_code not in (200, 202):
        return _err(
            f"❌ Runtime rejected task: HTTP {resp.status_code} {resp.text[:300]}",
        )

    submit = resp.json()
    task_id = submit.get("task_id")
    if not task_id:
        return _err(f"❌ Runtime responded without task_id: {submit}")

    logger.info(
        "delegate_task[http]: submitted task=%s session=%s wait=%s",
        task_id,
        ctx.get("session_id"),
        wait,
    )

    if not wait:
        return _ok(
            f"✅ Task delegated to Runtime (http).\n"
            f"Task ID: {task_id}\n"
            f"Initial status: {submit.get('status')}\n"
            f'Use track_task(task_id="{task_id}") to check progress.',
        )

    return await _wait_for_task(base_url, task_id, timeout_seconds)


async def _delegate_task_inprocess(
    goal: str,
    priority: str,
    workflow: str,
    wait: bool,
    timeout_seconds: int,
    extra_context: dict[str, Any] | None,
) -> ToolResponse:
    """In-process path: drive hubos.core directly inside the GM agent's process.

    Concurrency model: each call gets its own ``asyncio.create_task`` wrapping
    ``asyncio.to_thread(orchestrator.execute_task, task_id)``. Multiple sessions
    delegating in parallel run on the default thread-pool — the underlying
    TaskStore/EventStore are already concurrency-tested in the HubOS Runtime
    HTTP server (S1 verified 2.69x speed-up at concurrency=3).
    """
    try:
        orchestrator, task_store, _event_store = _get_inprocess_components()
    except Exception as e:  # noqa: BLE001
        logger.exception("Failed to initialize in-process runtime")
        return _err(
            f"❌ Failed to initialize in-process Runtime: {type(e).__name__}: {e}\n"
            f"提示：可临时设置 HUBOS_RUNTIME_MODE=http 回退到 HTTP 模式。",
        )

    ctx = _current_runtime_ctx()
    try:
        task = task_store.create_task(
            input_text=goal.strip(),
            session_id=ctx.get("session_id"),
            channel=ctx.get("channel") or "web_ui",
            priority=priority,
            requested_workflow=workflow,
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("create_task failed in in-process mode")
        return _err(f"❌ Runtime rejected task: {type(e).__name__}: {e}")

    task_id = task.task_id
    logger.info(
        "delegate_task[inprocess]: submitted task=%s session=%s wait=%s",
        task_id,
        ctx.get("session_id"),
        wait,
    )

    # Fire execute_task in a background thread (it's synchronous + may take
    # tens of seconds). We hold the asyncio handle so wait=True can await it.
    exec_future = asyncio.create_task(
        asyncio.to_thread(orchestrator.execute_task, task_id),
        name=f"hubos.core-execute-{task_id}",
    )

    if not wait:
        # Detach: execution keeps running; caller polls via track_task later.
        # Suppress "coroutine was never awaited" warnings by adding a noop done callback.
        exec_future.add_done_callback(_log_inprocess_completion)
        return _ok(
            f"✅ Task delegated to Runtime (inprocess).\n"
            f"Task ID: {task_id}\n"
            f"Initial status: {task.current_status.value}\n"
            f'Use track_task(task_id="{task_id}") to check progress.',
        )

    try:
        await asyncio.wait_for(exec_future, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        # Don't cancel — execution keeps running; caller can re-track later.
        return _ok(
            f"Task ID: {task_id}\n"
            f"Status: TIMEOUT\n"
            f"⏰ Wait timed out after {timeout_seconds}s. Task may still be "
            f'running. Use track_task("{task_id}") later.',
        )
    except Exception as e:  # noqa: BLE001
        logger.exception("execute_task crashed in in-process mode")
        return _err(f"❌ Task execution crashed: {type(e).__name__}: {e}")

    final_task = task_store.get_task(task_id)
    return _ok(_render_inprocess_task(final_task))


def _log_inprocess_completion(fut: "asyncio.Future[Any]") -> None:
    """Background callback: log uncaught exceptions from fire-and-forget tasks."""
    try:
        fut.result()
    except Exception:  # noqa: BLE001
        logger.exception(
            "Background in-process task crashed: %s",
            fut.get_name(),
        )


def _render_inprocess_task(task: Any) -> str:
    """Format a TaskStore Task object the same way HTTP path renders snapshots."""
    if task is None:
        return "Task not found (in-process)."
    status = (
        task.current_status.value
        if hasattr(task.current_status, "value")
        else str(task.current_status)
    )
    lines = [
        f"Task ID: {task.task_id}",
        f"Status: {status}",
    ]
    final_response = getattr(task, "final_response", None)
    if final_response:
        lines.append("Final response:")
        lines.append(_render_final_response(final_response))
    return "\n".join(lines)


# ==================== Tool 2: track_task ====================


async def track_task(
    task_id: str,
    follow: bool = True,
    timeout_seconds: int = _DEFAULT_WAIT_TIMEOUT,
) -> ToolResponse:
    """Get current status (and optionally wait until done) of a Runtime task.

    Use this after a previous ``delegate_task(..., wait=False)``, or to
    re-check a task that may have completed since you last looked.

    Backend selected via ``HUBOS_RUNTIME_MODE`` env var.

    Args:
        task_id: Task ID returned from a previous ``delegate_task`` call.
        follow: If True (default) and task is not yet terminal, wait until
            task finishes or timeout. If False, just return current snapshot.
        timeout_seconds: Max seconds to wait when ``follow=True``.

    Returns:
        ``ToolResponse`` with current status and final response if available.
    """
    if not task_id or not task_id.strip():
        return _err("track_task: task_id is required")

    mode = _get_runtime_mode()
    if mode == "agent_bridge":
        return await _track_task_agent_bridge(
            task_id,
            follow,
            timeout_seconds,
        )
    if mode == "inprocess":
        return await _track_task_inprocess(task_id, follow, timeout_seconds)
    if mode == "http":
        return await _track_task_http(task_id, follow, timeout_seconds)
    return _err(f"❌ Unknown HUBOS_RUNTIME_MODE: {mode!r}")


async def _track_task_http(
    task_id: str,
    follow: bool,
    timeout_seconds: int,
) -> ToolResponse:
    base_url = _get_runtime_base_url()

    try:
        async with _new_client() as client:
            resp = await client.get(f"{base_url}/v1/tasks/{task_id}")
    except httpx.RequestError as e:
        return _err(f"❌ HubOS Runtime unreachable at {base_url}: {e}")

    if resp.status_code == 404:
        return _err(f"❌ Task not found: {task_id}")
    if resp.status_code != 200:
        return _err(
            f"❌ Runtime returned HTTP {resp.status_code}: {resp.text[:300]}",
        )

    snapshot = resp.json()
    current_status = snapshot.get("current_status", "unknown")
    final_response = snapshot.get("final_response")

    is_terminal = current_status.lower() in _TERMINAL_TASK_STATUSES

    if is_terminal or not follow:
        lines = [
            f"Task ID: {task_id}",
            f"Status: {current_status}",
        ]
        if final_response:
            lines.append("Final response:")
            lines.append(_render_final_response(final_response))
        return _ok("\n".join(lines))

    return await _wait_for_task(base_url, task_id, timeout_seconds)


async def _track_task_inprocess(
    task_id: str,
    follow: bool,
    timeout_seconds: int,
) -> ToolResponse:
    """Inspect TaskStore directly. If not terminal and follow=True, poll until
    terminal or timeout (no SSE — store is local memory)."""
    try:
        _orchestrator, task_store, _event_store = _get_inprocess_components()
    except Exception as e:  # noqa: BLE001
        return _err(f"❌ Failed to access in-process Runtime: {e}")

    task = task_store.get_task(task_id)
    if task is None:
        return _err(f"❌ Task not found (in-process): {task_id}")

    status_value = (
        task.current_status.value
        if hasattr(task.current_status, "value")
        else str(task.current_status)
    )
    is_terminal = status_value.lower() in _TERMINAL_TASK_STATUSES
    if is_terminal or not follow:
        return _ok(_render_inprocess_task(task))

    # Poll-wait: hubos.core TaskStore is in-memory; cheapest to short-poll.
    poll_interval = 0.25
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(poll_interval)
        task = task_store.get_task(task_id)
        if task is None:
            return _err(f"❌ Task disappeared from store: {task_id}")
        status_value = (
            task.current_status.value
            if hasattr(task.current_status, "value")
            else str(task.current_status)
        )
        if status_value.lower() in _TERMINAL_TASK_STATUSES:
            return _ok(_render_inprocess_task(task))

    return _ok(
        f"Task ID: {task_id}\n"
        f"Status: {status_value} (still running)\n"
        f"⏰ track_task timed out after {timeout_seconds}s. Task may still be "
        f'running. Re-call track_task("{task_id}") later.',
    )


# ==================== Tool 3: cancel_task ====================


async def cancel_task(task_id: str) -> ToolResponse:
    """Cancel a Runtime task by ID.

    NOTE: per-task cooperative cancellation is not yet implemented in either
    backend. Both modes surface the same "unsupported" message; planned for S6.

    Args:
        task_id: Task ID to cancel.

    Returns:
        ``ToolResponse`` describing what happened.
    """
    if not task_id or not task_id.strip():
        return _err("cancel_task: task_id is required")

    mode = _get_runtime_mode()
    if mode == "agent_bridge":
        return await _cancel_task_agent_bridge(task_id)
    if mode == "inprocess":
        return await _cancel_task_inprocess(task_id)
    if mode == "http":
        return await _cancel_task_http(task_id)
    return _err(f"❌ Unknown HUBOS_RUNTIME_MODE: {mode!r}")


async def _cancel_task_http(task_id: str) -> ToolResponse:
    base_url = _get_runtime_base_url()
    try:
        async with _new_client() as client:
            resp = await client.post(f"{base_url}/v1/tasks/{task_id}/cancel")
    except httpx.RequestError as e:
        return _err(f"❌ HubOS Runtime unreachable at {base_url}: {e}")

    if resp.status_code == 404:
        return _err(f"❌ Task not found: {task_id}")

    try:
        body = resp.json()
    except json.JSONDecodeError:
        body = {"raw": resp.text[:300]}

    if resp.status_code == 501:
        msg = body.get("message", "cancel not implemented")
        return _ok(
            f"⚠️ Runtime does not yet support per-task cancellation.\n"
            f"Task ID: {task_id}\n"
            f"Detail: {msg}",
        )

    if resp.status_code in (200, 202):
        return _ok(
            f"✅ Cancel requested for task {task_id}\n"
            f"Runtime response: {body}",
        )

    return _err(
        f"❌ Cancel failed: HTTP {resp.status_code} {body}",
    )


async def _cancel_task_inprocess(task_id: str) -> ToolResponse:
    """In-process: same semantics as HTTP 501 — cooperative cancel not yet wired."""
    try:
        _orchestrator, task_store, _event_store = _get_inprocess_components()
    except Exception as e:  # noqa: BLE001
        return _err(f"❌ Failed to access in-process Runtime: {e}")

    task = task_store.get_task(task_id)
    if task is None:
        return _err(f"❌ Task not found (in-process): {task_id}")

    return _ok(
        f"⚠️ Runtime does not yet support per-task cancellation.\n"
        f"Task ID: {task_id}\n"
        f"Detail: cooperative cancel will land with S6.",
    )


# ==================== 内部：阻塞等任务结束 ====================


async def _wait_for_task(
    base_url: str,
    task_id: str,
    timeout_seconds: int,
) -> ToolResponse:
    """订阅 SSE 直到任务终止 / 超时 / 客户端断开。"""
    sse_url = f"{base_url}/v1/tasks/{task_id}/events"
    progress_lines: list[str] = []
    final_status: str | None = None
    final_response: Any = None
    saw_terminal = False

    try:
        async with _new_client(
            timeout=httpx.Timeout(None, connect=5),
        ) as client:
            async with client.stream(
                "GET",
                sse_url,
                headers={"Accept": "text/event-stream"},
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    return _err(
                        f"❌ Failed to subscribe to task events: HTTP "
                        f"{resp.status_code} {body[:300]!r}",
                    )

                current_event: str | None = None
                current_data_lines: list[str] = []
                _wall_remaining = timeout_seconds

                async for raw_line in _aiter_lines_with_timeout(
                    resp,
                    timeout_seconds,
                ):
                    line = raw_line.rstrip("\r")
                    if line == "":
                        if current_event is not None:
                            data = "\n".join(current_data_lines)
                            handled = _handle_sse_event(
                                current_event,
                                data,
                                progress_lines,
                            )
                            if handled is not None:
                                final_status, final_response = handled
                                saw_terminal = True
                                break
                        current_event = None
                        current_data_lines = []
                        continue

                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        current_event = line[len("event:") :].strip()
                    elif line.startswith("data:"):
                        current_data_lines.append(
                            line[len("data:") :].lstrip(),
                        )
                    elif line.startswith("id:"):
                        pass
    except _SseTimeout:
        return _ok(
            _summarise(
                task_id,
                status="TIMEOUT",
                final_response=None,
                progress_lines=progress_lines,
                note=(
                    f"⏰ Wait timed out after {timeout_seconds}s. Task may still "
                    f'be running. Use track_task("{task_id}") later.'
                ),
            ),
        )
    except httpx.RequestError as e:
        return _err(f"❌ SSE connection error: {e}")

    if not saw_terminal:
        return _ok(
            _summarise(
                task_id,
                status=final_status or "UNKNOWN",
                final_response=final_response,
                progress_lines=progress_lines,
                note="(SSE stream ended without explicit terminal event)",
            ),
        )

    return _ok(
        _summarise(
            task_id,
            status=final_status or "DONE",
            final_response=final_response,
            progress_lines=progress_lines,
        ),
    )


def _handle_sse_event(
    event_type: str,
    data: str,
    progress_lines: list[str],
) -> tuple[str, Any] | None:
    """Returns (final_status, final_response) when terminal, else None."""
    try:
        payload = json.loads(data) if data else {}
    except json.JSONDecodeError:
        payload = {"raw": data}

    if event_type == "done":
        return (
            payload.get("final_status") or "DONE",
            payload.get("final_response"),
        )

    summary_bits: list[str] = []
    if isinstance(payload, dict):
        for k in ("stage", "worker", "summary", "message", "error"):
            if k in payload and payload[k]:
                summary_bits.append(f"{k}={payload[k]}")
    summary = " ".join(summary_bits)
    progress_lines.append(f"· {event_type} {summary}".strip())

    if event_type == "task_failed":
        return ("FAILED", payload)

    return None


def _summarise(
    task_id: str,
    status: str,
    final_response: Any,
    progress_lines: list[str],
    note: str | None = None,
) -> str:
    out: list[str] = [f"Task ID: {task_id}", f"Status: {status}"]
    if progress_lines:
        shown = progress_lines[-_MAX_PROGRESS_LINES_IN_RESPONSE:]
        out.append("Progress:")
        out.extend(shown)
        if len(progress_lines) > len(shown):
            out.append(
                f"  (… {len(progress_lines) - len(shown)} earlier events truncated)",
            )
    if final_response:
        out.append("Final response:")
        out.append(_render_final_response(final_response))
    if note:
        out.append("")
        out.append(note)
    return "\n".join(out)


def _render_final_response(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return repr(payload)


# ==================== SSE 行迭代器 + 超时 ====================


class _SseTimeout(Exception):
    pass


async def _aiter_lines_with_timeout(
    resp: httpx.Response,
    timeout_seconds: int,
):
    """Wrap resp.aiter_lines() with a wall-clock timeout via asyncio.wait_for."""
    import asyncio

    iterator = resp.aiter_lines()
    deadline = asyncio.get_event_loop().time() + timeout_seconds
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise _SseTimeout()
        try:
            line = await asyncio.wait_for(
                iterator.__anext__(),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            raise _SseTimeout()
        except StopAsyncIteration:
            return
        yield line


# ==================== Agent Bridge 实现 ====================


async def _delegate_task_agent_bridge(
    goal: str,
    priority: str,
    workflow: str,
    wait: bool,
    timeout_seconds: int,
    extra_context: dict[str, Any] | None,
) -> ToolResponse:
    """Agent Bridge: route delegate_task through AgentScope agents.

    When ``HUBOS_DELEGATE_AGENT_BRIDGE=1``, instead of trying hubos.core
    Runtime (which requires registered agent roles), we delegate to the
    AgentScope agent pool via ``spawn_subagents`` / ``coordinate_workflow``.

    ``extra_context`` hints:
        - ``agent_id`` (str)        → single-agent task
        - ``assignments`` (list)    → multi-agent parallel
        - ``steps`` (list)          → coordinate_workflow DAG
        - none of the above         → single-agent, fallback to ``rd``
    """
    from hubos.agents.tools.agent_workforce import (
        coordinate_workflow as _coordinate_workflow,
        spawn_subagents as _spawn_subagents,
    )

    ctx = extra_context or {}
    task_id = f"bridge-{uuid4().hex[:12]}"

    # Determine execution mode from extra_context hints
    agent_id = ctx.get("agent_id")
    assignments = ctx.get("assignments")
    steps = ctx.get("steps")

    if steps:
        # DAG workflow
        exec_type = "workflow"
        record = _bridge_task_create(
            task_id,
            exec_type=exec_type,
            agent_id=None,
        )
        coro = _bridge_run_workflow(
            task_id,
            steps,
            timeout_seconds,
        )
    elif assignments:
        # Multi-agent parallel
        exec_type = "parallel"
        record = _bridge_task_create(
            task_id,
            exec_type=exec_type,
            agent_id=None,
        )
        coro = _bridge_run_parallel(
            task_id,
            assignments,
            timeout_seconds,
        )
    else:
        # Single agent (explicit or fallback)
        target = agent_id or _BRIDGE_FALLBACK_AGENT
        exec_type = "single"
        record = _bridge_task_create(
            task_id,
            exec_type=exec_type,
            agent_id=target,
        )
        coro = _bridge_run_single(
            task_id,
            target,
            goal,
            timeout_seconds,
        )

    if not wait:
        # Fire and forget — run in background
        _bridge_task_update(task_id, status="running")
        asyncio.create_task(
            _bridge_bg_wrapper(task_id, coro),
            name=f"agent-bridge-{task_id}",
        )
        return _ok(
            f"✅ Task delegated via Agent Bridge.\n"
            f"Task ID: {task_id}\n"
            f"Mode: {exec_type}\n"
            f'Use track_task(task_id="{task_id}") to check progress.',
        )

    # wait=True: run synchronously
    _bridge_task_update(task_id, status="running")
    try:
        result = await asyncio.wait_for(coro, timeout=timeout_seconds)
        return _ok(
            f"Task ID: {task_id}\n"
            f"Status: done\n"
            f"Mode: {exec_type}\n\n"
            f"{result}",
        )
    except asyncio.TimeoutError:
        _bridge_task_update(
            task_id,
            status="timeout",
            error=f"Timed out after {timeout_seconds}s",
        )
        return _ok(
            f"Task ID: {task_id}\n"
            f"Status: TIMEOUT\n"
            f"⏰ Wait timed out after {timeout_seconds}s. Task may still "
            f'be running. Use track_task("{task_id}") later.',
        )
    except Exception as e:  # noqa: BLE001
        _bridge_task_update(task_id, status="failed", error=str(e))
        return _err(f"❌ Agent Bridge task failed: {e}")


def _bridge_task_create(
    task_id: str,
    exec_type: str,
    agent_id: str | None,
) -> dict[str, Any]:
    """Create a bridge task record."""
    record: dict[str, Any] = {
        "task_id": task_id,
        "status": "pending",
        "exec_type": exec_type,
        "agent_id": agent_id,
        "result": None,
        "error": None,
        "created_at": time.time(),
        "finished_at": None,
        "workflow_id": None,
    }
    with _bridge_lock:
        _bridge_tasks[task_id] = record
    return record


def _bridge_task_update(
    task_id: str,
    *,
    status: str | None = None,
    result: str | None = None,
    error: str | None = None,
    workflow_id: str | None = None,
) -> None:
    """Update a bridge task record (thread-safe)."""
    with _bridge_lock:
        rec = _bridge_tasks.get(task_id)
        if rec is None:
            return
        if status is not None:
            rec["status"] = status
        if result is not None:
            rec["result"] = result
        if error is not None:
            rec["error"] = error
        if workflow_id is not None:
            rec["workflow_id"] = workflow_id
        if status in _TERMINAL_TASK_STATUSES | {"timeout"}:
            rec["finished_at"] = time.time()


async def _bridge_run_single(
    task_id: str,
    agent_id: str,
    goal: str,
    timeout_seconds: int,
) -> str:
    """Run a single-agent task via spawn_subagents."""
    from hubos.agents.tools.agent_workforce import (
        spawn_subagents as _spawn_subagents,
    )

    result = await _spawn_subagents(
        assignments=[{"agent_id": agent_id, "prompt": goal}],
        timeout_seconds=timeout_seconds,
    )
    # spawn_subagents returns a dict with results
    results = result.get("results", [])
    if results:
        content = results[0].get("content", "")
        _bridge_task_update(task_id, status="done", result=content)
        return content
    error = result.get("error", "No results returned")
    _bridge_task_update(task_id, status="failed", error=error)
    raise RuntimeError(error)


async def _bridge_run_parallel(
    task_id: str,
    assignments: list[dict[str, Any]],
    timeout_seconds: int,
) -> str:
    """Run a multi-agent parallel task via spawn_subagents."""
    from hubos.agents.tools.agent_workforce import (
        spawn_subagents as _spawn_subagents,
    )

    result = await _spawn_subagents(
        assignments=assignments,
        timeout_seconds=timeout_seconds,
    )
    results = result.get("results", [])
    parts = []
    for r in results:
        label = r.get("label", r.get("agent_id", "?"))
        content = r.get("content", "")
        success = r.get("success", False)
        if success:
            parts.append(f"## {label}\n{content}")
        else:
            parts.append(f"## {label} (FAILED)\n{r.get('error', '')}")
    summary = "\n\n".join(parts)
    _bridge_task_update(task_id, status="done", result=summary)
    return summary


async def _bridge_run_workflow(
    task_id: str,
    steps: list[dict[str, Any]],
    timeout_seconds: int,
) -> str:
    """Run a DAG workflow via coordinate_workflow."""
    from hubos.agents.tools.agent_workforce import (
        coordinate_workflow as _coordinate_workflow,
    )

    result = await _coordinate_workflow(
        steps=steps,
        timeout_seconds=timeout_seconds,
    )
    workflow_id = result.get("workflow_id", "")
    status = result.get("status", "unknown")
    if workflow_id:
        _bridge_task_update(task_id, workflow_id=workflow_id)

    if status == "done":
        parts = []
        for step in result.get("steps", []):
            sid = step.get("id", "?")
            step_status = step.get("status", "?")
            step_result = step.get("result", "")
            parts.append(
                f"### Step: {sid} ({step_status})\n{step_result}",
            )
        summary = "\n\n".join(parts)
        _bridge_task_update(task_id, status="done", result=summary)
        return summary

    _bridge_task_update(
        task_id,
        status="failed",
        error=f"Workflow ended with status: {status}",
    )
    raise RuntimeError(f"Workflow failed: {status}")


async def _bridge_bg_wrapper(
    task_id: str,
    coro: Any,
) -> None:
    """Background wrapper: run coroutine and update task record."""
    try:
        await coro
    except Exception as e:  # noqa: BLE001
        logger.exception("Agent Bridge background task %s failed", task_id)
        _bridge_task_update(task_id, status="failed", error=str(e))


async def _track_task_agent_bridge(
    task_id: str,
    follow: bool,
    timeout_seconds: int,
) -> ToolResponse:
    """Check status of an Agent Bridge task."""
    with _bridge_lock:
        rec = _bridge_tasks.get(task_id)

    if rec is None:
        return _err(f"❌ Task not found (agent_bridge): {task_id}")

    status = rec["status"]
    is_terminal = status in _TERMINAL_TASK_STATUSES | {"timeout"}

    if is_terminal or not follow:
        lines = [
            f"Task ID: {task_id}",
            f"Status: {status}",
            f"Mode: {rec.get('exec_type', '?')}",
        ]
        if rec.get("agent_id"):
            lines.append(f"Agent: {rec['agent_id']}")
        if rec.get("result"):
            lines.append(f"\nResult:\n{rec['result']}")
        if rec.get("error"):
            lines.append(f"\nError: {rec['error']}")
        return _ok("\n".join(lines))

    # follow=True but not yet terminal — poll
    elapsed = 0.0
    poll_interval = 2.0
    while elapsed < timeout_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval
        with _bridge_lock:
            rec = _bridge_tasks.get(task_id)
        if rec is None:
            return _err(
                f"❌ Task disappeared (agent_bridge): {task_id}",
            )
        status = rec["status"]
        if status in _TERMINAL_TASK_STATUSES | {"timeout"}:
            lines = [
                f"Task ID: {task_id}",
                f"Status: {status}",
                f"Mode: {rec.get('exec_type', '?')}",
            ]
            if rec.get("result"):
                lines.append(f"\nResult:\n{rec['result']}")
            if rec.get("error"):
                lines.append(f"\nError: {rec['error']}")
            return _ok("\n".join(lines))

    return _ok(
        f"Task ID: {task_id}\n"
        f"Status: {status} (still running)\n"
        f"⏰ Follow timed out after {timeout_seconds}s. "
        f'Use track_task("{task_id}") later.',
    )


async def _cancel_task_agent_bridge(task_id: str) -> ToolResponse:
    """Cancel an Agent Bridge task (best-effort)."""
    with _bridge_lock:
        rec = _bridge_tasks.get(task_id)

    if rec is None:
        return _err(f"❌ Task not found (agent_bridge): {task_id}")

    status = rec["status"]
    if status in _TERMINAL_TASK_STATUSES:
        return _ok(
            f"Task {task_id} already in terminal state: {status}",
        )

    # If it's a workflow, try to cancel via coordinate_workflow
    if rec.get("workflow_id"):
        try:
            from hubos.agents.tools.agent_workforce import (
                cancel_workflow as _cancel_workflow,
            )

            await _cancel_workflow(rec["workflow_id"])
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "Failed to cancel workflow %s: %s",
                rec["workflow_id"],
                e,
            )

    _bridge_task_update(task_id, status="cancelled")
    return _ok(f"✅ Cancel requested for bridge task {task_id}")
