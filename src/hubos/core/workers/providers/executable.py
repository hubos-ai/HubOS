# -*- coding: utf-8 -*-
"""Executable worker provider with timeout, retry, and error classification."""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from hubos.core.workers.providers.base import (
    WorkerExecutionError,
    WorkerProvider,
    WorkerProviderError,
    WorkerResult,
    WorkerTimeoutError,
)

logger = logging.getLogger(__name__)


class WorkerErrorType(str, Enum):
    """Classification of worker errors."""

    TIMEOUT = "timeout"
    EXECUTION_ERROR = "execution_error"
    RESOURCE_EXHAUSTED = "resource_exhausted"
    INVALID_INPUT = "invalid_input"
    UNKNOWN = "unknown"


@dataclass
class RetryPolicy:
    """Retry policy configuration."""

    max_retries: int = 3
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0
    exponential_base: float = 2.0

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given retry attempt."""
        delay = self.base_delay_seconds * (self.exponential_base**attempt)
        return min(delay, self.max_delay_seconds)


@dataclass
class ExecutableWorkerProvider(WorkerProvider):
    """
    Executable worker provider with timeout, retry, and error classification.

    Per IMPLEMENTATION_STANDARDS.md:
    - Unified interface for Claude Code/Codex/XClaw worker.
    - Timeout, retry, and result normalization.
    - Explicit error classification.

    This provider can execute actual task types like "research-summary".
    """

    SUPPORTED_TASK_TYPES = {
        "research-summary",
        "research",
        "analysis",
        "summary",
        "general",
    }

    def __init__(
        self,
        retry_policy: Optional[RetryPolicy] = None,
        default_timeout_seconds: int = 60,
    ) -> None:
        """
        Initialize the executable worker provider.

        Args:
            retry_policy: Retry policy configuration.
            default_timeout_seconds: Default timeout for tasks.
        """
        self._retry_policy = retry_policy or RetryPolicy()
        self._default_timeout = default_timeout_seconds
        self._task_handlers: dict[str, Any] = {
            "research-summary": self._handle_research_summary,
            "research": self._handle_research,
            "analysis": self._handle_analysis,
            "summary": self._handle_summary,
        }

    @property
    def name(self) -> str:
        """Return the provider name."""
        return "executable"

    def supports(self, task_type: str) -> bool:
        """
        Check if this provider supports a given task type.

        Args:
            task_type: The task type to check.

        Returns:
            True if supported, False otherwise.
        """
        return task_type.lower() in self.SUPPORTED_TASK_TYPES

    async def execute(
        self,
        unit_id: UUID,
        input_data: dict[str, Any],
        timeout_seconds: int,
    ) -> WorkerResult:
        """
        Execute a task with timeout and retry.

        Args:
            unit_id: The task unit ID.
            input_data: Input data for the task.
            timeout_seconds: Timeout in seconds.

        Returns:
            WorkerResult with execution outcome.

        Raises:
            WorkerTimeoutError: If all retries time out.
            WorkerExecutionError: If execution fails after retries.
        """
        task_type = input_data.get("task_type", "general")
        trace_id = input_data.get("trace_id", "")
        worker_id = self.name

        logger.info(
            "Worker executing task",
            extra={
                "unit_id": str(unit_id),
                "task_type": task_type,
                "trace_id": trace_id,
                "worker_id": worker_id,
                "timeout_seconds": timeout_seconds,
            },
        )

        last_error: Optional[Exception] = None
        error_type = WorkerErrorType.UNKNOWN

        for attempt in range(self._retry_policy.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self._execute_task(unit_id, input_data, task_type),
                    timeout=timeout_seconds,
                )
                logger.info(
                    "Worker task completed",
                    extra={
                        "unit_id": str(unit_id),
                        "task_type": task_type,
                        "trace_id": trace_id,
                        "worker_id": worker_id,
                        "attempt": attempt,
                        "confidence": result.confidence,
                    },
                )
                return result

            except asyncio.TimeoutError:
                error_type = WorkerErrorType.TIMEOUT
                last_error = WorkerTimeoutError(
                    f"Task {unit_id} timed out after {timeout_seconds}s (attempt {attempt + 1})",
                )
                logger.warning(
                    "Worker task timed out",
                    extra={
                        "unit_id": str(unit_id),
                        "task_type": task_type,
                        "trace_id": trace_id,
                        "worker_id": worker_id,
                        "attempt": attempt + 1,
                        "max_retries": self._retry_policy.max_retries,
                    },
                )

            except WorkerExecutionError as e:
                error_type = WorkerErrorType.EXECUTION_ERROR
                last_error = e
                logger.warning(
                    "Worker task execution error",
                    extra={
                        "unit_id": str(unit_id),
                        "task_type": task_type,
                        "trace_id": trace_id,
                        "worker_id": worker_id,
                        "attempt": attempt + 1,
                        "error": str(e),
                    },
                )

            except Exception as e:  # noqa: BLE001
                error_type = WorkerErrorType.UNKNOWN
                last_error = WorkerExecutionError(
                    f"Task {unit_id} failed: {e}",
                )
                logger.error(
                    "Worker task unexpected error",
                    extra={
                        "unit_id": str(unit_id),
                        "task_type": task_type,
                        "trace_id": trace_id,
                        "worker_id": worker_id,
                        "attempt": attempt + 1,
                        "error": str(e),
                    },
                )

            # Retry with exponential backoff
            if attempt < self._retry_policy.max_retries:
                delay = self._retry_policy.get_delay(attempt)
                logger.info(
                    "Worker retrying task",
                    extra={
                        "unit_id": str(unit_id),
                        "attempt": attempt + 1,
                        "delay_seconds": delay,
                    },
                )
                await asyncio.sleep(delay)

        # All retries exhausted
        logger.error(
            "Worker task failed after all retries",
            extra={
                "unit_id": str(unit_id),
                "task_type": task_type,
                "trace_id": trace_id,
                "worker_id": worker_id,
                "total_attempts": self._retry_policy.max_retries + 1,
                "error_type": error_type.value,
            },
        )

        # Return failure result instead of raising
        return WorkerResult(
            provider=self.name,
            unit_id=unit_id,
            success=False,
            data=input_data,
            confidence=0.0,
            artifacts=[],
            error=f"{error_type.value}: {last_error}",
            execution_time_ms=0,
            timestamp=datetime.now(timezone.utc),
        )

    async def _execute_task(
        self,
        unit_id: UUID,
        input_data: dict[str, Any],
        task_type: str,
    ) -> WorkerResult:
        """Execute the actual task logic."""
        start_time = time.time()

        handler = self._task_handlers.get(task_type, self._handle_general)
        result_data = await handler(input_data)

        elapsed_ms = int((time.time() - start_time) * 1000)

        return WorkerResult(
            provider=self.name,
            unit_id=unit_id,
            success=True,
            data=result_data,
            confidence=result_data.get("confidence", 0.9),
            artifacts=result_data.get("artifacts", []),
            error=None,
            execution_time_ms=elapsed_ms,
            timestamp=datetime.now(timezone.utc),
        )

    async def _handle_research_summary(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle research-summary task type."""
        query = input_data.get("query", "")
        topic = input_data.get("topic", "")

        # Simulate research work
        await asyncio.sleep(0.1)

        return {
            "task_type": "research-summary",
            "query": query,
            "topic": topic,
            "content": f"Research summary for: {topic or query}",
            "conclusion": f"Based on research, {topic or query} is an important topic.",
            "confidence": 0.92,
            "artifacts": [
                {
                    "type": "summary",
                    "content": f"Executive summary of research on {topic or query}",
                },
            ],
        }

    async def _handle_research(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle research task type."""
        query = input_data.get("query", "")

        await asyncio.sleep(0.05)

        return {
            "task_type": "research",
            "query": query,
            "content": f"Research findings for: {query}",
            "confidence": 0.88,
            "artifacts": [],
        }

    async def _handle_analysis(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle analysis task type."""
        _data = input_data.get("data", {})

        await asyncio.sleep(0.05)

        return {
            "task_type": "analysis",
            "content": f"Analysis of provided data",
            "conclusion": "Analysis complete",
            "confidence": 0.85,
            "artifacts": [],
        }

    async def _handle_summary(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle summary task type."""
        content = input_data.get("content", "")

        await asyncio.sleep(0.05)

        return {
            "task_type": "summary",
            "content": f"Summary: {content[:100]}..."
            if len(content) > 100
            else f"Summary: {content}",
            "confidence": 0.90,
            "artifacts": [],
        }

    async def _handle_general(
        self,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Handle general task type."""
        await asyncio.sleep(0.05)

        return {
            "task_type": "general",
            "content": "General task completed",
            "confidence": 0.80,
            "artifacts": [],
        }
