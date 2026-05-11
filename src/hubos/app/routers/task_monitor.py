# -*- coding: utf-8 -*-
"""Task Monitor API — list tasks, get details, and SSE real-time stream.

All endpoints are read-only. Task creation and updates happen inside the
multi-agent tool layer (spawn_subagents / coordinate_workflow / delegate_task)
which writes to TaskMonitorStore directly.

Routes are mounted at ``/task-monitor`` by the router aggregator.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from ...app.task_monitor import (
    BroadcastEvent,
    Task,
    TaskEventType,
    TaskMonitorStore,
    TaskStatus,
)
from ...app.task_monitor_helpers import get_monitor_store, request_cancel_task

router = APIRouter(prefix="/task-monitor", tags=["task-monitor"])

_SSE_HEARTBEAT_INTERVAL = 15


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialise_task(task: Task) -> Dict[str, Any]:
    """Convert a Task dataclass to a JSON-safe dict."""
    return {
        "task_id": task.task_id,
        "session_id": task.session_id,
        "source": task.source,
        "title": task.title,
        "tool_name": task.tool_name,
        "agent_id": task.agent_id,
        "status": task.status.value if isinstance(task.status, TaskStatus) else task.status,
        "current_stage": task.current_stage,
        "progress": task.progress,
        "result_summary": task.result_summary,
        "error": task.error,
        "metadata": task.metadata,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "finished_at": task.finished_at,
        "events": [
            {
                "event_type": e.event_type.value if isinstance(e.event_type, TaskEventType) else e.event_type,
                "message": e.message,
                "timestamp": e.timestamp,
                "stage": e.stage,
                "agent_id": e.agent_id,
                "metadata": e.metadata,
            }
            for e in task.events
        ],
    }


def _serialise_broadcast(event: BroadcastEvent) -> Dict[str, Any]:
    """Convert a BroadcastEvent to a JSON-safe dict."""
    return {
        "type": event.event_type.value if isinstance(event.event_type, TaskEventType) else event.event_type,
        "task_id": event.task_id,
        "data": event.data,
        "timestamp": event.timestamp,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/tasks", summary="List tasks")
async def list_tasks(
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
    tool_name: Optional[str] = Query(None),
) -> dict:
    """Return tasks matching the given filters."""
    store = get_monitor_store()
    status_enum = None
    if status:
        try:
            status_enum = TaskStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Must be one of: {[s.value for s in TaskStatus]}",
            )

    tasks = await store.list_tasks(
        limit=limit,
        status=status_enum,
        session_id=session_id,
        tool_name=tool_name,
    )
    return {
        "tasks": [_serialise_task(t) for t in tasks],
        "count": len(tasks),
    }


@router.get("/tasks/{task_id}", summary="Get task detail")
async def get_task(task_id: str) -> dict:
    """Return a single task by ID."""
    store = get_monitor_store()
    task = await store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return _serialise_task(task)


@router.post("/tasks/{task_id}/cancel", summary="Cancel task")
async def cancel_task(task_id: str) -> dict:
    """Request best-effort cancellation for a running monitor task."""
    ok = await request_cancel_task(task_id)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {"task_id": task_id, "status": "cancel_requested"}


@router.get("/stream", summary="SSE real-time task events")
async def stream_events(
    session_id: Optional[str] = Query(None),
    tool_name: Optional[str] = Query(None),
) -> StreamingResponse:
    """Subscribe to real-time task events via Server-Sent Events.

    Optionally filter by session_id or tool_name. The server sends a
    ``: ping`` heartbeat every 15 seconds to keep the connection alive.
    """
    store = get_monitor_store()
    sub_id, queue = store.subscribe()

    async def _event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=_SSE_HEARTBEAT_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    # Heartbeat
                    yield ": ping\n\n"
                    continue

                # Apply optional filters
                if session_id is not None:
                    event_sid = event.data.get("session_id")
                    if event_sid and event_sid != session_id:
                        continue
                if tool_name is not None:
                    event_tn = event.data.get("tool_name")
                    if event_tn and event_tn != tool_name:
                        continue

                payload = _serialise_broadcast(event)
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            store.unsubscribe(sub_id)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
