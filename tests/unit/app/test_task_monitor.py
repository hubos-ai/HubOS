# -*- coding: utf-8 -*-
"""Unit tests for hubos.app.task_monitor."""
from __future__ import annotations

import asyncio

import pytest

from hubos.app.task_monitor import (
    BroadcastEvent,
    TaskEventType,
    TaskMonitorStore,
    TaskStatus,
)


@pytest.fixture
def store() -> TaskMonitorStore:
    """Fresh store per test — no shared state."""
    return TaskMonitorStore()


# ---------------------------------------------------------------------------
# create_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_task_returns_task_with_correct_fields(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="sess-1",
        source="coordinate_workflow",
        title="Run pipeline",
        tool_name="bash",
        agent_id="agent-a",
        metadata={"priority": "high"},
    )

    assert task.session_id == "sess-1"
    assert task.source == "coordinate_workflow"
    assert task.title == "Run pipeline"
    assert task.status == TaskStatus.PENDING
    assert task.tool_name == "bash"
    assert task.agent_id == "agent-a"
    assert task.metadata == {"priority": "high"}
    assert task.task_id
    assert task.created_at > 0
    assert len(task.events) == 1
    assert task.events[0].event_type == TaskEventType.TASK_CREATED


@pytest.mark.asyncio
async def test_create_task_minimal(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s2",
        source="manual",
        title="Do something",
    )
    assert task.tool_name is None
    assert task.agent_id is None
    assert task.metadata is None


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_task_status(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    updated = await store.update_task(task.task_id, status=TaskStatus.RUNNING)
    assert updated.status == TaskStatus.RUNNING
    assert updated.finished_at is None


@pytest.mark.asyncio
async def test_update_task_done_sets_finished_at(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    updated = await store.update_task(
        task.task_id,
        status=TaskStatus.DONE,
        result_summary="All good",
    )
    assert updated.status == TaskStatus.DONE
    assert updated.finished_at is not None
    assert updated.result_summary == "All good"


@pytest.mark.asyncio
async def test_update_task_failed_sets_finished_at(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    updated = await store.update_task(
        task.task_id,
        status=TaskStatus.FAILED,
        error="Something broke",
    )
    assert updated.status == TaskStatus.FAILED
    assert updated.error == "Something broke"
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_update_task_cancelled_sets_finished_at(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    updated = await store.update_task(
        task.task_id,
        status=TaskStatus.CANCELLED,
        result_summary="Cancelled by user",
    )
    assert updated.status == TaskStatus.CANCELLED
    assert updated.result_summary == "Cancelled by user"
    assert updated.finished_at is not None


@pytest.mark.asyncio
async def test_update_task_progress_and_stage(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    updated = await store.update_task(
        task.task_id,
        current_stage="Stage 2",
        progress=0.5,
    )
    assert updated.current_stage == "Stage 2"
    assert updated.progress == 0.5


@pytest.mark.asyncio
async def test_update_task_metadata_merges(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1",
        source="test",
        title="t1",
        metadata={"key1": "val1"},
    )
    updated = await store.update_task(
        task.task_id,
        metadata={"key2": "val2"},
    )
    assert updated.metadata == {"key1": "val1", "key2": "val2"}


@pytest.mark.asyncio
async def test_update_task_not_found_raises(store: TaskMonitorStore):
    with pytest.raises(KeyError, match="Task not found"):
        await store.update_task("nonexistent", status=TaskStatus.RUNNING)


# ---------------------------------------------------------------------------
# add_event
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_event_appends_to_task(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    evt = await store.add_event(
        task.task_id,
        TaskEventType.LOG,
        "Processing started",
        stage="stage-1",
        agent_id="agent-x",
    )
    assert evt.event_type == TaskEventType.LOG
    assert evt.message == "Processing started"
    assert evt.stage == "stage-1"
    assert evt.agent_id == "agent-x"

    refreshed = await store.get_task(task.task_id)
    assert len(refreshed.events) == 2  # created + log


@pytest.mark.asyncio
async def test_add_event_not_found_raises(store: TaskMonitorStore):
    with pytest.raises(KeyError, match="Task not found"):
        await store.add_event("nope", TaskEventType.LOG, "msg")


# ---------------------------------------------------------------------------
# list_tasks — filtering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tasks_returns_all(store: TaskMonitorStore):
    await store.create_task(session_id="s1", source="a", title="t1")
    await store.create_task(session_id="s2", source="b", title="t2")
    tasks = await store.list_tasks()
    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(store: TaskMonitorStore):
    t1 = await store.create_task(session_id="s1", source="a", title="t1")
    await store.create_task(session_id="s2", source="b", title="t2")
    await store.update_task(t1.task_id, status=TaskStatus.RUNNING)

    running = await store.list_tasks(status=TaskStatus.RUNNING)
    assert len(running) == 1
    assert running[0].task_id == t1.task_id


@pytest.mark.asyncio
async def test_list_tasks_filter_by_session(store: TaskMonitorStore):
    await store.create_task(session_id="s1", source="a", title="t1")
    await store.create_task(session_id="s2", source="b", title="t2")
    await store.create_task(session_id="s1", source="c", title="t3")

    filtered = await store.list_tasks(session_id="s1")
    assert len(filtered) == 2


@pytest.mark.asyncio
async def test_list_tasks_limit(store: TaskMonitorStore):
    for i in range(5):
        await store.create_task(session_id="s1", source="a", title=f"t{i}")
    tasks = await store.list_tasks(limit=3)
    assert len(tasks) == 3


@pytest.mark.asyncio
async def test_list_tasks_ordered_newest_first(store: TaskMonitorStore):
    t1 = await store.create_task(session_id="s1", source="a", title="first")
    t2 = await store.create_task(session_id="s1", source="a", title="second")
    tasks = await store.list_tasks()
    assert tasks[0].task_id == t2.task_id
    assert tasks[1].task_id == t1.task_id


# ---------------------------------------------------------------------------
# get_task
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_existing(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    fetched = await store.get_task(task.task_id)
    assert fetched is not None
    assert fetched.task_id == task.task_id


@pytest.mark.asyncio
async def test_get_task_missing_returns_none(store: TaskMonitorStore):
    result = await store.get_task("does-not-exist")
    assert result is None


# ---------------------------------------------------------------------------
# subscribe / unsubscribe — broadcast
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_subscribe_receives_create_event(store: TaskMonitorStore):
    sub_id, queue = store.subscribe()
    try:
        task = await store.create_task(
            session_id="s1", source="test", title="t1",
        )
        event = queue.get_nowait()
        assert event.event_type == TaskEventType.TASK_CREATED
        assert event.task_id == task.task_id
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_subscribe_receives_update_event(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    sub_id, queue = store.subscribe()
    try:
        await store.update_task(task.task_id, status=TaskStatus.RUNNING)
        event = queue.get_nowait()
        assert event.event_type == TaskEventType.TASK_UPDATED
        assert event.data["status"] == "running"
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_subscribe_receives_done_event(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    sub_id, queue = store.subscribe()
    try:
        await store.update_task(task.task_id, status=TaskStatus.DONE)
        event = queue.get_nowait()
        assert event.event_type == TaskEventType.TASK_DONE
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_subscribe_receives_add_event(store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1", source="test", title="t1",
    )
    sub_id, queue = store.subscribe()
    try:
        await store.add_event(
            task.task_id, TaskEventType.STAGE_STARTED, "Stage 1 begin",
        )
        event = queue.get_nowait()
        assert event.event_type == TaskEventType.STAGE_STARTED
        assert event.data["message"] == "Stage 1 begin"
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery(store: TaskMonitorStore):
    sub_id, queue = store.subscribe()
    store.unsubscribe(sub_id)
    # Queue should not receive anything after unsubscribe
    await store.create_task(session_id="s1", source="test", title="t1")
    assert queue.empty()


@pytest.mark.asyncio
async def test_multiple_subscribers(store: TaskMonitorStore):
    sub1_id, q1 = store.subscribe()
    sub2_id, q2 = store.subscribe()
    try:
        await store.create_task(session_id="s1", source="test", title="t1")
        assert not q1.empty()
        assert not q2.empty()
        e1 = q1.get_nowait()
        e2 = q2.get_nowait()
        assert e1.task_id == e2.task_id
    finally:
        store.unsubscribe(sub1_id)
        store.unsubscribe(sub2_id)
