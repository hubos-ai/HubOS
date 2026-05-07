# -*- coding: utf-8 -*-
"""Base worker provider interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class WorkerProviderError(Exception):
    """Base exception for worker provider errors."""

    pass


class WorkerTimeoutError(WorkerProviderError):
    """Raised when a worker operation times out."""

    pass


class WorkerExecutionError(WorkerProviderError):
    """Raised when worker execution fails."""

    pass


@dataclass(frozen=True)
class WorkerResult:
    """
    Standardized result from any worker provider.

    Workers return structured results with confidence and artifacts
    per ARCHITECTURE.md Worker Contract.
    """

    provider: str
    unit_id: UUID
    success: bool
    data: dict[str, Any]
    confidence: float
    artifacts: list[dict[str, Any]]
    error: str | None
    execution_time_ms: int
    timestamp: datetime


class WorkerProvider(ABC):
    """
    Unified interface for worker providers.

    Per IMPLEMENTATION_STANDARDS.md:
    - Unified interface for Claude Code/Codex worker.
    - Timeout, retry, and result normalization.

    Workers never message each other directly (ARCHITECTURE.md Cross-Agent Collaboration Rule #1).
    Each worker runs in isolated context (Rule #4).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the provider name."""

    @abstractmethod
    async def execute(
        self,
        unit_id: UUID,
        input_data: dict[str, Any],
        timeout_seconds: int,
    ) -> WorkerResult:
        """
        Execute a task unit.

        Args:
            unit_id: The task unit ID.
            input_data: Input data for the task.
            timeout_seconds: Timeout in seconds.

        Returns:
            WorkerResult with execution outcome.

        Raises:
            WorkerTimeoutError: If execution times out.
            WorkerExecutionError: If execution fails.
        """

    @abstractmethod
    def supports(self, task_type: str) -> bool:
        """
        Check if this provider supports a given task type.

        Args:
            task_type: The task type to check.

        Returns:
            True if supported, False otherwise.
        """
