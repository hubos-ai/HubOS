# -*- coding: utf-8 -*-
"""Execution Loop MVP - Event Store.

Stores time-ordered execution events for audit trails.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


class EventType(str, Enum):
    """Event types for execution audit trail."""

    TASK_SUBMITTED = "task_submitted"
    STATE_TRANSITION = "state_transition"
    STAGE_DISPATCH = "stage_dispatch"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"
    STAGE_SKIPPED = "stage_skipped"
    WORKER_RESULT = "worker_result"
    HUMAN_GATE_ENTERED = "human_gate_entered"
    HUMAN_GATE_RESOLVED = "human_gate_resolved"
    MERGE_COMPLETED = "merge_completed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TIMEOUT = "timeout"
    # Parallel Core V1.5 Step 1: Parallel execution events
    BRANCH_DISPATCH = "branch_dispatch"
    BRANCH_RUNNING = "branch_running"
    BRANCH_COMPLETED = "branch_completed"
    BRANCH_FAILED = "branch_failed"
    BRANCH_RETRY_SCHEDULED = "branch_retry_scheduled"
    MERGE_STARTED = "merge_started"
    MERGE_HUMAN_GATE = "merge_human_gate"
    BACKEND_FALLBACK = "backend_fallback"
    # Phase 4: Work Experience Layer
    WORK_EXPERIENCE_RETRIEVED = "work_experience_retrieved"


@dataclass
class ExecutionEvent:
    """Single execution event."""

    event_id: str
    task_id: str
    trace_id: str
    event_type: EventType
    timestamp: datetime
    stage: Optional[str] = None
    agent_id: Optional[str] = None
    from_status: Optional[str] = None
    to_status: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "stage": self.stage,
            "agent_id": self.agent_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "data": self.data,
            "error_code": self.error_code,
        }


class EventStore:
    """Thread-safe event storage for execution audit trails."""

    def __init__(self) -> None:
        """Initialize event store."""
        self._events: dict[str, list[ExecutionEvent]] = {}
        self._lock = threading.Lock()

    def add_event(
        self,
        task_id: str,
        trace_id: str,
        event_type: EventType,
        stage: Optional[str] = None,
        agent_id: Optional[str] = None,
        from_status: Optional[str] = None,
        to_status: Optional[str] = None,
        data: Optional[dict[str, Any]] = None,
        error_code: Optional[str] = None,
    ) -> ExecutionEvent:
        """Add a new event to the audit trail."""
        event = ExecutionEvent(
            event_id=f"evt-{uuid.uuid4().hex[:12]}",
            task_id=task_id,
            trace_id=trace_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            stage=stage,
            agent_id=agent_id,
            from_status=from_status,
            to_status=to_status,
            data=data,
            error_code=error_code,
        )

        with self._lock:
            if task_id not in self._events:
                self._events[task_id] = []
            self._events[task_id].append(event)

        return event

    def get_events(
        self,
        task_id: str,
        limit: int = 100,
    ) -> list[ExecutionEvent]:
        """Get events for a task in chronological order."""
        with self._lock:
            events = self._events.get(task_id, [])
            return events[-limit:]

    def get_events_by_type(
        self,
        task_id: str,
        event_type: EventType,
    ) -> list[ExecutionEvent]:
        """Get events of a specific type for a task."""
        with self._lock:
            events = self._events.get(task_id, [])
            return [e for e in events if e.event_type == event_type]

    def clear_task_events(self, task_id: str) -> None:
        """Clear all events for a task (for testing)."""
        with self._lock:
            if task_id in self._events:
                del self._events[task_id]

    def add_parallel_event(self, event: Any) -> None:
        """
        Add a parallel execution event to the audit trail.

        Args:
            event: ParallelEvent from CAMEL callbacks
        """
        from dataclasses import dataclass

        # Convert ParallelEvent to ExecutionEvent for unified storage
        exec_event = ExecutionEvent(
            event_id=event.event_id,
            task_id=event.task_id,
            trace_id=event.trace_id,
            event_type=EventType(event.event_type),
            timestamp=event.timestamp,
            stage=event.branch_id,  # Reuse stage field for branch_id
            agent_id=event.role,  # Reuse agent_id field for role
            data=event.data,
            error_code=event.error_code,
        )

        with self._lock:
            if event.task_id not in self._events:
                self._events[event.task_id] = []
            self._events[event.task_id].append(exec_event)
