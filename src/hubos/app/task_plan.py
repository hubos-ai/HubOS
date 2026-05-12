# -*- coding: utf-8 -*-
"""In-process TaskPlanStore for managing task plans with ordered steps.

A *task plan* is a sequence of steps that guides multi-agent execution.
Steps may declare dependencies (``depends_on``) so the executor can
determine the correct order.

This module is intentionally self-contained — it does not import from
any tool module or frontend code.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Queue / store limits
# ---------------------------------------------------------------------------

_SUBSCRIBER_QUEUE_MAXSIZE = 200


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PlanStatus(str, Enum):
    DRAFT = "draft"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanStepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanEventType(str, Enum):
    PLAN_CREATED = "plan_created"
    PLAN_UPDATED = "plan_updated"
    STEP_ADDED = "step_added"
    STEP_UPDATED = "step_updated"
    STEP_STARTED = "step_started"
    STEP_COMPLETED = "step_completed"
    STEP_FAILED = "step_failed"
    PLAN_CANCELLED = "plan_cancelled"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TaskPlanStep:
    step_id: str
    title: str
    status: PlanStepStatus
    order: int
    description: str = ""
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    depends_on: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: Optional[float] = None
    error: Optional[str] = None


@dataclass
class TaskPlan:
    plan_id: str
    session_id: str
    title: str
    status: PlanStatus
    steps: List[TaskPlanStep] = field(default_factory=list)
    current_step_id: Optional[str] = None
    created_at: float = 0.0
    updated_at: float = 0.0
    finished_at: Optional[float] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class PlanEvent:
    event_type: PlanEventType
    plan_id: str
    message: str
    timestamp: float
    step_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Broadcast envelope (for SSE subscribers)
# ---------------------------------------------------------------------------


@dataclass
class _BroadcastEvent:
    event_type: PlanEventType
    plan_id: str
    data: Dict[str, Any]
    timestamp: float


# ---------------------------------------------------------------------------
# TaskPlanStore
# ---------------------------------------------------------------------------


class TaskPlanStore:
    """Process-wide, async-safe task plan store.

    Usage::

        store = TaskPlanStore()
        plan = await store.create_plan(
            session_id="s1",
            title="Data pipeline",
        )
        step = await store.add_step(plan.plan_id, title="Extract")
        await store.update_plan(plan.plan_id, status=PlanStatus.RUNNING)
    """

    def __init__(self, max_plans: int = 10_000) -> None:
        self._plans: Dict[str, TaskPlan] = {}
        self._lock = asyncio.Lock()
        self._subscribers: Dict[str, asyncio.Queue[_BroadcastEvent]] = {}
        self._max_plans = max_plans

    # -- subscribe / unsubscribe ------------------------------------------

    def subscribe(self) -> tuple[str, asyncio.Queue[_BroadcastEvent]]:
        """Register a subscriber. Returns ``(subscriber_id, queue)``."""
        sub_id = uuid.uuid4().hex
        queue: asyncio.Queue[_BroadcastEvent] = asyncio.Queue(
            maxsize=_SUBSCRIBER_QUEUE_MAXSIZE,
        )
        self._subscribers[sub_id] = queue
        return sub_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a subscriber."""
        self._subscribers.pop(subscriber_id, None)

    # -- create_plan -------------------------------------------------------

    async def create_plan(
        self,
        session_id: str,
        title: str,
        *,
        steps: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        now = time.time()
        plan_id = uuid.uuid4().hex
        plan = TaskPlan(
            plan_id=plan_id,
            session_id=session_id,
            title=title,
            status=PlanStatus.DRAFT,
            created_at=now,
            updated_at=now,
            metadata=dict(metadata) if metadata else None,
        )

        # Add initial steps if provided
        if steps:
            for idx, s in enumerate(steps):
                step = TaskPlanStep(
                    step_id=uuid.uuid4().hex,
                    title=s.get("title", ""),
                    status=PlanStepStatus.PENDING,
                    order=s.get("order", idx),
                    description=s.get("description", ""),
                    agent_id=s.get("agent_id"),
                    tool_name=s.get("tool_name"),
                    depends_on=list(s.get("depends_on", [])),
                    metadata=dict(s["metadata"])
                    if s.get("metadata")
                    else None,
                    created_at=now,
                    updated_at=now,
                )
                plan.steps.append(step)

        async with self._lock:
            self._plans[plan_id] = plan
            self._evict_if_needed_locked()

        await self._broadcast(
            _BroadcastEvent(
                event_type=PlanEventType.PLAN_CREATED,
                plan_id=plan_id,
                data=self._plan_summary(plan),
                timestamp=now,
            ),
        )
        return plan

    # -- get_plan ----------------------------------------------------------

    async def get_plan(self, plan_id: str) -> Optional[TaskPlan]:
        async with self._lock:
            return self._plans.get(plan_id)

    # -- list_plans --------------------------------------------------------

    async def list_plans(
        self,
        *,
        limit: int = 100,
        status: Optional[PlanStatus] = None,
        session_id: Optional[str] = None,
    ) -> List[TaskPlan]:
        async with self._lock:
            plans = list(self._plans.values())

        if status is not None:
            plans = [p for p in plans if p.status == status]
        if session_id is not None:
            plans = [p for p in plans if p.session_id == session_id]

        plans.sort(key=lambda p: p.created_at, reverse=True)
        return plans[:limit]

    # -- add_step ----------------------------------------------------------

    _TERMINAL_STATUSES = frozenset(
        (
            PlanStatus.DONE,
            PlanStatus.FAILED,
            PlanStatus.CANCELLED,
        )
    )

    async def add_step(
        self,
        plan_id: str,
        title: str,
        description: str = "",
        *,
        agent_id: Optional[str] = None,
        tool_name: Optional[str] = None,
        depends_on: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        after_step_id: Optional[str] = None,
    ) -> TaskPlanStep:
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise KeyError(f"Plan not found: {plan_id}")

            if plan.status in self._TERMINAL_STATUSES:
                raise ValueError(
                    f"Cannot add step to plan in terminal status '{plan.status.value}'",
                )

            now = time.time()

            if after_step_id is not None:
                # Find target step and insert after it
                target_idx = None
                for i, s in enumerate(plan.steps):
                    if s.step_id == after_step_id:
                        target_idx = i
                        break
                if target_idx is None:
                    raise KeyError(f"Step not found: {after_step_id}")

                insert_order = plan.steps[target_idx].order + 1
                # Shift subsequent steps
                for s in plan.steps[target_idx + 1 :]:
                    s.order += 1
                # Re-sort by order
                plan.steps.sort(key=lambda s: s.order)
            else:
                insert_order = len(plan.steps)

            step = TaskPlanStep(
                step_id=uuid.uuid4().hex,
                title=title,
                status=PlanStepStatus.PENDING,
                order=insert_order,
                description=description,
                agent_id=agent_id,
                tool_name=tool_name,
                depends_on=list(depends_on) if depends_on else [],
                metadata=dict(metadata) if metadata else None,
                created_at=now,
                updated_at=now,
            )

            if after_step_id is not None and target_idx is not None:
                plan.steps.insert(target_idx + 1, step)
            else:
                plan.steps.append(step)

            plan.updated_at = now

        await self._broadcast(
            _BroadcastEvent(
                event_type=PlanEventType.STEP_ADDED,
                plan_id=plan_id,
                data=self._step_summary(step),
                timestamp=now,
            ),
        )
        return step

    # -- update_step -------------------------------------------------------

    async def update_step(
        self,
        plan_id: str,
        step_id: str,
        *,
        status: Optional[PlanStepStatus] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPlanStep:
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise KeyError(f"Plan not found: {plan_id}")

            step = _find_step(plan, step_id)
            if step is None:
                raise KeyError(f"Step not found: {step_id}")

            now = time.time()
            step.updated_at = now

            if status is not None:
                step.status = status
            if title is not None:
                step.title = title
            if description is not None:
                step.description = description
            if error is not None:
                step.error = error
            if metadata is not None:
                if step.metadata is None:
                    step.metadata = {}
                step.metadata.update(metadata)

            # Set finished_at for terminal statuses
            if status in (
                PlanStepStatus.DONE,
                PlanStepStatus.FAILED,
                PlanStepStatus.CANCELLED,
            ):
                step.finished_at = now

            plan.updated_at = now

        # Derive event type
        if status == PlanStepStatus.RUNNING:
            event_type = PlanEventType.STEP_STARTED
        elif status == PlanStepStatus.DONE:
            event_type = PlanEventType.STEP_COMPLETED
        elif status == PlanStepStatus.FAILED:
            event_type = PlanEventType.STEP_FAILED
        else:
            event_type = PlanEventType.STEP_UPDATED

        await self._broadcast(
            _BroadcastEvent(
                event_type=event_type,
                plan_id=plan_id,
                data=self._step_summary(step),
                timestamp=now,
            ),
        )
        return step

    # -- update_plan -------------------------------------------------------

    _UNSET = object()  # sentinel for "not provided"

    async def update_plan(
        self,
        plan_id: str,
        *,
        status: Optional[PlanStatus] = None,
        current_step_id: Optional[str] = _UNSET,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TaskPlan:
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise KeyError(f"Plan not found: {plan_id}")

            now = time.time()
            plan.updated_at = now

            if status is not None:
                plan.status = status
            if current_step_id is not self._UNSET:
                plan.current_step_id = (
                    current_step_id  # can be set to None explicitly
                )
            if metadata is not None:
                if plan.metadata is None:
                    plan.metadata = {}
                plan.metadata.update(metadata)

            # Set finished_at for terminal statuses
            if status in (
                PlanStatus.DONE,
                PlanStatus.FAILED,
                PlanStatus.CANCELLED,
            ):
                plan.finished_at = now

            summary = self._plan_summary(plan)

        await self._broadcast(
            _BroadcastEvent(
                event_type=PlanEventType.PLAN_UPDATED,
                plan_id=plan_id,
                data=summary,
                timestamp=now,
            ),
        )
        return plan

    # -- pause_plan --------------------------------------------------------

    async def pause_plan(self, plan_id: str) -> TaskPlan:
        """Pause a running plan by setting status to WAITING_USER."""
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise KeyError(f"Plan not found: {plan_id}")
            if plan.status != PlanStatus.RUNNING:
                raise ValueError(
                    f"Cannot pause plan in status '{plan.status.value}', expected 'running'",
                )

            now = time.time()
            plan.status = PlanStatus.WAITING_USER
            plan.updated_at = now
            summary = self._plan_summary(plan)

        await self._broadcast(
            _BroadcastEvent(
                event_type=PlanEventType.PLAN_UPDATED,
                plan_id=plan_id,
                data=summary,
                timestamp=now,
            ),
        )
        return plan

    # -- resume_plan -------------------------------------------------------

    async def resume_plan(self, plan_id: str) -> TaskPlan:
        """Resume a paused plan by setting status back to RUNNING."""
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise KeyError(f"Plan not found: {plan_id}")
            if plan.status != PlanStatus.WAITING_USER:
                raise ValueError(
                    f"Cannot resume plan in status '{plan.status.value}', expected 'waiting_user'",
                )

            now = time.time()
            plan.status = PlanStatus.RUNNING
            plan.updated_at = now
            summary = self._plan_summary(plan)

        await self._broadcast(
            _BroadcastEvent(
                event_type=PlanEventType.PLAN_UPDATED,
                plan_id=plan_id,
                data=summary,
                timestamp=now,
            ),
        )
        return plan

    # -- cancel_plan -------------------------------------------------------

    async def cancel_plan(self, plan_id: str) -> TaskPlan:
        async with self._lock:
            plan = self._plans.get(plan_id)
            if plan is None:
                raise KeyError(f"Plan not found: {plan_id}")

            now = time.time()
            plan.status = PlanStatus.CANCELLED
            plan.updated_at = now
            plan.finished_at = now

            # Cancel all non-terminal steps
            for step in plan.steps:
                if step.status not in (
                    PlanStepStatus.DONE,
                    PlanStepStatus.FAILED,
                    PlanStepStatus.CANCELLED,
                ):
                    step.status = PlanStepStatus.CANCELLED
                    step.updated_at = now
                    step.finished_at = now

        await self._broadcast(
            _BroadcastEvent(
                event_type=PlanEventType.PLAN_CANCELLED,
                plan_id=plan_id,
                data=self._plan_summary(plan),
                timestamp=now,
            ),
        )
        return plan

    # -- internal helpers --------------------------------------------------

    @staticmethod
    def _step_summary(step: TaskPlanStep) -> Dict[str, Any]:
        return {
            "step_id": step.step_id,
            "title": step.title,
            "status": step.status.value,
            "order": step.order,
            "description": step.description,
            "agent_id": step.agent_id,
            "tool_name": step.tool_name,
            "depends_on": step.depends_on,
            "error": step.error,
            "created_at": step.created_at,
            "updated_at": step.updated_at,
            "finished_at": step.finished_at,
        }

    @staticmethod
    def _plan_summary(plan: TaskPlan) -> Dict[str, Any]:
        return {
            "plan_id": plan.plan_id,
            "session_id": plan.session_id,
            "title": plan.title,
            "status": plan.status.value,
            "current_step_id": plan.current_step_id,
            "step_count": len(plan.steps),
            "created_at": plan.created_at,
            "updated_at": plan.updated_at,
            "finished_at": plan.finished_at,
        }

    def _evict_if_needed_locked(self) -> None:
        """Drop oldest plans if over capacity. Caller must hold _lock."""
        if len(self._plans) <= self._max_plans:
            return
        sorted_ids = sorted(
            self._plans,
            key=lambda k: self._plans[k].created_at,
        )
        while len(self._plans) > self._max_plans:
            del self._plans[sorted_ids.pop(0)]

    async def _broadcast(self, event: _BroadcastEvent) -> None:
        """Push event to all subscriber queues (non-blocking)."""
        dead: list[str] = []
        for sub_id, queue in list(self._subscribers.items()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug(
                    "task_plan: dropping event %s for slow subscriber %s",
                    event.event_type.value,
                    sub_id,
                )
                dead.append(sub_id)
        for sub_id in dead:
            self._subscribers.pop(sub_id, None)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[TaskPlanStore] = None


def get_plan_store() -> TaskPlanStore:
    """Return (and lazily create) the global TaskPlanStore."""
    global _store
    if _store is None:
        _store = TaskPlanStore()
    return _store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_step(plan: TaskPlan, step_id: str) -> Optional[TaskPlanStep]:
    for step in plan.steps:
        if step.step_id == step_id:
            return step
    return None
