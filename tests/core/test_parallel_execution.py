"""Tests for parallel execution engine."""

import asyncio
from uuid import uuid4

import pytest

from hubos.core.orchestrator.parallel_executor import (
    ConflictLevel,
    ConflictRecord,
    ParallelExecutor,
    UnitExecutionRecord,
    UnitStatus,
)


class TestParallelExecutor:
    """Tests for ParallelExecutor."""

    @pytest.mark.asyncio
    async def test_parallel_execution_happy_path(self) -> None:
        """Test parallel execution of independent units."""
        executed_units: list[str] = []

        async def mock_executor(unit_id, input_data):
            await asyncio.sleep(0.01)
            executed_units.append(str(unit_id))
            return {"status": "success"}

        executor = ParallelExecutor(mock_executor, max_parallel=3)

        # Create 3 independent units
        unit_ids = [uuid4(), uuid4(), uuid4()]
        unit_defs = [
            {"step_id": str(unit_ids[0]), "name": "unit1", "required": True},
            {"step_id": str(unit_ids[1]), "name": "unit2", "required": True},
            {"step_id": str(unit_ids[2]), "name": "unit3", "required": True},
        ]

        await executor.execute_all(unit_defs, trace_id="trace-123")

        # All units should complete
        results = executor.get_results()
        assert len(results) == 3
        assert all(r.status == UnitStatus.SUCCESS for r in results.values())

    @pytest.mark.asyncio
    async def test_dependency_ordering(self) -> None:
        """Test that units respect dependency ordering."""
        execution_order: list[str] = []

        async def mock_executor(unit_id, input_data):
            await asyncio.sleep(0.01)
            execution_order.append(str(unit_id))
            return {"status": "success"}

        executor = ParallelExecutor(mock_executor, max_parallel=10)

        unit_a = uuid4()
        unit_b = uuid4()
        unit_c = uuid4()

        # C depends on B, B depends on A
        unit_defs = [
            {"step_id": str(unit_a), "name": "A", "required": True, "depends_on": []},
            {"step_id": str(unit_b), "name": "B", "required": True, "depends_on": [str(unit_a)]},
            {"step_id": str(unit_c), "name": "C", "required": True, "depends_on": [str(unit_b)]},
        ]

        await executor.execute_all(unit_defs, trace_id="trace-123")

        # Verify execution order respects dependencies
        idx_a = execution_order.index(str(unit_a))
        idx_b = execution_order.index(str(unit_b))
        idx_c = execution_order.index(str(unit_c))

        assert idx_a < idx_b < idx_c

    @pytest.mark.asyncio
    async def test_parallel_with_dependencies(self) -> None:
        """Test parallel execution where independent units run together."""
        start_times: dict[str, float] = {}

        async def mock_executor(unit_id, input_data):
            import time

            start_times[str(unit_id)] = time.time()
            await asyncio.sleep(0.05)  # Simulate work
            return {"status": "success"}

        executor = ParallelExecutor(mock_executor, max_parallel=10)

        unit_a = uuid4()
        unit_b = uuid4()
        unit_c = uuid4()

        # A and B are independent, C depends on both
        unit_defs = [
            {"step_id": str(unit_a), "name": "A", "required": True, "depends_on": []},
            {"step_id": str(unit_b), "name": "B", "required": True, "depends_on": []},
            {"step_id": str(unit_c), "name": "C", "required": True, "depends_on": [str(unit_a), str(unit_b)]},
        ]

        await executor.execute_all(unit_defs, trace_id="trace-123")

        # A and B should have similar start times (parallel)
        # C should start after both A and B complete
        import time

        time_diff = abs(start_times[str(unit_a)] - start_times[str(unit_b)])
        assert time_diff < 0.02  # Started within 20ms = parallel

    @pytest.mark.asyncio
    async def test_non_required_failure_isolation(self) -> None:
        """Test that non-required unit failures don't block execution."""
        async def mock_executor(unit_id, input_data):
            if "fail" in input_data.get("name", ""):
                raise RuntimeError("Simulated failure")
            await asyncio.sleep(0.01)
            return {"status": "success"}

        executor = ParallelExecutor(mock_executor, max_parallel=10)

        unit_a = uuid4()
        unit_b = uuid4()  # Will fail
        unit_c = uuid4()

        unit_defs = [
            {"step_id": str(unit_a), "input_data": {"name": "success"}, "required": True, "depends_on": []},
            {"step_id": str(unit_b), "input_data": {"name": "fail"}, "required": False, "depends_on": []},
            {"step_id": str(unit_c), "input_data": {"name": "also_success"}, "required": True, "depends_on": []},
        ]

        await executor.execute_all(unit_defs, trace_id="trace-123")

        results = executor.get_results()

        # Non-required failed unit should not block others
        assert results[unit_a].status == UnitStatus.SUCCESS
        assert results[unit_b].status == UnitStatus.FAILURE
        assert results[unit_c].status == UnitStatus.SUCCESS

        # No blocking failures since unit_b was not required
        assert not executor.has_blocking_failures()

    @pytest.mark.asyncio
    async def test_required_failure_blocks(self) -> None:
        """Test that required unit failures are tracked."""
        async def mock_executor(unit_id, input_data):
            if "fail" in input_data.get("name", ""):
                raise RuntimeError("Simulated failure")
            await asyncio.sleep(0.01)
            return {"status": "success"}

        executor = ParallelExecutor(mock_executor, max_parallel=10)

        unit_a = uuid4()
        unit_b = uuid4()

        unit_defs = [
            {"step_id": str(unit_a), "input_data": {"name": "success"}, "required": True, "depends_on": []},
            {"step_id": str(unit_b), "input_data": {"name": "fail"}, "required": True, "depends_on": []},
        ]

        await executor.execute_all(unit_defs, trace_id="trace-123")

        results = executor.get_results()

        # Required failure should be tracked
        assert executor.has_blocking_failures()
        assert len(executor.get_failed_required_units()) == 1

    @pytest.mark.asyncio
    async def test_max_parallel_limit(self) -> None:
        """Test that max_parallel limit is respected."""
        running_count = 0
        max_observed = 0

        async def mock_executor(unit_id, input_data):
            nonlocal running_count, max_observed

            running_count += 1
            max_observed = max(max_observed, running_count)
            await asyncio.sleep(0.05)
            running_count -= 1

            return {"status": "success"}

        executor = ParallelExecutor(mock_executor, max_parallel=2)

        unit_ids = [uuid4() for _ in range(5)]
        unit_defs = [
            {"step_id": str(uid), "name": f"unit{i}", "required": True}
            for i, uid in enumerate(unit_ids)
        ]

        await executor.execute_all(unit_defs, trace_id="trace-123")

        # Should never have more than max_parallel running
        assert max_observed <= 2


class TestUnitExecutionRecord:
    """Tests for UnitExecutionRecord."""

    def test_record_initialization(self) -> None:
        """Test record starts with correct defaults."""
        unit_id = uuid4()
        record = UnitExecutionRecord(
            unit_id=unit_id,
            step_name="test-step",
        )

        assert record.unit_id == unit_id
        assert record.status == UnitStatus.PENDING
        assert record.retry_count == 0
        assert record.error is None
        assert record.result is None


class TestConflictRecord:
    """Tests for ConflictRecord."""

    def test_conflict_record_creation(self) -> None:
        """Test creating a conflict record."""
        unit_id = uuid4()
        conflict = ConflictRecord(
            unit_id=unit_id,
            conflict_type="conclusion",
            details="Unit A says X, Unit B says Y",
            level=ConflictLevel.HIGH,
        )

        assert conflict.unit_id == unit_id
        assert conflict.level == ConflictLevel.HIGH
