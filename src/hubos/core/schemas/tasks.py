# -*- coding: utf-8 -*-
"""Task unit and result schemas."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


SCHEMA_VERSION = "1.0.0"


class TaskStatus(str, Enum):
    """Status of a task unit execution."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    RETRYING = "retrying"


@dataclass(frozen=True)
class TaskUnit:
    """
    A unit of work to be executed by a worker.

    Part of the Worker Contract as defined in ARCHITECTURE.md.
    """

    schema_version: str = field(default=SCHEMA_VERSION)
    unit_id: UUID = field(default_factory=uuid4)
    plan_step_id: UUID = field(default_factory=uuid4)
    task_id: str = ""
    worker_provider: str = ""
    input_data: dict = field(default_factory=dict)
    timeout_seconds: int = 300
    retry_count: int = 0
    max_retries: int = 3
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.worker_provider:
            raise ValueError("worker_provider is required")


@dataclass(frozen=True)
class TaskResult:
    """
    Result returned by a worker after executing a TaskUnit.

    Part of the Worker Contract as defined in ARCHITECTURE.md.
    Returns structured TaskResult with confidence and artifacts.
    """

    schema_version: str = field(default=SCHEMA_VERSION)
    unit_id: UUID = field(default_factory=uuid4)
    task_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    confidence: float = 0.0
    output_data: dict = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None
    retry_count: int = 0
    executed_at: datetime = field(default_factory=_utcnow)
    trace_id: str = ""

    def __post_init__(self) -> None:
        """Validate confidence score."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
