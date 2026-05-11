# -*- coding: utf-8 -*-
"""Unit tests for the task-monitor API router."""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hubos.app.routers.task_monitor import router
from hubos.app.task_monitor import TaskEventType, TaskMonitorStore, TaskStatus
from hubos.app.task_monitor_helpers import get_monitor_store

app = FastAPI()
app.include_router(router, prefix="/api")


@pytest.fixture(autouse=True)
def _reset_monitor_store():
    """Reset the global monitor store between tests."""
    import hubos.app.task_monitor_helpers as _helpers

    old = _helpers._store
    old_handlers = dict(_helpers._cancel_handlers)
    _helpers._store = None
    _helpers._cancel_handlers.clear()
    yield
    _helpers._store = old
    _helpers._cancel_handlers.clear()
    _helpers._cancel_handlers.update(old_handlers)


@pytest.fixture
def store() -> TaskMonitorStore:
    return get_monitor_store()


@pytest.fixture
def api_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# GET /task-monitor/tasks — empty
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tasks_empty(api_client: AsyncClient):
    async with api_client:
        resp = await api_client.get("/api/task-monitor/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body["tasks"] == []
    assert body["count"] == 0


# ---------------------------------------------------------------------------
# GET /task-monitor/tasks — after create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tasks_after_create(api_client: AsyncClient, store: TaskMonitorStore):
    await store.create_task(
        session_id="s1",
        source="tool",
        title="Test task",
        tool_name="spawn_subagents",
    )
    async with api_client:
        resp = await api_client.get("/api/task-monitor/tasks")
    body = resp.json()
    assert body["count"] == 1
    task = body["tasks"][0]
    assert task["title"] == "Test task"
    assert task["status"] == "pending"
    assert task["tool_name"] == "spawn_subagents"


# ---------------------------------------------------------------------------
# GET /task-monitor/tasks — filters
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(api_client: AsyncClient, store: TaskMonitorStore):
    t1 = await store.create_task(session_id="s1", source="tool", title="t1")
    await store.create_task(session_id="s2", source="tool", title="t2")
    await store.update_task(t1.task_id, status=TaskStatus.RUNNING)

    async with api_client:
        resp = await api_client.get("/api/task-monitor/tasks", params={"status": "running"})
    body = resp.json()
    assert body["count"] == 1
    assert body["tasks"][0]["task_id"] == t1.task_id


@pytest.mark.asyncio
async def test_list_tasks_filter_by_session(api_client: AsyncClient, store: TaskMonitorStore):
    await store.create_task(session_id="s1", source="tool", title="t1")
    await store.create_task(session_id="s2", source="tool", title="t2")
    await store.create_task(session_id="s1", source="tool", title="t3")

    async with api_client:
        resp = await api_client.get("/api/task-monitor/tasks", params={"session_id": "s1"})
    assert resp.json()["count"] == 2


@pytest.mark.asyncio
async def test_list_tasks_filter_by_tool_name(api_client: AsyncClient, store: TaskMonitorStore):
    await store.create_task(
        session_id="s1", source="tool", title="t1", tool_name="spawn_subagents",
    )
    await store.create_task(
        session_id="s1", source="tool", title="t2", tool_name="coordinate_workflow",
    )

    async with api_client:
        resp = await api_client.get(
            "/api/task-monitor/tasks", params={"tool_name": "spawn_subagents"},
        )
    assert resp.json()["count"] == 1
    assert resp.json()["tasks"][0]["tool_name"] == "spawn_subagents"


@pytest.mark.asyncio
async def test_list_tasks_invalid_status(api_client: AsyncClient):
    async with api_client:
        resp = await api_client.get("/api/task-monitor/tasks", params={"status": "bogus"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /task-monitor/tasks/{task_id}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_task_detail(api_client: AsyncClient, store: TaskMonitorStore):
    task = await store.create_task(
        session_id="s1",
        source="tool",
        title="Detail test",
        tool_name="delegate_task",
        agent_id="agent-x",
    )
    await store.update_task(task.task_id, status=TaskStatus.RUNNING)
    await store.add_event(
        task.task_id, TaskEventType.STAGE_STARTED, "Stage 1", stage="step-1",
    )

    async with api_client:
        resp = await api_client.get(f"/api/task-monitor/tasks/{task.task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["task_id"] == task.task_id
    assert body["status"] == "running"
    assert body["tool_name"] == "delegate_task"
    assert body["agent_id"] == "agent-x"
    assert len(body["events"]) == 2  # created + stage_started (update_task broadcasts but doesn't append to events)


@pytest.mark.asyncio
async def test_get_task_detail_404(api_client: AsyncClient):
    async with api_client:
        resp = await api_client.get("/api/task-monitor/tasks/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /task-monitor/tasks/{task_id}/cancel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_task_marks_running_task_cancelled(
    api_client: AsyncClient,
    store: TaskMonitorStore,
):
    task = await store.create_task(session_id="s1", source="tool", title="cancel me")
    await store.update_task(task.task_id, status=TaskStatus.RUNNING)

    async with api_client:
        resp = await api_client.post(f"/api/task-monitor/tasks/{task.task_id}/cancel")

    assert resp.status_code == 200
    refreshed = await store.get_task(task.task_id)
    assert refreshed.status == TaskStatus.CANCELLED
    assert refreshed.finished_at is not None
    assert any(e.event_type == TaskEventType.TASK_CANCELLED for e in refreshed.events)


@pytest.mark.asyncio
async def test_cancel_task_404(api_client: AsyncClient):
    async with api_client:
        resp = await api_client.post("/api/task-monitor/tasks/nonexistent/cancel")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /task-monitor/stream — SSE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stream_event_generator(store: TaskMonitorStore):
    """SSE generator should produce properly formatted data events.

    We iterate the async generator directly to avoid httpx ASGI transport
    issues with infinite SSE streams.
    """
    from hubos.app.routers.task_monitor import stream_events

    response = await stream_events(session_id=None, tool_name=None)
    assert response.media_type == "text/event-stream"

    gen = response.body_iterator

    # Schedule the first anext() so the generator starts and waits on queue
    read_task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.1)  # Let generator reach queue.get()

    # Trigger a broadcast event
    await store.create_task(session_id="s1", source="tool", title="stream test")

    # Read the yielded SSE chunk
    chunk = await asyncio.wait_for(read_task, timeout=2.0)
    await gen.aclose()  # Clean up

    text = chunk if isinstance(chunk, str) else chunk.decode("utf-8")
    assert "data: " in text
    payload = json.loads(text.split("data: ", 1)[1].strip())
    assert payload["type"] == "task_created"
    assert "task_id" in payload


@pytest.mark.asyncio
async def test_stream_cleanup_on_close(store: TaskMonitorStore):
    """Closing the SSE generator should unsubscribe from the store."""
    from hubos.app.routers.task_monitor import stream_events

    sub_count_before = len(store._subscribers)
    response = await stream_events(session_id=None, tool_name=None)
    gen = response.body_iterator

    # Start the generator so it enters the try block
    read_task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)
    assert len(store._subscribers) > sub_count_before

    # Cancel pending read and close generator
    read_task.cancel()
    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
        await read_task
    await gen.aclose()
    await asyncio.sleep(0.05)

    assert len(store._subscribers) == sub_count_before
