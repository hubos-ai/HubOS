# -*- coding: utf-8 -*-
"""Tests for TaskPlanExecutor — execution control and agent dispatch."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from hubos.app.task_plan import PlanStatus, PlanStepStatus, get_plan_store
from hubos.app.task_plan_executor import TaskPlanExecutor, get_plan_executor
from hubos.core.workers.providers.base import WorkerResult


def _make_worker_result(
    content: str = "ok",
    success: bool = True,
    error: str | None = None,
    execution_time_ms: int = 42,
) -> WorkerResult:
    return WorkerResult(
        provider="host_agent",
        unit_id=uuid4(),
        success=success,
        data={"content": content, "agent_id": "agent-1"},
        confidence=0.9,
        artifacts=[],
        error=error,
        execution_time_ms=execution_time_ms,
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_stores():
    import hubos.app.task_plan as _plan_mod
    import hubos.app.task_plan_executor as _exec_mod
    import hubos.app.task_monitor_helpers as _mon_mod

    old_plan = _plan_mod._store
    old_exec = _exec_mod._executor
    old_mon = _mon_mod._store
    old_handlers = _mon_mod._cancel_handlers.copy()
    _plan_mod._store = None
    _exec_mod._executor = None
    _mon_mod._store = None
    _mon_mod._cancel_handlers.clear()
    yield
    _plan_mod._store = old_plan
    _exec_mod._executor = old_exec
    _mon_mod._store = old_mon
    _mon_mod._cancel_handlers.clear()
    _mon_mod._cancel_handlers.update(old_handlers)


@pytest.fixture
def store():
    return get_plan_store()


@pytest.fixture
def executor():
    return get_plan_executor()


# ---------------------------------------------------------------------------
# start_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_missing_raises(executor: TaskPlanExecutor):
    with pytest.raises(KeyError, match="Plan not found"):
        await executor.start_plan("nonexistent")


@pytest.mark.asyncio
async def test_start_draft_plan(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Test",
        steps=[
            {"title": "A"},
            {"title": "B"},
        ],
    )

    started = await executor.start_plan(plan.plan_id)
    assert started is True

    # Wait for execution to complete
    for _ in range(50):
        await asyncio.sleep(0.02)
        refreshed = await store.get_plan(plan.plan_id)
        if refreshed.status == PlanStatus.DONE:
            break

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE
    assert refreshed.current_step_id is None
    assert all(s.status == PlanStepStatus.DONE for s in refreshed.steps)


@pytest.mark.asyncio
async def test_start_running_plan_returns_false(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(session_id="s1", title="P")
    await store.update_plan(plan.plan_id, status=PlanStatus.RUNNING)

    started = await executor.start_plan(plan.plan_id)
    assert started is False


@pytest.mark.asyncio
async def test_start_done_plan_returns_false(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(session_id="s1", title="P")
    await store.update_plan(plan.plan_id, status=PlanStatus.DONE)

    started = await executor.start_plan(plan.plan_id)
    assert started is False


@pytest.mark.asyncio
async def test_start_cancelled_plan_returns_false(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(session_id="s1", title="P")
    await store.cancel_plan(plan.plan_id)

    started = await executor.start_plan(plan.plan_id)
    assert started is False


# ---------------------------------------------------------------------------
# Step status updates during execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execution_updates_step_statuses(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(
        session_id="s1",
        title="Steps test",
        steps=[
            {"title": "Step 1"},
            {"title": "Step 2"},
        ],
    )

    await executor.start_plan(plan.plan_id)

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.02)
        refreshed = await store.get_plan(plan.plan_id)
        if refreshed.status == PlanStatus.DONE:
            break

    refreshed = await store.get_plan(plan.plan_id)
    # All steps should be done
    for step in refreshed.steps:
        assert step.status == PlanStepStatus.DONE
        assert step.finished_at is not None


# ---------------------------------------------------------------------------
# cancel_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_running_plan(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Cancel test",
        steps=[
            {"title": "A"},
            {"title": "B"},
            {"title": "C"},
        ],
    )

    await executor.start_plan(plan.plan_id)
    # Give it a moment to start
    await asyncio.sleep(0.02)

    cancelled = await executor.cancel_plan(plan.plan_id)
    assert cancelled is True

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.CANCELLED

    # Not all steps should be done — at least some cancelled
    statuses = [s.status for s in refreshed.steps]
    assert PlanStepStatus.CANCELLED in statuses


@pytest.mark.asyncio
async def test_cancel_non_running_plan(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(session_id="s1", title="Draft")
    cancelled = await executor.cancel_plan(plan.plan_id)
    assert cancelled is True

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.CANCELLED


# ---------------------------------------------------------------------------
# Executor cleanup
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_cleanup_after_done(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Cleanup",
        steps=[{"title": "A"}],
    )

    await executor.start_plan(plan.plan_id)

    # Wait for completion
    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    assert not executor.is_running(plan.plan_id)
    assert plan.plan_id not in executor._running
    assert plan.plan_id not in executor._cancel_events


# ---------------------------------------------------------------------------
# Executor failure handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_failure_marks_plan_failed(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(
        session_id="s1",
        title="Fail test",
        steps=[{"title": "A"}],
    )

    # Monkeypatch store.update_step to raise on first call
    original = store.update_step
    call_count = 0

    async def _failing_update_step(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 1:
            raise RuntimeError("test failure")
        return await original(*args, **kwargs)

    store.update_step = _failing_update_step

    await executor.start_plan(plan.plan_id)

    # Wait for the executor to finish (it should catch the error)
    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.FAILED
    assert refreshed.metadata is not None
    assert refreshed.metadata.get("executor_error") == "internal error"

    store.update_step = original


# ---------------------------------------------------------------------------
# Agent dispatch — real HostAgentRunner integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_without_agent_still_uses_mock(
    store,
    executor: TaskPlanExecutor,
):
    """Steps without agent_id or tool_name use simulated execution."""
    plan = await store.create_plan(
        session_id="s1",
        title="Mock step",
        steps=[{"title": "A"}, {"title": "B"}],
    )

    await executor.start_plan(plan.plan_id)

    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE
    assert all(s.status == PlanStepStatus.DONE for s in refreshed.steps)


@pytest.mark.asyncio
@patch("hubos.core.workers.registry.get_host_agent_runner")
async def test_step_with_agent_calls_runner(
    mock_get_runner,
    store,
    executor: TaskPlanExecutor,
):
    """Step with agent_id dispatches to HostAgentRunner."""
    fake_runner = AsyncMock(return_value="done by agent-1")
    mock_get_runner.return_value = fake_runner

    plan = await store.create_plan(
        session_id="s1",
        title="Agent step",
        steps=[
            {
                "title": "Do work",
                "description": "Please analyze the data",
                "agent_id": "agent-1",
            },
        ],
    )

    await executor.start_plan(plan.plan_id)

    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    # Runner was called
    fake_runner.assert_awaited_once()
    call_args = fake_runner.call_args
    assert call_args[0][0] == "agent-1"  # agent_id
    assert "analyze the data" in call_args[0][1]  # prompt

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE
    step = refreshed.steps[0]
    assert step.status == PlanStepStatus.DONE
    assert step.metadata is not None
    assert step.metadata["result_summary"] == "done by agent-1"
    assert step.metadata["executed_by"] == "agent-1"
    assert "execution_time_ms" in step.metadata


@pytest.mark.asyncio
@patch("hubos.core.workers.registry.get_host_agent_runner")
async def test_missing_runner_marks_step_failed(
    mock_get_runner,
    store,
    executor: TaskPlanExecutor,
):
    """No registered HostAgentRunner → step and plan marked failed."""
    mock_get_runner.return_value = None

    plan = await store.create_plan(
        session_id="s1",
        title="No runner",
        steps=[{"title": "A", "agent_id": "agent-1"}],
    )

    await executor.start_plan(plan.plan_id)

    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.FAILED
    assert refreshed.steps[0].status == PlanStepStatus.FAILED
    assert refreshed.steps[0].error == "HostAgentRunner is not registered"
    assert refreshed.metadata is not None
    assert refreshed.metadata["failed_step_id"] == refreshed.steps[0].step_id


@pytest.mark.asyncio
@patch("hubos.core.workers.registry.get_host_agent_runner")
async def test_runner_raises_marks_step_failed(
    mock_get_runner,
    store,
    executor: TaskPlanExecutor,
):
    """Agent runner raises → step failed, plan failed, stops execution."""
    fake_runner = AsyncMock(side_effect=RuntimeError("agent crashed"))
    mock_get_runner.return_value = fake_runner

    plan = await store.create_plan(
        session_id="s1",
        title="Runner crash",
        steps=[
            {"title": "A", "agent_id": "agent-1"},
            {"title": "B"},  # should not execute
        ],
    )

    await executor.start_plan(plan.plan_id)

    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.FAILED
    assert refreshed.steps[0].status == PlanStepStatus.FAILED
    assert "agent crashed" in refreshed.steps[0].error
    # Second step should remain pending (execution stopped)
    assert refreshed.steps[1].status == PlanStepStatus.PENDING


@pytest.mark.asyncio
@patch("hubos.core.workers.registry.get_host_agent_runner")
async def test_runner_returns_unsuccessful_marks_failed(
    mock_get_runner,
    store,
    executor: TaskPlanExecutor,
):
    """Worker returns success=False → step and plan failed."""
    fake_runner = AsyncMock(return_value="ignored")
    mock_get_runner.return_value = fake_runner

    # Patch HostAgentWorker.execute to return unsuccessful result
    from hubos.core.workers.providers.host_agent import HostAgentWorker

    original_execute = HostAgentWorker.execute

    async def _fake_execute(self, unit_id, input_data, timeout_seconds):
        return _make_worker_result(success=False, error="agent refused")

    HostAgentWorker.execute = _fake_execute
    try:
        plan = await store.create_plan(
            session_id="s1",
            title="Unsuccessful",
            steps=[{"title": "A", "agent_id": "agent-1"}],
        )

        await executor.start_plan(plan.plan_id)

        for _ in range(50):
            await asyncio.sleep(0.02)
            if not executor.is_running(plan.plan_id):
                break

        refreshed = await store.get_plan(plan.plan_id)
        assert refreshed.status == PlanStatus.FAILED
        assert refreshed.steps[0].status == PlanStepStatus.FAILED
        assert refreshed.steps[0].error == "agent refused"
    finally:
        HostAgentWorker.execute = original_execute


@pytest.mark.asyncio
async def test_tool_name_without_agent_waits_user(
    store,
    executor: TaskPlanExecutor,
):
    """Step with tool_name but no agent_id → waiting_user, plan pauses."""
    plan = await store.create_plan(
        session_id="s1",
        title="Tool step",
        steps=[
            {"title": "Run tool", "tool_name": "some_tool"},
            {"title": "After tool"},  # should not execute
        ],
    )

    await executor.start_plan(plan.plan_id)

    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.WAITING_USER
    assert refreshed.steps[0].status == PlanStepStatus.WAITING_USER
    assert refreshed.steps[0].metadata is not None
    assert (
        refreshed.steps[0].metadata["reason"]
        == "tool execution not implemented"
    )
    # Second step stays pending
    assert refreshed.steps[1].status == PlanStepStatus.PENDING


@pytest.mark.asyncio
@patch("hubos.core.workers.registry.get_host_agent_runner")
async def test_cancel_during_agent_execution(
    mock_get_runner,
    store,
    executor: TaskPlanExecutor,
):
    """Cancellation during agent execution → plan cancelled."""
    # Runner that takes a long time so we can cancel mid-execution
    fake_runner = AsyncMock()

    async def _slow_runner(*a, **kw):
        await asyncio.sleep(10)

    fake_runner.side_effect = _slow_runner

    mock_get_runner.return_value = fake_runner

    plan = await store.create_plan(
        session_id="s1",
        title="Cancel during agent",
        steps=[
            {"title": "A", "agent_id": "agent-1"},
            {"title": "B"},
        ],
    )

    await executor.start_plan(plan.plan_id)
    # Let the agent step start executing
    await asyncio.sleep(0.1)

    cancelled = await executor.cancel_plan(plan.plan_id)
    assert cancelled is True

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.CANCELLED


@pytest.mark.asyncio
@patch("hubos.core.workers.registry.get_host_agent_runner")
async def test_mixed_steps_agent_then_mock(
    mock_get_runner,
    store,
    executor: TaskPlanExecutor,
):
    """Agent step followed by mock step — both complete."""
    fake_runner = AsyncMock(return_value="agent result")
    mock_get_runner.return_value = fake_runner

    plan = await store.create_plan(
        session_id="s1",
        title="Mixed",
        steps=[
            {"title": "Agent step", "agent_id": "agent-1"},
            {"title": "Mock step"},
        ],
    )

    await executor.start_plan(plan.plan_id)

    for _ in range(50):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan.plan_id):
            break

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE
    assert refreshed.steps[0].status == PlanStepStatus.DONE
    assert refreshed.steps[0].metadata["executed_by"] == "agent-1"
    assert refreshed.steps[1].status == PlanStepStatus.DONE


# ---------------------------------------------------------------------------
# Pause / Resume / Dynamic step insertion
# ---------------------------------------------------------------------------


async def _wait_done(executor, plan_id, store, iterations=100):
    """Helper: wait until executor finishes or plan is terminal."""
    for _ in range(iterations):
        await asyncio.sleep(0.02)
        if not executor.is_running(plan_id):
            break
        plan = await store.get_plan(plan_id)
        if plan and plan.status in (
            PlanStatus.WAITING_USER,
            PlanStatus.FAILED,
            PlanStatus.CANCELLED,
            PlanStatus.DONE,
        ):
            break


@pytest.mark.asyncio
async def test_pause_running_plan(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Pause test",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    await executor.start_plan(plan.plan_id)
    await asyncio.sleep(0.1)

    ok = await executor.pause_plan(plan.plan_id)
    assert ok is True

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.WAITING_USER

    # Task should still be tracked (not cleaned up)
    assert plan.plan_id in executor._running


@pytest.mark.asyncio
async def test_resume_paused_plan(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Resume test",
        steps=[{"title": "A"}, {"title": "B"}],
    )
    await executor.start_plan(plan.plan_id)
    await asyncio.sleep(0.1)
    await executor.pause_plan(plan.plan_id)

    ok = await executor.resume_plan(plan.plan_id)
    assert ok is True

    # Wait for completion
    await _wait_done(executor, plan.plan_id, store)

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE


@pytest.mark.asyncio
async def test_insert_step_while_paused_then_resume(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(
        session_id="s1",
        title="Insert paused",
        steps=[{"title": "A"}, {"title": "C"}],
    )
    await executor.start_plan(plan.plan_id)
    await asyncio.sleep(0.1)
    await executor.pause_plan(plan.plan_id)

    # Insert a new step while paused
    await store.add_step(
        plan.plan_id,
        title="B inserted",
        after_step_id=plan.steps[0].step_id
        if (await store.get_plan(plan.plan_id)).steps[0].status == "done"
        else None,
    )

    ok = await executor.resume_plan(plan.plan_id)
    assert ok is True

    await _wait_done(executor, plan.plan_id, store, iterations=150)

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE
    # All steps should be done
    assert all(s.status == PlanStepStatus.DONE for s in refreshed.steps)
    assert len(refreshed.steps) == 3


@pytest.mark.asyncio
async def test_dynamic_step_discovery(store, executor: TaskPlanExecutor):
    """Executor reads latest steps each iteration, not startup snapshot."""
    plan = await store.create_plan(
        session_id="s1",
        title="Dynamic",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    await executor.start_plan(plan.plan_id)
    # Wait just long enough for A to be done but before C finishes
    await asyncio.sleep(0.15)

    # Insert new step while running (B or C still pending)
    refreshed = await store.get_plan(plan.plan_id)
    if refreshed.status == PlanStatus.DONE:
        pytest.skip("Plan finished too fast")

    await store.add_step(plan.plan_id, title="D dynamic")

    await _wait_done(executor, plan.plan_id, store, iterations=150)

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE
    assert len(refreshed.steps) == 4
    assert all(s.status == PlanStepStatus.DONE for s in refreshed.steps)


@pytest.mark.asyncio
async def test_pause_does_not_cleanup_task(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Pause cleanup",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    await executor.start_plan(plan.plan_id)
    await asyncio.sleep(0.1)
    await executor.pause_plan(plan.plan_id)

    # Task should still be in _running
    assert plan.plan_id in executor._running
    assert plan.plan_id in executor._cancel_events


@pytest.mark.asyncio
async def test_done_cleans_up_task(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Cleanup",
        steps=[{"title": "A"}],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store)

    assert not executor.is_running(plan.plan_id)
    assert plan.plan_id not in executor._running
    assert plan.plan_id not in executor._cancel_events


@pytest.mark.asyncio
async def test_pause_non_running_returns_false(
    store,
    executor: TaskPlanExecutor,
):
    ok = await executor.pause_plan("nonexistent")
    assert ok is False


@pytest.mark.asyncio
async def test_resume_non_waiting_returns_false(
    store,
    executor: TaskPlanExecutor,
):
    ok = await executor.resume_plan("nonexistent")
    assert ok is False


# ---------------------------------------------------------------------------
# Risk confirmation gate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_high_risk_unconfirmed_returns_false(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(
        session_id="s1",
        title="Deploy to production",
        steps=[{"title": "deploy"}],
        metadata={"requires_confirmation": True},
    )
    started = await executor.start_plan(plan.plan_id)
    assert started is False

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.WAITING_USER


@pytest.mark.asyncio
async def test_start_high_risk_confirmed_runs(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(
        session_id="s1",
        title="Deploy",
        steps=[{"title": "deploy"}],
        metadata={"requires_confirmation": True, "confirmed": True},
    )
    started = await executor.start_plan(plan.plan_id)
    assert started is True

    await _wait_done(executor, plan.plan_id, store)
    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE


@pytest.mark.asyncio
async def test_resume_confirms_high_risk_plan(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(
        session_id="s1",
        title="Deploy",
        steps=[{"title": "deploy"}],
        metadata={"requires_confirmation": True},
    )
    # start will gate it to waiting_user
    await executor.start_plan(plan.plan_id)
    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.WAITING_USER

    # resume confirms it
    ok = await executor.resume_plan(plan.plan_id)
    assert ok is True

    await _wait_done(executor, plan.plan_id, store)
    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE


@pytest.mark.asyncio
async def test_step_level_confirmation_pauses(
    store,
    executor: TaskPlanExecutor,
):
    plan = await store.create_plan(
        session_id="s1",
        title="Mixed risk",
        steps=[
            {"title": "safe step"},
            {
                "title": "deploy to production",
                "metadata": {"requires_confirmation": True},
            },
            {"title": "after deploy"},
        ],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store, iterations=100)

    refreshed = await store.get_plan(plan.plan_id)
    # Should pause at the risky step
    assert refreshed.status == PlanStatus.WAITING_USER
    # First step done, second step waiting
    assert refreshed.steps[0].status == PlanStepStatus.DONE
    assert refreshed.steps[1].status == PlanStepStatus.WAITING_USER


@pytest.mark.asyncio
async def test_resume_confirms_waiting_step(store, executor: TaskPlanExecutor):
    plan = await store.create_plan(
        session_id="s1",
        title="Step risk",
        steps=[
            {"title": "safe"},
            {"title": "deploy", "metadata": {"requires_confirmation": True}},
            {"title": "after"},
        ],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store, iterations=100)

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.WAITING_USER

    # Resume confirms the step
    await executor.resume_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store, iterations=100)

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.DONE
    assert all(s.status == PlanStepStatus.DONE for s in refreshed.steps)


# ---------------------------------------------------------------------------
# Step 12: TaskMonitor integration
# ---------------------------------------------------------------------------


async def _get_monitor_task(plan_id, executor):
    """Helper: get the monitor task record for a plan."""
    from hubos.app.task_monitor_helpers import get_monitor_store

    mon_store = get_monitor_store()
    mid = executor._monitor_task_ids.get(plan_id)
    if mid:
        return await mon_store.get_task(mid)
    return None


@pytest.mark.asyncio
async def test_start_plan_creates_monitor_record(
    store,
    executor: TaskPlanExecutor,
):
    """start_plan should create a TaskMonitor record."""
    plan = await store.create_plan(
        session_id="s1",
        title="Monitor test",
        steps=[{"title": "A"}],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store)

    from hubos.app.task_monitor_helpers import get_monitor_store

    mon_store = get_monitor_store()
    tasks = await mon_store.list_tasks(tool_name="task_plan_executor")
    matching = [
        t
        for t in tasks
        if plan.plan_id in (t.metadata or {}).get("plan_id", "")
    ]
    assert len(matching) > 0
    mon_task = matching[0]
    assert mon_task.source == "task_plan"
    assert mon_task.tool_name == "task_plan_executor"
    assert "Monitor test" in mon_task.title


@pytest.mark.asyncio
async def test_step_emits_stage_events(store, executor: TaskPlanExecutor):
    """Executing steps should emit stage_started and stage_completed events."""
    plan = await store.create_plan(
        session_id="s1",
        title="Events test",
        steps=[{"title": "Step A"}, {"title": "Step B"}],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store)

    from hubos.app.task_monitor_helpers import get_monitor_store

    mon_store = get_monitor_store()
    tasks = await mon_store.list_tasks(tool_name="task_plan_executor")
    matching = [
        t
        for t in tasks
        if plan.plan_id in (t.metadata or {}).get("plan_id", "")
    ]
    assert len(matching) > 0
    mon_task = matching[0]
    event_types = [e.event_type for e in mon_task.events]
    assert "stage_started" in event_types
    assert "stage_completed" in event_types


@pytest.mark.asyncio
async def test_failed_step_updates_monitor_failed(
    store,
    executor: TaskPlanExecutor,
):
    """Failed step should update monitor task status to failed."""
    plan = await store.create_plan(
        session_id="s1",
        title="Fail monitor",
        steps=[{"title": "A", "agent_id": "agent-1"}],
    )
    with patch(
        "hubos.core.workers.registry.get_host_agent_runner",
        return_value=None,
    ):
        await executor.start_plan(plan.plan_id)
        await _wait_done(executor, plan.plan_id, store)

    from hubos.app.task_monitor import TaskStatus
    from hubos.app.task_monitor_helpers import get_monitor_store

    mon_store = get_monitor_store()
    tasks = await mon_store.list_tasks(tool_name="task_plan_executor")
    matching = [
        t
        for t in tasks
        if plan.plan_id in (t.metadata or {}).get("plan_id", "")
    ]
    assert len(matching) > 0
    assert matching[0].status == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_waiting_step_updates_monitor_waiting(
    store,
    executor: TaskPlanExecutor,
):
    """waiting_user step should update monitor task status to waiting."""
    plan = await store.create_plan(
        session_id="s1",
        title="Wait monitor",
        steps=[{"title": "Tool", "tool_name": "some_tool"}],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store)

    mon_task = await _get_monitor_task(plan.plan_id, executor)
    assert mon_task is not None
    from hubos.app.task_monitor import TaskStatus

    assert mon_task.status == TaskStatus.WAITING


@pytest.mark.asyncio
async def test_cancel_updates_monitor_cancelled(
    store,
    executor: TaskPlanExecutor,
):
    """Cancelling a plan should update monitor task status to cancelled."""
    plan = await store.create_plan(
        session_id="s1",
        title="Cancel monitor",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    await executor.start_plan(plan.plan_id)
    await asyncio.sleep(0.05)

    mid = executor._monitor_task_ids.get(plan.plan_id)
    assert mid is not None

    await executor.cancel_plan(plan.plan_id)

    from hubos.app.task_monitor import TaskStatus
    from hubos.app.task_monitor_helpers import get_monitor_store

    mon_store = get_monitor_store()
    mon_task = await mon_store.get_task(mid)
    assert mon_task is not None
    assert mon_task.status == TaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_done_updates_monitor_done(store, executor: TaskPlanExecutor):
    """Completed plan should update monitor task status to done with progress 100."""
    plan = await store.create_plan(
        session_id="s1",
        title="Done monitor",
        steps=[{"title": "A"}],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store)

    from hubos.app.task_monitor import TaskStatus
    from hubos.app.task_monitor_helpers import get_monitor_store

    mon_store = get_monitor_store()
    tasks = await mon_store.list_tasks(tool_name="task_plan_executor")
    matching = [
        t
        for t in tasks
        if plan.plan_id in (t.metadata or {}).get("plan_id", "")
    ]
    assert len(matching) > 0
    assert matching[0].status == TaskStatus.DONE
    assert matching[0].progress == 100


@pytest.mark.asyncio
async def test_monitor_cancel_handler_cancels_plan(
    store,
    executor: TaskPlanExecutor,
):
    """Triggering monitor cancel handler should cancel the plan."""
    plan = await store.create_plan(
        session_id="s1",
        title="Handler cancel",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    await executor.start_plan(plan.plan_id)
    await asyncio.sleep(0.1)

    mid = executor._monitor_task_ids.get(plan.plan_id)
    assert mid is not None

    from hubos.app.task_monitor_helpers import request_cancel_task

    await request_cancel_task(mid)

    # Give it a moment for the async cancel to propagate
    await asyncio.sleep(0.2)

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.CANCELLED


@pytest.mark.asyncio
async def test_cleanup_unregisters_monitor_mapping(
    store,
    executor: TaskPlanExecutor,
):
    """After plan finishes, monitor mapping and cancel handler should be cleaned up."""
    plan = await store.create_plan(
        session_id="s1",
        title="Cleanup monitor",
        steps=[{"title": "A"}],
    )
    await executor.start_plan(plan.plan_id)
    await _wait_done(executor, plan.plan_id, store)

    # Mapping should be gone
    assert plan.plan_id not in executor._monitor_task_ids
    # Executor state should be clean
    assert plan.plan_id not in executor._running
    assert plan.plan_id not in executor._cancel_events
