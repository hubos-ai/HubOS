"""Versioned schemas for solo-hub coordinator system."""

from hubos.core.schemas.collaboration import CollaborationMessage, MessageType
from hubos.core.schemas.events import ConversationEvent, SourceMetadata
from hubos.core.schemas.planning import ExecutionPlan, PlanStep, PlanStepType
from hubos.core.schemas.tasks import TaskUnit, TaskResult, TaskStatus
from hubos.core.schemas.responses import FinalResponse, MergeResult
from hubos.core.schemas.memory import MemoryContext, MemoryUpdate, MemoryNamespace
from hubos.core.schemas.state import TaskState, TaskStateMachine

__all__ = [
    "CollaborationMessage",
    "MessageType",
    "ConversationEvent",
    "SourceMetadata",
    "ExecutionPlan",
    "PlanStep",
    "PlanStepType",
    "TaskUnit",
    "TaskResult",
    "TaskStatus",
    "FinalResponse",
    "MergeResult",
    "MemoryContext",
    "MemoryUpdate",
    "MemoryNamespace",
    "TaskState",
    "TaskStateMachine",
]
