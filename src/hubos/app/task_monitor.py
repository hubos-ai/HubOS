# -*- coding: utf-8 -*-
"""In-process TaskMonitorStore for tracking long-running tasks.

Provides a single-process store that records task lifecycle events and
broadcasts them to subscribers via asyncio.Queue for future SSE integration.

This module is intentionally self-contained — it does not import from
spawn_subagents, coordinate_workflow, or any frontend code.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_SUBSCRIBER_QUEUE_MAXSIZE = 200
_MAX_EVENTS_PER_TASK = 500


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskEventType(str, Enum):
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    LOG = "log"
    ERROR = "error"
    TASK_DONE = "task_done"
    TASK_FAILED = "task_failed"
    TASK_CANCELLED = "task_cancelled"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TaskEvent:
    event_type: TaskEventType
    message: str
    timestamp: float
    stage: Optional[str] = None
    agent_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class Task:
    task_id: str
    session_id: str
    source: str
    title: str
    status: TaskStatus
    created_at: float
    updated_at: float
    tool_name: Optional[str] = None
    agent_id: Optional[str] = None
    current_stage: Optional[str] = None
    progress: Optional[float] = None
    events: List[TaskEvent] = field(default_factory=list)
    result_summary: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    finished_at: Optional[float] = None


# ---------------------------------------------------------------------------
# Broadcast event (what subscribers receive)
# ---------------------------------------------------------------------------


@dataclass
class BroadcastEvent:
    """Envelope pushed to every subscriber queue."""

    event_type: TaskEventType
    task_id: str
    data: Dict[str, Any]
    timestamp: float


# ---------------------------------------------------------------------------
# TaskMonitorStore
# ---------------------------------------------------------------------------


class TaskMonitorStore:
    """Process-wide, async-safe task monitor.

    Usage::

        store = TaskMonitorStore()
        task = await store.create_task(
            session_id="s1",
            source="coordinate_workflow",
            title="Running sub-agent pipeline",
        )
        await store.update_task(task.task_id, status=TaskStatus.RUNNING)
        # ...
    """

    def __init__(self, max_tasks: int = 10_000) -> None:
        self._tasks: Dict[str, Task] = {}
        self._lock = asyncio.Lock()
        self._subscribers: Dict[str, asyncio.Queue[BroadcastEvent]] = {}
        self._max_tasks = max_tasks

    # -- subscribe / unsubscribe ------------------------------------------

    def subscribe(self) -> tuple[str, asyncio.Queue[BroadcastEvent]]:
        """Register a subscriber. Returns ``(subscriber_id, queue)``."""
        sub_id = uuid.uuid4().hex
        queue: asyncio.Queue[BroadcastEvent] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_MAXSIZE
        )
        self._subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a subscriber."""
        self._subscribers.pop(subscriber_id, None)

    # -- create_task -------------------------------------------------------

    async def create_task(
        self,
        session_id: str,
        source: str,
        title: str,
        *,
        tool_name: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        now = time.time()
        task_id = uuid.uuid4().hex
        task = Task(
            task_id=task_id,
            session_id=session_id,
            source=source,
            title=title,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
            tool_name=tool_name,
            agent_id=agent_id,
            metadata=dict(metadata) if metadata else None,
        )
        evt = TaskEvent(
            event_type=TaskEventType.TASK_CREATED,
            message=f"Task created: {title}",
            timestamp=now,
            agent_id=agent_id,
        )
        task.events.append(evt)

        async with self._lock:
            self._tasks[task_id] = task
            self._evict_if_needed_locked()

        await self._broadcast(
            BroadcastEvent(
                event_type=TaskEventType.TASK_CREATED,
                task_id=task_id,
                data=self._task_summary(task),
                timestamp=now,
            ),
        )
        return task

    # -- update_task -------------------------------------------------------

    async def update_task(
        self,
        task_id: str,
        *,
        status: Optional[TaskStatus] = None,
        current_stage: Optional[str] = None,
        progress: Optional[float] = None,
        result_summary: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Task:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task not found: {task_id}")

            now = time.time()
            task.updated_at = now

            if status is not None:
                task.status = status
            if current_stage is not None:
                task.current_stage = current_stage
            if progress is not None:
                task.progress = progress
            if result_summary is not None:
                task.result_summary = result_summary
            if error is not None:
                task.error = error
            if metadata is not None:
                if task.metadata is None:
                    task.metadata = {}
                task.metadata.update(metadata)

            # Derive event type from status transition
            if status == TaskStatus.DONE:
                event_type = TaskEventType.TASK_DONE
                task.finished_at = now
            elif status == TaskStatus.FAILED:
                event_type = TaskEventType.TASK_FAILED
                task.finished_at = now
            elif status == TaskStatus.CANCELLED:
                event_type = TaskEventType.TASK_CANCELLED
                task.finished_at = now
            else:
                event_type = TaskEventType.TASK_UPDATED

            summary = self._task_summary(task)

        await self._broadcast(
            BroadcastEvent(
                event_type=event_type,
                task_id=task_id,
                data=summary,
                timestamp=now,
            ),
        )
        return task

    # -- add_event ---------------------------------------------------------

    async def add_event(
        self,
        task_id: str,
        event_type: TaskEventType,
        message: str,
        *,
        stage: Optional[str] = None,
        agent_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskEvent:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(f"Task not found: {task_id}")

            now = time.time()
            task.updated_at = now

            evt = TaskEvent(
                event_type=event_type,
                message=message,
                timestamp=now,
                stage=stage,
                agent_id=agent_id,
                metadata=dict(metadata) if metadata else None,
            )
            task.events.append(evt)
            # Cap events to bound memory for long-running tasks
            if len(task.events) > _MAX_EVENTS_PER_TASK:
                task.events = task.events[-_MAX_EVENTS_PER_TASK:]

        await self._broadcast(
            BroadcastEvent(
                event_type=event_type,
                task_id=task_id,
                data={
                    "message": message,
                    "stage": stage,
                    "agent_id": agent_id,
                },
                timestamp=now,
            ),
        )
        return evt

    # -- list_tasks --------------------------------------------------------

    async def list_tasks(
        self,
        *,
        limit: int = 100,
        status: Optional[TaskStatus] = None,
        session_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> List[Task]:
        async with self._lock:
            tasks = list(self._tasks.values())

        if status is not None:
            tasks = [t for t in tasks if t.status == status]
        if session_id is not None:
            tasks = [t for t in tasks if t.session_id == session_id]
        if tool_name is not None:
            tasks = [t for t in tasks if t.tool_name == tool_name]

        # Most recent first
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    # -- get_task ----------------------------------------------------------

    async def get_task(self, task_id: str) -> Optional[Task]:
        async with self._lock:
            return self._tasks.get(task_id)

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _task_summary(task: Task) -> Dict[str, Any]:
        return {
            "task_id": task.task_id,
            "session_id": task.session_id,
            "source": task.source,
            "title": task.title,
            "status": task.status.value,
            "current_stage": task.current_stage,
            "progress": task.progress,
            "tool_name": task.tool_name,
            "agent_id": task.agent_id,
            "result_summary": task.result_summary,
            "error": task.error,
            "created_at": task.created_at,
            "updated_at": task.updated_at,
            "finished_at": task.finished_at,
        }

    def _evict_if_needed_locked(self) -> None:
        """Drop oldest tasks if over capacity. Caller must hold _lock."""
        if len(self._tasks) <= self._max_tasks:
            return
        sorted_ids = sorted(
            self._tasks,
            key=lambda k: self._tasks[k].created_at,
        )
        while len(self._tasks) > self._max_tasks:
            del self._tasks[sorted_ids.pop(0)]

    async def _broadcast(self, event: BroadcastEvent) -> None:
        """Push event to all subscriber queues (non-blocking)."""
        dead: list[str] = []
        for sub_id, queue in list(self._subscribers.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug(
                    "task_monitor: dropping event %s for slow subscriber %s",
                    event.event_type.value,
                    sub_id,
                )
                dead.append(sub_id)
        for sub_id in dead:
            self._subscribers.pop(sub_id, None)
