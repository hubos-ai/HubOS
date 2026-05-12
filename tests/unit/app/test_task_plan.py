# -*- coding: utf-8 -*-
"""Unit tests for TaskPlanStore."""
from __future__ import annotations

import asyncio
import time

import pytest

from hubos.app.task_plan import (
    PlanEventType,
    PlanStatus,
    PlanStepStatus,
    TaskPlanStep,
    TaskPlanStore,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store() -> TaskPlanStore:
    return TaskPlanStore()


# ---------------------------------------------------------------------------
# create_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_plan_minimal(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="Test plan")
    assert plan.plan_id
    assert plan.session_id == "s1"
    assert plan.title == "Test plan"
    assert plan.status == PlanStatus.DRAFT
    assert plan.steps == []
    assert plan.created_at > 0
    assert plan.updated_at == plan.created_at
    assert plan.finished_at is None
    assert plan.metadata is None


@pytest.mark.asyncio
async def test_create_plan_with_metadata(store: TaskPlanStore):
    plan = await store.create_plan(
        session_id="s1",
        title="P",
        metadata={"key": "val"},
    )
    assert plan.metadata == {"key": "val"}


@pytest.mark.asyncio
async def test_create_plan_with_initial_steps(store: TaskPlanStore):
    plan = await store.create_plan(
        session_id="s1",
        title="With steps",
        steps=[
            {"title": "Step A", "description": "Do A"},
            {"title": "Step B", "depends_on": [], "agent_id": "agent-1"},
        ],
    )
    assert len(plan.steps) == 2
    assert plan.steps[0].title == "Step A"
    assert plan.steps[0].description == "Do A"
    assert plan.steps[0].order == 0
    assert plan.steps[0].status == PlanStepStatus.PENDING
    assert plan.steps[1].title == "Step B"
    assert plan.steps[1].agent_id == "agent-1"
    assert plan.steps[1].order == 1


@pytest.mark.asyncio
async def test_create_plan_with_step_metadata(store: TaskPlanStore):
    plan = await store.create_plan(
        session_id="s1",
        title="P",
        steps=[
            {"title": "S1", "metadata": {"x": 1}},
        ],
    )
    assert plan.steps[0].metadata == {"x": 1}


# ---------------------------------------------------------------------------
# get_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_plan_existing(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    fetched = await store.get_plan(plan.plan_id)
    assert fetched is not None
    assert fetched.plan_id == plan.plan_id


@pytest.mark.asyncio
async def test_get_plan_missing(store: TaskPlanStore):
    assert await store.get_plan("nonexistent") is None


# ---------------------------------------------------------------------------
# add_step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_step_auto_order(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    s1 = await store.add_step(plan.plan_id, title="First")
    s2 = await store.add_step(plan.plan_id, title="Second")
    assert s1.order == 0
    assert s2.order == 1
    assert s1.status == PlanStepStatus.PENDING


@pytest.mark.asyncio
async def test_add_step_with_deps(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    s1 = await store.add_step(plan.plan_id, title="A")
    s2 = await store.add_step(
        plan.plan_id,
        title="B",
        description="Depends on A",
        agent_id="agent-x",
        tool_name="search",
        depends_on=[s1.step_id],
        metadata={"priority": 1},
    )
    assert s2.depends_on == [s1.step_id]
    assert s2.agent_id == "agent-x"
    assert s2.tool_name == "search"
    assert s2.metadata == {"priority": 1}

    # Verify plan.steps was updated
    refreshed = await store.get_plan(plan.plan_id)
    assert len(refreshed.steps) == 2


@pytest.mark.asyncio
async def test_add_step_not_found(store: TaskPlanStore):
    with pytest.raises(KeyError, match="Plan not found"):
        await store.add_step("nonexistent", title="X")


@pytest.mark.asyncio
async def test_add_step_after_target(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    s0 = await store.add_step(plan.plan_id, title="A")
    s1 = await store.add_step(plan.plan_id, title="B")
    s2 = await store.add_step(plan.plan_id, title="C")

    # Insert after s0 → new step at order 1, B and C shift
    s_new = await store.add_step(
        plan.plan_id,
        title="Inserted",
        after_step_id=s0.step_id,
    )

    refreshed = await store.get_plan(plan.plan_id)
    orders = [s.order for s in refreshed.steps]
    titles = [s.title for s in refreshed.steps]
    assert titles == ["A", "Inserted", "B", "C"]
    assert orders == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_add_step_after_missing_step_raises(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    await store.add_step(plan.plan_id, title="A")
    with pytest.raises(KeyError, match="Step not found"):
        await store.add_step(
            plan.plan_id,
            title="X",
            after_step_id="nonexistent",
        )


@pytest.mark.asyncio
async def test_add_step_terminal_plan_raises(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    await store.cancel_plan(plan.plan_id)
    with pytest.raises(ValueError, match="terminal status"):
        await store.add_step(plan.plan_id, title="X")


@pytest.mark.asyncio
async def test_add_step_after_last(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    s0 = await store.add_step(plan.plan_id, title="A")
    s1 = await store.add_step(plan.plan_id, title="B")

    s_new = await store.add_step(
        plan.plan_id,
        title="Tail",
        after_step_id=s1.step_id,
    )

    refreshed = await store.get_plan(plan.plan_id)
    titles = [s.title for s in refreshed.steps]
    assert titles == ["A", "B", "Tail"]


# ---------------------------------------------------------------------------
# update_step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_step_status(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(plan.plan_id, title="S")

    updated = await store.update_step(
        plan.plan_id,
        step.step_id,
        status=PlanStepStatus.RUNNING,
    )
    assert updated.status == PlanStepStatus.RUNNING


@pytest.mark.asyncio
async def test_update_step_done_sets_finished_at(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(plan.plan_id, title="S")

    updated = await store.update_step(
        plan.plan_id,
        step.step_id,
        status=PlanStepStatus.DONE,
    )
    assert updated.finished_at is not None
    assert updated.status == PlanStepStatus.DONE


@pytest.mark.asyncio
async def test_update_step_failed_sets_finished_at(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(plan.plan_id, title="S")

    updated = await store.update_step(
        plan.plan_id,
        step.step_id,
        status=PlanStepStatus.FAILED,
        error="boom",
    )
    assert updated.finished_at is not None
    assert updated.error == "boom"


@pytest.mark.asyncio
async def test_update_step_cancelled_sets_finished_at(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(plan.plan_id, title="S")

    updated = await store.update_step(
        plan.plan_id,
        step.step_id,
        status=PlanStepStatus.CANCELLED,
    )
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_update_step_title_description(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(plan.plan_id, title="Old")

    updated = await store.update_step(
        plan.plan_id,
        step.step_id,
        title="New",
        description="desc",
    )
    assert updated.title == "New"
    assert updated.description == "desc"


@pytest.mark.asyncio
async def test_update_step_metadata_merges(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(
        plan.plan_id,
        title="S",
        metadata={"a": 1},
    )

    await store.update_step(
        plan.plan_id,
        step.step_id,
        metadata={"b": 2},
    )
    refreshed_plan = await store.get_plan(plan.plan_id)
    refreshed_step = refreshed_plan.steps[0]
    assert refreshed_step.metadata == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_update_step_not_found_plan(store: TaskPlanStore):
    with pytest.raises(KeyError, match="Plan not found"):
        await store.update_step("nonexistent", "x", status=PlanStepStatus.DONE)


@pytest.mark.asyncio
async def test_update_step_not_found_step(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    with pytest.raises(KeyError, match="Step not found"):
        await store.update_step(
            plan.plan_id,
            "nonexistent",
            status=PlanStepStatus.DONE,
        )


# ---------------------------------------------------------------------------
# update_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_plan_status(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    updated = await store.update_plan(
        plan.plan_id,
        status=PlanStatus.RUNNING,
    )
    assert updated.status == PlanStatus.RUNNING


@pytest.mark.asyncio
async def test_update_plan_done_sets_finished_at(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    updated = await store.update_plan(plan.plan_id, status=PlanStatus.DONE)
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_update_plan_failed_sets_finished_at(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    updated = await store.update_plan(plan.plan_id, status=PlanStatus.FAILED)
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_update_plan_cancelled_sets_finished_at(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    updated = await store.update_plan(
        plan.plan_id,
        status=PlanStatus.CANCELLED,
    )
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_update_plan_current_step(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(plan.plan_id, title="S")
    updated = await store.update_plan(
        plan.plan_id,
        current_step_id=step.step_id,
    )
    assert updated.current_step_id == step.step_id


@pytest.mark.asyncio
async def test_update_plan_metadata_merges(store: TaskPlanStore):
    plan = await store.create_plan(
        session_id="s1",
        title="P",
        metadata={"a": 1},
    )
    await store.update_plan(plan.plan_id, metadata={"b": 2})
    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.metadata == {"a": 1, "b": 2}


@pytest.mark.asyncio
async def test_update_plan_not_found(store: TaskPlanStore):
    with pytest.raises(KeyError, match="Plan not found"):
        await store.update_plan("nonexistent", status=PlanStatus.DONE)


# ---------------------------------------------------------------------------
# cancel_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_plan_marks_plan_cancelled(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    cancelled = await store.cancel_plan(plan.plan_id)
    assert cancelled.status == PlanStatus.CANCELLED
    assert cancelled.finished_at is not None


@pytest.mark.asyncio
async def test_cancel_plan_cancels_incomplete_steps(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    s1 = await store.add_step(plan.plan_id, title="Running")
    s2 = await store.add_step(plan.plan_id, title="Done")
    s3 = await store.add_step(plan.plan_id, title="Pending")

    await store.update_step(
        plan.plan_id,
        s1.step_id,
        status=PlanStepStatus.RUNNING,
    )
    await store.update_step(
        plan.plan_id,
        s2.step_id,
        status=PlanStepStatus.DONE,
    )

    cancelled = await store.cancel_plan(plan.plan_id)

    # s1 was running → cancelled
    step1 = _get_step(cancelled, s1.step_id)
    assert step1.status == PlanStepStatus.CANCELLED
    assert step1.finished_at is not None

    # s2 was done → stays done
    step2 = _get_step(cancelled, s2.step_id)
    assert step2.status == PlanStepStatus.DONE

    # s3 was pending → cancelled
    step3 = _get_step(cancelled, s3.step_id)
    assert step3.status == PlanStepStatus.CANCELLED
    assert step3.finished_at is not None


@pytest.mark.asyncio
async def test_cancel_plan_not_found(store: TaskPlanStore):
    with pytest.raises(KeyError, match="Plan not found"):
        await store.cancel_plan("nonexistent")


# ---------------------------------------------------------------------------
# pause_plan / resume_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_plan_running(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    await store.update_plan(plan.plan_id, status=PlanStatus.RUNNING)
    paused = await store.pause_plan(plan.plan_id)
    assert paused.status == PlanStatus.WAITING_USER


@pytest.mark.asyncio
async def test_pause_plan_non_running_raises(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    with pytest.raises(ValueError, match="Cannot pause"):
        await store.pause_plan(plan.plan_id)


@pytest.mark.asyncio
async def test_resume_plan_waiting_user(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    await store.update_plan(plan.plan_id, status=PlanStatus.RUNNING)
    await store.pause_plan(plan.plan_id)
    resumed = await store.resume_plan(plan.plan_id)
    assert resumed.status == PlanStatus.RUNNING


@pytest.mark.asyncio
async def test_resume_plan_non_waiting_raises(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    with pytest.raises(ValueError, match="Cannot resume"):
        await store.resume_plan(plan.plan_id)


@pytest.mark.asyncio
async def test_pause_plan_not_found(store: TaskPlanStore):
    with pytest.raises(KeyError, match="Plan not found"):
        await store.pause_plan("nonexistent")


@pytest.mark.asyncio
async def test_resume_plan_not_found(store: TaskPlanStore):
    with pytest.raises(KeyError, match="Plan not found"):
        await store.resume_plan("nonexistent")


@pytest.mark.asyncio
async def test_list_plans_returns_all(store: TaskPlanStore):
    await store.create_plan(session_id="s1", title="A")
    await store.create_plan(session_id="s2", title="B")
    plans = await store.list_plans()
    assert len(plans) == 2


@pytest.mark.asyncio
async def test_list_plans_filter_by_session(store: TaskPlanStore):
    await store.create_plan(session_id="s1", title="A")
    await store.create_plan(session_id="s2", title="B")
    await store.create_plan(session_id="s1", title="C")
    plans = await store.list_plans(session_id="s1")
    assert len(plans) == 2
    assert all(p.session_id == "s1" for p in plans)


@pytest.mark.asyncio
async def test_list_plans_filter_by_status(store: TaskPlanStore):
    p1 = await store.create_plan(session_id="s1", title="A")
    await store.create_plan(session_id="s2", title="B")
    await store.update_plan(p1.plan_id, status=PlanStatus.RUNNING)
    plans = await store.list_plans(status=PlanStatus.RUNNING)
    assert len(plans) == 1
    assert plans[0].plan_id == p1.plan_id


@pytest.mark.asyncio
async def test_list_plans_limit(store: TaskPlanStore):
    for i in range(5):
        await store.create_plan(session_id="s1", title=f"P{i}")
    plans = await store.list_plans(limit=3)
    assert len(plans) == 3


@pytest.mark.asyncio
async def test_list_plans_ordered_newest_first(store: TaskPlanStore):
    p1 = await store.create_plan(session_id="s1", title="Old")
    p2 = await store.create_plan(session_id="s1", title="New")
    plans = await store.list_plans()
    assert plans[0].plan_id == p2.plan_id
    assert plans[1].plan_id == p1.plan_id


# ---------------------------------------------------------------------------
# subscribe / unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subscribe_receives_plan_created(store: TaskPlanStore):
    sub_id, queue = store.subscribe()
    try:
        plan = await store.create_plan(session_id="s1", title="P")
        event = queue.get_nowait()
        assert event.event_type == PlanEventType.PLAN_CREATED
        assert event.plan_id == plan.plan_id
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_subscribe_receives_step_added(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    sub_id, queue = store.subscribe()
    try:
        await store.add_step(plan.plan_id, title="S")
        # Skip plan_created from before subscribe — only step_added expected
        event = queue.get_nowait()
        assert event.event_type == PlanEventType.STEP_ADDED
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_subscribe_receives_step_updated(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    step = await store.add_step(plan.plan_id, title="S")
    sub_id, queue = store.subscribe()
    try:
        await store.update_step(
            plan.plan_id,
            step.step_id,
            status=PlanStepStatus.RUNNING,
        )
        event = queue.get_nowait()
        assert event.event_type == PlanEventType.STEP_STARTED
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_subscribe_receives_plan_cancelled(store: TaskPlanStore):
    plan = await store.create_plan(session_id="s1", title="P")
    sub_id, queue = store.subscribe()
    try:
        await store.cancel_plan(plan.plan_id)
        event = queue.get_nowait()
        assert event.event_type == PlanEventType.PLAN_CANCELLED
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery(store: TaskPlanStore):
    sub_id, queue = store.subscribe()
    store.unsubscribe(sub_id)
    await store.create_plan(session_id="s1", title="P")
    assert queue.empty()


@pytest.mark.asyncio
async def test_multiple_subscribers(store: TaskPlanStore):
    s1, q1 = store.subscribe()
    s2, q2 = store.subscribe()
    try:
        await store.create_plan(session_id="s1", title="P")
        assert not q1.empty()
        assert not q2.empty()
    finally:
        store.unsubscribe(s1)
        store.unsubscribe(s2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_step(plan, step_id: str) -> TaskPlanStep:
    for s in plan.steps:
        if s.step_id == step_id:
            return s
    raise AssertionError(f"Step {step_id} not found")
