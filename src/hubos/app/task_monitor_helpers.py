# -*- coding: utf-8 -*-
"""Helper to instrument multi-agent tools with TaskMonitorStore.

All functions swallow exceptions so monitoring failures never affect tool
execution.  Import and call the ``instrument_*`` wrappers near the top of
each tool function.
"""
from __future__ import annotations

import logging
import inspect
import time
from typing import Any, Callable, Optional

from hubos.app.task_monitor import (
    TaskEventType,
    TaskMonitorStore,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# Module-level singleton — shared across all tools in this process.
_store: Optional[TaskMonitorStore] = None
_cancel_handlers: dict[str, Callable[[], Any]] = {}


def get_monitor_store() -> TaskMonitorStore:
    """Return (and lazily create) the global TaskMonitorStore."""
    global _store
    if _store is None:
        _store = TaskMonitorStore()
    return _store


# ------------------------------------------------------------------
# Safe wrappers — never raise
# ------------------------------------------------------------------

async def safe_create_task(
    session_id: str,
    source: str,
    title: str,
    **kwargs: Any,
) -> Optional[str]:
    """Create a task, returning task_id or None on failure."""
    try:
        store = get_monitor_store()
        task = await store.create_task(
            session_id=session_id,
            source=source,
            title=title,
            **kwargs,
        )
        return task.task_id
    except Exception:  # noqa: BLE001
        logger.warning("task_monitor: create_task failed", exc_info=True)
        return None


async def safe_update_task(task_id: Optional[str], **kwargs: Any) -> None:
    """Update a task. Swallows all errors."""
    if not task_id:
        return
    try:
        store = get_monitor_store()
        await store.update_task(task_id, **kwargs)
    except Exception:  # noqa: BLE001
        logger.warning("task_monitor: update_task failed", exc_info=True)


async def safe_add_event(
    task_id: Optional[str],
    event_type: TaskEventType,
    message: str,
    **kwargs: Any,
) -> None:
    """Add an event. Swallows all errors."""
    if not task_id:
        return
    try:
        store = get_monitor_store()
        await store.add_event(task_id, event_type, message, **kwargs)
    except Exception:  # noqa: BLE001
        logger.warning("task_monitor: add_event failed", exc_info=True)


# ------------------------------------------------------------------
# Cancellation registry
# ------------------------------------------------------------------

def register_cancel_handler(
    task_id: Optional[str],
    handler: Callable[[], Any],
) -> None:
    """Register a best-effort cancellation handler for a monitor task."""
    if not task_id:
        return
    _cancel_handlers[task_id] = handler


def unregister_cancel_handler(task_id: Optional[str]) -> None:
    """Remove a previously registered cancellation handler."""
    if not task_id:
        return
    _cancel_handlers.pop(task_id, None)


async def request_cancel_task(task_id: str) -> bool:
    """Request cancellation for a monitor task.

    Returns True when the task exists and a cancel request was recorded. The
    handler is best-effort: it may cancel asyncio tasks, set a cooperative flag,
    or no-op for completed tasks. Any handler error is logged and swallowed so
    the API remains responsive.
    """
    store = get_monitor_store()
    task = await store.get_task(task_id)
    if task is None:
        return False

    if task.status in {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.CANCELLED}:
        return True

    await safe_add_event(
        task_id,
        TaskEventType.TASK_CANCELLED,
        "Cancellation requested by user",
    )
    await safe_update_task(
        task_id,
        status=TaskStatus.CANCELLED,
        result_summary="Cancellation requested",
    )

    handler = _cancel_handlers.get(task_id)
    if handler is None:
        return True

    try:
        result = handler()
        if inspect.isawaitable(result):
            await result
    except Exception:  # noqa: BLE001
        logger.warning("task_monitor: cancel handler failed", exc_info=True)
    finally:
        unregister_cancel_handler(task_id)
    return True
