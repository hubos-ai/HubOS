# -*- coding: utf-8 -*-
"""Execution Loop MVP - Task Store.

In-memory task storage with local store authoritative semantics.
"""

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional


class TaskStatus(str, Enum):
    """Task execution status."""

    RECEIVED = "received"
    PLANNED = "planned"
    RUNNING = "running"
    HUMAN_GATE = "human_gate"
    DONE = "done"
    FAILED = "failed"


class TaskStage(str, Enum):
    """Workflow stages."""

    CEO = "ceo"
    INFO = "info"
    DEV = "dev"
    REVIEW = "review"
    SUMMARY = "summary"


@dataclass
class StageStatus:
    """Status of a single stage."""

    stage: TaskStage
    status: str  # pending, running, completed, skipped, failed
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    output: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    retry_count: int = 0


@dataclass
class Task:
    """Task execution record."""

    task_id: str
    trace_id: str
    input_text: str
    session_id: Optional[str] = None
    channel: Optional[str] = None
    priority: str = "normal"  # low, normal, high
    requested_workflow: str = "one_person_default"
    current_status: TaskStatus = TaskStatus.RECEIVED
    stage_statuses: dict[str, StageStatus] = field(default_factory=dict)
    final_response: Optional[dict[str, Any]] = None
    failure_reason: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # Whether this task requires human approval at a gate
    requires_human: bool = False
    # Phase 4: Work Experience Layer — retrieved experience cards for this task
    work_experience_cards: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses."""
        return {
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "input_text": self.input_text,
            "session_id": self.session_id,
            "channel": self.channel,
            "priority": self.priority,
            "requested_workflow": self.requested_workflow,
            "current_status": self.current_status.value,
            "stage_statuses": {
                k: {
                    "stage": v.stage.value,
                    "status": v.status,
                    "started_at": v.started_at.isoformat()
                    if v.started_at
                    else None,
                    "completed_at": v.completed_at.isoformat()
                    if v.completed_at
                    else None,
                    "output": v.output,
                    "error": v.error,
                    "retry_count": v.retry_count,
                }
                for k, v in self.stage_statuses.items()
            },
            "final_response": self.final_response,
            "failure_reason": self.failure_reason,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "created_at": self.created_at.isoformat(),
            "started_at": self.started_at.isoformat()
            if self.started_at
            else None,
            "updated_at": self.updated_at.isoformat()
            if self.updated_at
            else None,
            "completed_at": self.completed_at.isoformat()
            if self.completed_at
            else None,
            "requires_human": self.requires_human,
            "work_experience_cards": self.work_experience_cards,
        }


class DeadLetterQueue:
    """Dead letter queue for failed tasks."""

    def __init__(self) -> None:
        """Initialize DLQ."""
        self._entries: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def add(
        self,
        task_id: str,
        reason: str,
        task_data: dict[str, Any],
    ) -> None:
        """Add a failed task to the DLQ."""
        with self._lock:
            self._entries[task_id] = {
                "task_id": task_id,
                "reason": reason,
                "task_data": task_data,
                "added_at": datetime.now(timezone.utc).isoformat(),
            }

    def get(self, task_id: str) -> Optional[dict[str, Any]]:
        """Get DLQ entry by task ID."""
        with self._lock:
            return self._entries.get(task_id)

    def list_all(self) -> list[dict[str, Any]]:
        """List all DLQ entries."""
        with self._lock:
            return list(self._entries.values())

    def remove(self, task_id: str) -> bool:
        """Remove a task from DLQ (e.g., after retry)."""
        with self._lock:
            if task_id in self._entries:
                del self._entries[task_id]
                return True
            return False

    def count(self) -> int:
        """Get DLQ entry count."""
        with self._lock:
            return len(self._entries)


class TaskStore:
    """Thread-safe in-memory task storage."""

    def __init__(self) -> None:
        """Initialize task store."""
        self._tasks: dict[str, Task] = {}
        self._lock = threading.Lock()
        self._dlq = DeadLetterQueue()
        self._default_max_retries = 3
        self._task_timeout_seconds = 300  # 5 minutes default

    @property
    def dlq(self) -> DeadLetterQueue:
        """Get the dead letter queue."""
        return self._dlq

    def create_task(
        self,
        input_text: str,
        session_id: Optional[str] = None,
        channel: Optional[str] = None,
        priority: str = "normal",
        requested_workflow: str = "one_person_default",
    ) -> Task:
        """Create a new task."""
        task_id = f"task-{uuid.uuid4().hex[:12]}"
        trace_id = f"trace-{uuid.uuid4().hex[:16]}"

        task = Task(
            task_id=task_id,
            trace_id=trace_id,
            input_text=input_text,
            session_id=session_id,
            channel=channel,
            priority=priority,
            requested_workflow=requested_workflow,
            current_status=TaskStatus.RECEIVED,
            created_at=datetime.now(timezone.utc),
        )

        with self._lock:
            self._tasks[task_id] = task

        return task

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def update_status(
        self,
        task_id: str,
        status: TaskStatus,
        failure_reason: Optional[str] = None,
        final_response: Optional[dict[str, Any]] = None,
    ) -> Optional[Task]:
        """Update task status."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            task.current_status = status
            task.updated_at = datetime.now(timezone.utc)

            if status == TaskStatus.RUNNING and task.started_at is None:
                task.started_at = datetime.now(timezone.utc)

            if status in (TaskStatus.DONE, TaskStatus.FAILED):
                task.completed_at = datetime.now(timezone.utc)

            if failure_reason is not None:
                task.failure_reason = failure_reason

            if final_response is not None:
                task.final_response = final_response

            return task

    def update_stage_status(
        self,
        task_id: str,
        stage: TaskStage,
        status: str,
        output: Optional[dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Optional[Task]:
        """Update stage status within a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            stage_key = stage.value
            if stage_key not in task.stage_statuses:
                task.stage_statuses[stage_key] = StageStatus(
                    stage=stage,
                    status=status,
                )

            stage_status = task.stage_statuses[stage_key]
            stage_status.status = status
            stage_status.output = output
            stage_status.error = error

            if status == "running":
                stage_status.started_at = datetime.now(timezone.utc)
            elif status in ("completed", "skipped", "failed"):
                stage_status.completed_at = datetime.now(timezone.utc)

            task.updated_at = datetime.now(timezone.utc)
            return task

    def set_requires_human(
        self,
        task_id: str,
        requires_human: bool,
    ) -> Optional[Task]:
        """Set the requires_human flag on a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.requires_human = requires_human
            task.updated_at = datetime.now(timezone.utc)
            return task

    def list_tasks(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
    ) -> list[Task]:
        """List tasks in reverse chronological order.

        Args:
            limit: Max tasks to return.
            offset: Skip first N tasks.
            status: Optional status filter (received, planned, running,
                   human_gate, done, failed).
        """
        with self._lock:
            tasks = list(self._tasks.values())

            # Apply status filter if provided
            if status:
                status_upper = status.upper()
                # Map HubOS-style status names to TaskStatus
                status_map = {
                    "PENDING": TaskStatus.RECEIVED,
                    "RECEIVED": TaskStatus.RECEIVED,
                    "PLANNED": TaskStatus.PLANNED,
                    "RUNNING": TaskStatus.RUNNING,
                    "HUMAN_GATE": TaskStatus.HUMAN_GATE,
                    "DONE": TaskStatus.DONE,
                    "COMPLETED": TaskStatus.DONE,
                    "FAILED": TaskStatus.FAILED,
                }
                target = status_map.get(status_upper)
                if target:
                    tasks = [t for t in tasks if t.current_status == target]

            sorted_tasks = sorted(
                tasks,
                key=lambda t: t.created_at,
                reverse=True,
            )
            return sorted_tasks[offset : offset + limit]

    def count(self) -> int:
        """Get total task count."""
        with self._lock:
            return len(self._tasks)

    def get_summary(self) -> dict[str, int]:
        """Get task summary counts by status.

        Returns counts for HubOS TaskSummary fields:
        - pending: received + planned
        - running: running (excludes human_gate)
        - human_gate: requires_human=True (waiting for human approval)
        - done: done
        - failed: failed

        solo-hub status -> HubOS status mapping:
          RECEIVED, PLANNED -> pending
          RUNNING           -> running (but NOT human_gate)
          HUMAN_GATE        -> human_gate (requires_human flag set)
          DONE              -> done
          FAILED            -> failed
        """
        with self._lock:
            pending = 0
            running = 0
            human_gate = 0
            done = 0
            failed = 0

            for task in self._tasks.values():
                status = task.current_status
                if (
                    status == TaskStatus.RECEIVED
                    or status == TaskStatus.PLANNED
                ):
                    pending += 1
                elif status == TaskStatus.RUNNING:
                    if task.requires_human:
                        human_gate += 1
                    else:
                        running += 1
                elif status == TaskStatus.HUMAN_GATE:
                    # Explicit human_gate status always counts as human_gate
                    human_gate += 1
                elif status == TaskStatus.DONE:
                    done += 1
                elif status == TaskStatus.FAILED:
                    failed += 1

            return {
                "pending": pending,
                "running": running,
                "human_gate": human_gate,
                "done": done,
                "failed": failed,
            }

    def retry_task(self, task_id: str) -> Optional[Task]:
        """Retry a failed task by resetting its status."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            if task.current_status != TaskStatus.FAILED:
                return None

            if task.retry_count >= task.max_retries:
                return None

            # Reset task for retry
            task.current_status = TaskStatus.RECEIVED
            task.failure_reason = None
            task.final_response = None
            task.retry_count += 1
            task.started_at = None
            task.completed_at = None
            task.updated_at = datetime.now(timezone.utc)

            # Clear stage statuses
            for stage_status in task.stage_statuses.values():
                stage_status.status = "pending"
                stage_status.started_at = None
                stage_status.completed_at = None
                stage_status.output = None
                stage_status.error = None

            # Remove from DLQ if present
            self._dlq.remove(task_id)

            return task

    def get_stuck_tasks(
        self,
        timeout_seconds: Optional[int] = None,
    ) -> list[Task]:
        """Get tasks that appear stuck (running too long)."""
        timeout = timeout_seconds or self._task_timeout_seconds
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout)
        stuck = []

        with self._lock:
            for task in self._tasks.values():
                if task.current_status == TaskStatus.RUNNING:
                    if task.started_at and task.started_at < cutoff:
                        stuck.append(task)

        return stuck

    def get_failed_tasks(self) -> list[Task]:
        """Get all failed tasks."""
        with self._lock:
            return [
                t
                for t in self._tasks.values()
                if t.current_status == TaskStatus.FAILED
            ]

    def move_to_dlq(self, task_id: str, reason: str) -> bool:
        """Move a task to the dead letter queue."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False

            # Add to DLQ
            self._dlq.add(task_id, reason, task.to_dict())

            # Update task status
            task.current_status = TaskStatus.FAILED
            task.failure_reason = f"DLQ: {reason}"
            task.completed_at = datetime.now(timezone.utc)
            task.updated_at = datetime.now(timezone.utc)

            return True

    def set_max_retries(
        self,
        task_id: str,
        max_retries: int,
    ) -> Optional[Task]:
        """Set max retries for a specific task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            task.max_retries = max_retries
            return task

    def reset_task(self, task_id: str) -> Optional[Task]:
        """Force reset a task to received state (for manual recovery)."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None

            task.current_status = TaskStatus.RECEIVED
            task.failure_reason = None
            task.final_response = None
            task.started_at = None
            task.completed_at = None
            task.updated_at = datetime.now(timezone.utc)

            # Clear stage statuses
            for stage_status in task.stage_statuses.values():
                stage_status.status = "pending"
                stage_status.started_at = None
                stage_status.completed_at = None
                stage_status.output = None
                stage_status.error = None

            # Remove from DLQ if present
            self._dlq.remove(task_id)

            return task
