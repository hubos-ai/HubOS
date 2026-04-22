"""Parallel task execution engine with dependency management."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


class UnitStatus(str, Enum):
    """Status of an execution unit."""

    PENDING = "pending"
    WAITING = "waiting"  # Waiting for dependencies
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"


class ConflictLevel(str, Enum):
    """Conflict severity levels for merge decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class UnitExecutionRecord:
    """Record of a unit's execution lifecycle."""

    unit_id: UUID
    step_name: str
    status: UnitStatus = UnitStatus.PENDING
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    retry_count: int = 0
    error: Optional[str] = None
    result: Any = None


@dataclass
class ConflictRecord:
    """Record of a conflict detected during merge."""

    unit_id: UUID
    conflict_type: str  # conclusion, confidence, critical_field
    details: str
    level: ConflictLevel = ConflictLevel.LOW


class ParallelExecutor:
    """
    Parallel task execution engine with dependency management.

    Supports:
    - Parallel execution of independent TaskUnits
    - Dependency constraints via depends_on
    - Independent timeout/retry per unit
    - Failure isolation (non-required units can fail without blocking all)
    """

    def __init__(
        self,
        worker_executor: Callable,  # Async function to execute units
        max_parallel: int = 10,
    ) -> None:
        """
        Initialize parallel executor.

        Args:
            worker_executor: Async function that executes a single unit.
            max_parallel: Maximum concurrent unit executions.
        """
        self._worker_executor = worker_executor
        self._max_parallel = max_parallel
        self._execution_records: dict[UUID, UnitExecutionRecord] = {}
        self._running_units: set[UUID] = set()
        self._completed_units: set[UUID] = set()
        self._failed_units: set[UUID] = set()
        self._required_units: set[UUID] = set()

    def initialize_units(
        self,
        units: list[dict[str, Any]],
    ) -> list[UUID]:
        """
        Initialize execution units from step definitions.

        Args:
            units: List of unit definitions with step_id, depends_on, required, etc.

        Returns:
            List of unit IDs in execution order (respecting dependencies).
        """
        self._execution_records.clear()
        self._running_units.clear()
        self._completed_units.clear()
        self._failed_units.clear()
        self._required_units.clear()

        unit_ids: list[UUID] = []
        for unit_def in units:
            unit_id = UUID(unit_def["step_id"])
            is_required = unit_def.get("required", True)

            record = UnitExecutionRecord(
                unit_id=unit_id,
                step_name=unit_def.get("name", str(unit_id)),
                status=UnitStatus.PENDING,
            )
            self._execution_records[unit_id] = record

            if is_required:
                self._required_units.add(unit_id)

            unit_ids.append(unit_id)

        # Return all unit IDs for tracking
        return unit_ids

    def get_ready_units(
        self,
        unit_defs: list[dict[str, Any]],
    ) -> list[UUID]:
        """
        Get units that are ready to execute (dependencies met).

        Args:
            unit_defs: List of unit definitions with depends_on.

        Returns:
            List of unit IDs ready for execution.
        """
        ready: list[UUID] = []
        dep_map = {UUID(u["step_id"]): set(UUID(d) for d in u.get("depends_on", [])) for u in unit_defs}

        for unit_id, record in self._execution_records.items():
            if record.status != UnitStatus.PENDING:
                continue

            deps = dep_map.get(unit_id, set())
            # Check all dependencies are completed
            if deps.issubset(self._completed_units):
                ready.append(unit_id)

        return ready

    async def execute_all(
        self,
        unit_defs: list[dict[str, Any]],
        timeout_seconds: int = 300,
        trace_id: str = "",
        session_id: str = "",
    ) -> dict[UUID, Any]:
        """
        Execute all units respecting dependencies.

        Args:
            unit_defs: List of unit definitions.
            timeout_seconds: Default timeout per unit.
            trace_id: Trace ID for logging.
            session_id: Session ID for logging.

        Returns:
            Dict mapping unit_id to execution result.
        """
        unit_ids = self.initialize_units(unit_defs)
        results: dict[UUID, Any] = {}

        # Create dependency map
        dep_map = {UUID(u["step_id"]): [UUID(d) for d in u.get("depends_on", [])] for u in unit_defs}

        # Track which units are waiting
        waiting: dict[UUID, list[UUID]] = {}  # unit_id -> units waiting for it
        for unit_id, deps in dep_map.items():
            for dep in deps:
                if dep not in waiting:
                    waiting[dep] = []
                waiting[dep].append(unit_id)

        while len(self._completed_units) + len(self._failed_units) < len(unit_ids):
            # Get ready units
            ready = self.get_ready_units(unit_defs)

            # Filter out units already running
            ready = [u for u in ready if u not in self._running_units]

            if not ready:
                # Check if we're blocked (deadlock or all running)
                if len(self._running_units) == 0:
                    # No units running and none ready - we're stuck
                    logger.warning(
                        "No units ready and none running - possible deadlock",
                        extra={
                            "trace_id": trace_id,
                            "pending": [str(u) for u in self._execution_records if self._execution_records[u].status == UnitStatus.PENDING],
                            "running": [str(u) for u in self._running_units],
                        },
                    )
                    break

                # Wait for running units to complete
                await asyncio.sleep(0.1)
                continue

            # Execute ready units (up to max_parallel)
            for unit_id in ready[: self._max_parallel - len(self._running_units)]:
                record = self._execution_records[unit_id]
                record.status = UnitStatus.RUNNING
                record.started_at = datetime.now(timezone.utc)
                self._running_units.add(unit_id)

                # Find unit def
                unit_def = next((u for u in unit_defs if UUID(u["step_id"]) == unit_id), None)
                if unit_def:
                    asyncio.create_task(
                        self._execute_unit(
                            unit_id,
                            unit_def,
                            timeout_seconds,
                            trace_id,
                            session_id,
                        )
                    )

            # Small yield
            await asyncio.sleep(0.01)

        return results

    async def _execute_unit(
        self,
        unit_id: UUID,
        unit_def: dict[str, Any],
        timeout_seconds: int,
        trace_id: str,
        session_id: str,
    ) -> None:
        """Execute a single unit with timeout/retry."""
        record = self._execution_records[unit_id]
        retries = unit_def.get("retry_count", 0)
        max_retries = unit_def.get("max_retries", 3)
        unit_timeout = unit_def.get("timeout_seconds", timeout_seconds)

        for attempt in range(retries + 1):
            try:
                logger.info(
                    "Executing unit",
                    extra={
                        "trace_id": trace_id,
                        "session_id": session_id,
                        "unit_id": str(unit_id),
                        "step_name": record.step_name,
                        "attempt": attempt + 1,
                    },
                )

                result = await asyncio.wait_for(
                    self._worker_executor(unit_id, unit_def.get("input_data", {})),
                    timeout=unit_timeout,
                )

                record.status = UnitStatus.SUCCESS
                record.result = result
                record.completed_at = datetime.now(timezone.utc)

                logger.info(
                    "Unit completed successfully",
                    extra={
                        "trace_id": trace_id,
                        "unit_id": str(unit_id),
                        "step_name": record.step_name,
                        "attempt": attempt + 1,
                    },
                )
                break

            except asyncio.TimeoutError:
                record.retry_count = attempt + 1
                logger.warning(
                    "Unit timed out",
                    extra={
                        "trace_id": trace_id,
                        "unit_id": str(unit_id),
                        "step_name": record.step_name,
                        "attempt": attempt + 1,
                        "timeout_seconds": unit_timeout,
                    },
                )
                if attempt >= retries:
                    record.status = UnitStatus.FAILURE
                    record.error = f"Timeout after {attempt + 1} attempts"
                    record.completed_at = datetime.now(timezone.utc)
                    break

            except Exception as e:
                record.retry_count = attempt + 1
                logger.error(
                    "Unit execution failed",
                    extra={
                        "trace_id": trace_id,
                        "unit_id": str(unit_id),
                        "step_name": record.step_name,
                        "attempt": attempt + 1,
                        "error": str(e),
                    },
                )
                if attempt >= retries:
                    record.status = UnitStatus.FAILURE
                    record.error = str(e)
                    record.completed_at = datetime.now(timezone.utc)
                    break

        self._running_units.discard(unit_id)

        # Track completed vs failed separately
        if record.status == UnitStatus.FAILURE:
            self._failed_units.add(unit_id)
        self._completed_units.add(unit_id)

    def get_results(self) -> dict[UUID, UnitExecutionRecord]:
        """Get all execution records."""
        return self._execution_records.copy()

    def get_failed_required_units(self) -> list[UUID]:
        """Get failed units that were required."""
        return list(self._required_units & self._failed_units)

    def has_blocking_failures(self) -> bool:
        """Check if there are any required unit failures."""
        return len(self.get_failed_required_units()) > 0
