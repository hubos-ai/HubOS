# -*- coding: utf-8 -*-
"""Stub worker provider for testing and development."""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from hubos.core.workers.providers.base import (
    WorkerExecutionError,
    WorkerProvider,
    WorkerResult,
    WorkerTimeoutError,
)

logger = logging.getLogger(__name__)


class StubWorkerProvider(WorkerProvider):
    """
    Stub worker provider for testing and development.

    This is a placeholder implementation that simulates worker behavior.
    Per ARCHITECTURE.md: Workers cannot write memory directly (Rule #2).
    """

    SUPPORTED_TASKS = {"research", "analysis", "summary", "general"}

    def __init__(self, simulate_delay_ms: int = 100) -> None:
        """
        Initialize the stub worker.

        Args:
            simulate_delay_ms: Artificial delay to simulate work.
        """
        self._simulate_delay_ms = simulate_delay_ms

    @property
    def name(self) -> str:
        """Return the provider name."""
        return "stub"

    async def execute(
        self,
        unit_id: UUID,
        input_data: dict[str, Any],
        timeout_seconds: int,
    ) -> WorkerResult:
        """
        Execute a stub task.

        Args:
            unit_id: The task unit ID.
            input_data: Input data for the task.
            timeout_seconds: Timeout in seconds.

        Returns:
            WorkerResult with stub execution outcome.

        Raises:
            WorkerTimeoutError: If execution times out.
            WorkerExecutionError: If execution fails.
        """
        start_time = time.time()
        logger.info(
            "Stub worker executing",
            extra={
                "unit_id": str(unit_id),
                "timeout_seconds": timeout_seconds,
            },
        )

        try:
            # Simulate work
            await asyncio.sleep(self._simulate_delay_ms / 1000)

            elapsed_ms = int((time.time() - start_time) * 1000)

            return WorkerResult(
                provider=self.name,
                unit_id=unit_id,
                success=True,
                data=input_data,
                confidence=0.95,
                artifacts=[],
                error=None,
                execution_time_ms=elapsed_ms,
                timestamp=datetime.now(timezone.utc),
            )

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start_time) * 1000)
            raise WorkerTimeoutError(
                f"Task {unit_id} timed out after {elapsed_ms}ms",
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            raise WorkerExecutionError(f"Task {unit_id} failed: {e}") from e

    def supports(self, task_type: str) -> bool:
        """
        Check if this provider supports a given task type.

        Args:
            task_type: The task type to check.

        Returns:
            True if supported, False otherwise.
        """
        return task_type.lower() in self.SUPPORTED_TASKS
