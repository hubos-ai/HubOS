# -*- coding: utf-8 -*-
"""Execution Loop MVP - Task Execution Services.

This module provides task execution capabilities for the platform.
"""

# Import components directly
from hubos.core.execution.task_store import (
    TaskStore,
    Task,
    TaskStatus,
    TaskStage,
    StageStatus,
)
from hubos.core.execution.event_store import (
    EventStore,
    ExecutionEvent,
    EventType,
)
from hubos.core.execution.orchestrator import ExecutionOrchestrator

__all__ = [
    # Task store
    "TaskStore",
    "Task",
    "TaskStatus",
    "TaskStage",
    "StageStatus",
    # Event store
    "EventStore",
    "ExecutionEvent",
    "EventType",
    # Orchestrator
    "ExecutionOrchestrator",
    # Factory functions
    "get_task_store",
    "get_event_store",
    "get_orchestrator",
]

# Global instances
_task_store: TaskStore = None
_event_store: EventStore = None
_orchestrator: ExecutionOrchestrator = None


def get_task_store() -> TaskStore:
    """Get global task store instance."""
    global _task_store
    if _task_store is None:
        _task_store = TaskStore()
    return _task_store


def get_event_store() -> EventStore:
    """Get global event store instance."""
    global _event_store
    if _event_store is None:
        _event_store = EventStore()
    return _event_store


def get_orchestrator() -> ExecutionOrchestrator:
    """Get global orchestrator instance."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = ExecutionOrchestrator(
            task_store=get_task_store(),
            event_store=get_event_store(),
        )
    return _orchestrator
