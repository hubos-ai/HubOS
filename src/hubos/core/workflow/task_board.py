# -*- coding: utf-8 -*-
"""Task Board Service - Minimal Usable Task Tracking.

Provides:
- Task board state tracking (received/planned/running/human_gate/done/failed)
- Task card information (task_id, stage, assigned_agent, age, last_error)
- Recovery operations (retry, requeue, resolve_human_gate)
- Audit logging for recovery actions
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class BoardColumn(str, Enum):
    """Task board columns."""

    RECEIVED = "received"
    PLANNED = "planned"
    RUNNING = "running"
    HUMAN_GATE = "human_gate"
    DONE = "done"
    FAILED = "failed"


@dataclass
class TaskCard:
    """Task card for the board."""

    task_id: str
    column: BoardColumn
    current_stage: str
    assigned_agent: Optional[str]
    created_at: datetime
    last_error: Optional[str] = None
    retry_count: int = 0
    trace_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecoveryAction:
    """Record of a recovery action."""

    action_id: str
    action_type: str  # retry, requeue, resolve_human_gate
    target_task_id: str
    actor: str
    reason: str
    timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    outcome: str = "pending"  # pending, success, failed
    details: dict[str, Any] = field(default_factory=dict)


class TaskBoardService:
    """
    Task board service for tracking task states.

    Provides a minimal kanban-style view of tasks across all stages.

    Usage:
        board = TaskBoardService()
        board.add_task(task_id="t1", stage="running", agent="dev-1")
        cards = board.get_column(BoardColumn.RUNNING)
        board.retry_task("t1", actor="admin")
    """

    def __init__(self) -> None:
        """Initialize task board service."""
        self._tasks: dict[str, TaskCard] = {}
        self._recovery_log: list[RecoveryAction] = []
        self._lock_log: list[dict[str, Any]] = []

    def add_task(
        self,
        task_id: str,
        current_stage: str,
        assigned_agent: Optional[str] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> TaskCard:
        """Add a task to the board in RECEIVED column."""
        card = TaskCard(
            task_id=task_id,
            column=BoardColumn.RECEIVED,
            current_stage=current_stage,
            assigned_agent=assigned_agent,
            created_at=datetime.now(timezone.utc),
            trace_id=trace_id,
            session_id=session_id,
        )
        self._tasks[task_id] = card
        logger.info(
            f"Task {task_id} added to board at {BoardColumn.RECEIVED.value}",
        )
        return card

    def move_task(
        self,
        task_id: str,
        to_column: BoardColumn,
        error: Optional[str] = None,
    ) -> Optional[TaskCard]:
        """Move a task to a different column."""
        card = self._tasks.get(task_id)
        if not card:
            logger.warning(f"Task {task_id} not found on board")
            return None

        old_column = card.column
        card.column = to_column

        if error:
            card.last_error = error

        logger.info(
            f"Task {task_id} moved from {old_column.value} to {to_column.value}",
        )

        if to_column == BoardColumn.FAILED:
            logger.warning(f"Task {task_id} failed: {error}")

        if to_column == BoardColumn.HUMAN_GATE:
            logger.warning(
                f"Task {task_id} requires human intervention: {error}",
            )

        return card

    def get_task(self, task_id: str) -> Optional[TaskCard]:
        """Get a task card by ID."""
        return self._tasks.get(task_id)

    def get_column(self, column: BoardColumn) -> list[TaskCard]:
        """Get all tasks in a column."""
        return [card for card in self._tasks.values() if card.column == column]

    def get_all_tasks(self) -> list[TaskCard]:
        """Get all tasks."""
        return list(self._tasks.values())

    def get_board_summary(self) -> dict[str, Any]:
        """Get a summary of the board state."""
        summary: dict[str, int] = {}
        for column in BoardColumn:
            summary[column.value] = len(self.get_column(column))

        return {
            "columns": summary,
            "total_tasks": len(self._tasks),
            "failed_count": summary.get(BoardColumn.FAILED.value, 0),
            "human_gate_count": summary.get(BoardColumn.HUMAN_GATE.value, 0),
        }

    def retry_task(
        self,
        task_id: str,
        actor: str = "system",
        reason: str = "Manual retry",
    ) -> RecoveryAction:
        """
        Retry a failed task by moving it back to RECEIVED.

        Args:
            task_id: Task to retry
            actor: Who initiated the retry
            reason: Reason for retry

        Returns:
            RecoveryAction record
        """
        card = self._tasks.get(task_id)
        if not card:
            action = RecoveryAction(
                action_id=str(uuid4())[:8],
                action_type="retry",
                target_task_id=task_id,
                actor=actor,
                reason=reason,
                outcome="failed",
                details={"error": "Task not found"},
            )
            self._recovery_log.append(action)
            return action

        action = RecoveryAction(
            action_id=str(uuid4())[:8],
            action_type="retry",
            target_task_id=task_id,
            actor=actor,
            reason=reason,
            outcome="pending",
        )
        self._recovery_log.append(action)

        # Move task back to received
        card.last_error = None
        card.retry_count += 1
        self.move_task(task_id, BoardColumn.RECEIVED)

        action.outcome = "success"
        action.details = {
            "retry_count": card.retry_count,
            "previous_column": BoardColumn.FAILED.value,
        }

        logger.info(
            f"Task {task_id} retried by {actor}, retry count: {card.retry_count}",
        )

        return action

    def requeue_from_dlq(
        self,
        task_id: str,
        actor: str = "system",
        reason: str = "DLQ requeue",
    ) -> RecoveryAction:
        """
        Requeue a task from the Dead Letter Queue.

        Args:
            task_id: Task to requeue
            actor: Who initiated the requeue
            reason: Reason for requeue

        Returns:
            RecoveryAction record
        """
        card = self._tasks.get(task_id)
        if not card:
            action = RecoveryAction(
                action_id=str(uuid4())[:8],
                action_type="requeue",
                target_task_id=task_id,
                actor=actor,
                reason=reason,
                outcome="failed",
                details={"error": "Task not found in DLQ"},
            )
            self._recovery_log.append(action)
            return action

        action = RecoveryAction(
            action_id=str(uuid4())[:8],
            action_type="requeue",
            target_task_id=task_id,
            actor=actor,
            reason=reason,
            outcome="pending",
        )
        self._recovery_log.append(action)

        # Move to received
        card.last_error = None
        self.move_task(task_id, BoardColumn.RECEIVED)

        action.outcome = "success"
        action.details = {"previous_column": BoardColumn.FAILED.value}

        logger.info(f"Task {task_id} requeued from DLQ by {actor}")

        return action

    def resolve_human_gate(
        self,
        task_id: str,
        actor: str = "system",
        resolution: str = "Approved",
    ) -> RecoveryAction:
        """
        Resolve a task stuck in human gate.

        Args:
            task_id: Task to resolve
            actor: Who resolved it
            resolution: Resolution description

        Returns:
            RecoveryAction record
        """
        card = self._tasks.get(task_id)
        if not card:
            action = RecoveryAction(
                action_id=str(uuid4())[:8],
                action_type="resolve_human_gate",
                target_task_id=task_id,
                actor=actor,
                reason=resolution,
                outcome="failed",
                details={"error": "Task not found"},
            )
            self._recovery_log.append(action)
            return action

        action = RecoveryAction(
            action_id=str(uuid4())[:8],
            action_type="resolve_human_gate",
            target_task_id=task_id,
            actor=actor,
            reason=resolution,
            outcome="pending",
        )
        self._recovery_log.append(action)

        # Move to done or running based on resolution
        if "reject" in resolution.lower():
            self.move_task(
                task_id,
                BoardColumn.FAILED,
                error=f"Rejected: {resolution}",
            )
        else:
            self.move_task(task_id, BoardColumn.RUNNING)

        action.outcome = "success"
        action.details = {"resolution": resolution}

        logger.info(
            f"Task {task_id} human gate resolved by {actor}: {resolution}",
        )

        return action

    def get_recovery_log(
        self,
        task_id: Optional[str] = None,
        action_type: Optional[str] = None,
        limit: int = 50,
    ) -> list[RecoveryAction]:
        """Get recovery action log, optionally filtered."""
        results = self._recovery_log

        if task_id:
            results = [r for r in results if r.target_task_id == task_id]

        if action_type:
            results = [r for r in results if r.action_type == action_type]

        return results[-limit:]

    def get_failed_tasks(self) -> list[TaskCard]:
        """Get all failed tasks that can be retried."""
        return self.get_column(BoardColumn.FAILED)

    def get_human_gate_tasks(self) -> list[TaskCard]:
        """Get all tasks in human gate."""
        return self.get_column(BoardColumn.HUMAN_GATE)

    def get_blocked_count(self) -> int:
        """Get count of blocked tasks (failed + human_gate)."""
        return len(self.get_column(BoardColumn.FAILED)) + len(
            self.get_column(BoardColumn.HUMAN_GATE),
        )


# Global service instance
_board_service: Optional[TaskBoardService] = None


def get_task_board() -> TaskBoardService:
    """Get the global task board service."""
    global _board_service
    if _board_service is None:
        _board_service = TaskBoardService()
    return _board_service
