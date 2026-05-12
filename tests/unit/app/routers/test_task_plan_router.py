# -*- coding: utf-8 -*-
"""Unit tests for the task-plans API router."""
from __future__ import annotations

import asyncio
import contextlib
import json

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hubos.app.routers.task_plan import router
from hubos.app.task_plan import (
    PlanEventType,
    PlanStatus,
    PlanStepStatus,
    get_plan_store,
)

app = FastAPI()
app.include_router(router, prefix="/api")


@pytest.fixture(autouse=True)
def _reset_plan_store():
    """Reset the global plan store and executor between tests."""
    import hubos.app.task_plan as _mod
    import hubos.app.task_plan_executor as _exec_mod

    old = _mod._store
    old_exec = _exec_mod._executor
    _mod._store = None
    _exec_mod._executor = None
    yield
    _mod._store = old
    _exec_mod._executor = old_exec


@pytest.fixture
def store():
    return get_plan_store()


@pytest.fixture
def api_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# GET /task-plans — empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_empty(api_client: AsyncClient):
    resp = await api_client.get("/api/task-plans")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plans"] == []
    assert body["count"] == 0


# ---------------------------------------------------------------------------
# POST /task-plans — create
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_plan_minimal(api_client: AsyncClient):
    resp = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "Test plan",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == "s1"
    assert body["title"] == "Test plan"
    assert body["status"] == "draft"
    assert body["steps"] == []


@pytest.mark.asyncio
async def test_create_plan_with_initial_steps(api_client: AsyncClient):
    resp = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "With steps",
            "steps": [
                {"title": "Step A", "description": "Do A"},
                {"title": "Step B", "agent_id": "agent-1", "depends_on": []},
            ],
            "metadata": {"priority": "high"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["steps"]) == 2
    assert body["steps"][0]["title"] == "Step A"
    assert body["steps"][0]["order"] == 0
    assert body["steps"][1]["agent_id"] == "agent-1"
    assert body["metadata"] == {"priority": "high"}


# ---------------------------------------------------------------------------
# GET /task-plans/{plan_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_detail(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "P",
        },
    )
    plan_id = create.json()["plan_id"]

    resp = await api_client.get(f"/api/task-plans/{plan_id}")
    assert resp.status_code == 200
    assert resp.json()["plan_id"] == plan_id


@pytest.mark.asyncio
async def test_get_detail_404(api_client: AsyncClient):
    resp = await api_client.get("/api/task-plans/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /task-plans — filters
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_by_session_id(api_client: AsyncClient):
    await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "A"},
    )
    await api_client.post(
        "/api/task-plans",
        json={"session_id": "s2", "title": "B"},
    )

    resp = await api_client.get("/api/task-plans", params={"session_id": "s1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["plans"][0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_list_by_status(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "A"},
    )
    plan_id = create.json()["plan_id"]
    # Cancel one plan
    await api_client.post(f"/api/task-plans/{plan_id}/cancel")

    resp = await api_client.get(
        "/api/task-plans",
        params={"status": "cancelled"},
    )
    assert resp.status_code == 200
    assert resp.json()["count"] == 1


@pytest.mark.asyncio
async def test_list_invalid_status(api_client: AsyncClient):
    resp = await api_client.get("/api/task-plans", params={"status": "bogus"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /task-plans/{plan_id}/steps — add step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_step(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]

    resp = await api_client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={
            "title": "New step",
            "description": "Do something",
            "agent_id": "agent-x",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "New step"
    assert body["agent_id"] == "agent-x"
    assert body["order"] == 0
    assert body["status"] == "pending"


@pytest.mark.asyncio
async def test_add_step_404(api_client: AsyncClient):
    resp = await api_client.post(
        "/api/task-plans/nonexistent/steps",
        json={
            "title": "X",
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_step_chat_insert_auto_agent(api_client: AsyncClient):
    """Chat-inserted step gets auto agent_id from heuristic."""
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]

    resp = await api_client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={
            "title": "修复代码中的bug",
            "metadata": {"inserted_from_chat": True},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent_id"] == "rd"
    assert body["metadata"]["inserted_from_chat"] is True
    assert body["metadata"]["agent_routing"] == "heuristic"


@pytest.mark.asyncio
async def test_add_step_chat_insert_running_plan_uses_current_step(
    api_client: AsyncClient,
):
    """Chat-inserted step on a running plan inserts after current_step_id."""
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "P",
            "steps": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
        },
    )
    plan_id = create.json()["plan_id"]

    # Start the plan, wait briefly for first step to become current
    await api_client.post(f"/api/task-plans/{plan_id}/start")
    await asyncio.sleep(0.05)

    detail = (await api_client.get(f"/api/task-plans/{plan_id}")).json()
    if detail["current_step_id"] is not None:
        resp = await api_client.post(
            f"/api/task-plans/{plan_id}/steps",
            json={
                "title": "Inserted step",
                "metadata": {"inserted_from_chat": True},
            },
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_add_step_terminal_plan_returns_400(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]
    await api_client.post(f"/api/task-plans/{plan_id}/cancel")

    resp = await api_client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={
            "title": "X",
        },
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /task-plans/{plan_id}/steps/{step_id}/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_step_status_running(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]

    add = await api_client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={"title": "S"},
    )
    step_id = add.json()["step_id"]

    resp = await api_client.post(
        f"/api/task-plans/{plan_id}/steps/{step_id}/status",
        json={"status": "running"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


@pytest.mark.asyncio
async def test_update_step_status_failed_with_error(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]

    add = await api_client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={"title": "S"},
    )
    step_id = add.json()["step_id"]

    resp = await api_client.post(
        f"/api/task-plans/{plan_id}/steps/{step_id}/status",
        json={"status": "failed", "error": "boom"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error"] == "boom"
    assert body["finished_at"] is not None


@pytest.mark.asyncio
async def test_update_step_invalid_status(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]

    add = await api_client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={"title": "S"},
    )
    step_id = add.json()["step_id"]

    resp = await api_client.post(
        f"/api/task-plans/{plan_id}/steps/{step_id}/status",
        json={"status": "bogus"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_step_404_plan(api_client: AsyncClient):
    resp = await api_client.post(
        "/api/task-plans/nonexistent/steps/x/status",
        json={"status": "running"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_step_404_step(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]

    resp = await api_client.post(
        f"/api/task-plans/{plan_id}/steps/nonexistent/status",
        json={"status": "running"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /task-plans/{plan_id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_plan(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={"session_id": "s1", "title": "P"},
    )
    plan_id = create.json()["plan_id"]

    # Add a step that should get cancelled
    add = await api_client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={"title": "S"},
    )
    step_id = add.json()["step_id"]

    resp = await api_client.post(f"/api/task-plans/{plan_id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["finished_at"] is not None
    # Step should be cancelled too
    step = [s for s in body["steps"] if s["step_id"] == step_id][0]
    assert step["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_plan_404(api_client: AsyncClient):
    resp = await api_client.post("/api/task-plans/nonexistent/cancel")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sse_receives_plan_created(api_client: AsyncClient, store):
    sub_id, queue = store.subscribe()
    try:
        await api_client.post(
            "/api/task-plans",
            json={
                "session_id": "s1",
                "title": "SSE test",
            },
        )
        event = queue.get_nowait()
        assert event.event_type == PlanEventType.PLAN_CREATED
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_sse_filters_by_session_id(store):
    sub_id, queue = store.subscribe()
    try:
        # Create a plan — broadcast includes session_id
        await store.create_plan(session_id="s1", title="Match")
        event = queue.get_nowait()
        assert event.data["session_id"] == "s1"
    finally:
        store.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_sse_cleanup_unsubscribe(api_client: AsyncClient, store):
    sub_id, queue = store.subscribe()
    store.unsubscribe(sub_id)
    # Create plan — queue should NOT receive it
    await store.create_plan(session_id="s1", title="After unsub")
    assert queue.empty()


# ---------------------------------------------------------------------------
# POST /task-plans/{plan_id}/start
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_plan_success(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "Startable",
            "steps": [{"title": "A"}, {"title": "B"}],
        },
    )
    plan_id = create.json()["plan_id"]

    resp = await api_client.post(f"/api/task-plans/{plan_id}/start")
    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_id"] == plan_id
    assert body["started"] is True

    # Wait for execution to finish
    for _ in range(50):
        await asyncio.sleep(0.02)
        detail = await api_client.get(f"/api/task-plans/{plan_id}")
        if detail.json()["status"] == "done":
            break

    detail = await api_client.get(f"/api/task-plans/{plan_id}")
    assert detail.json()["status"] == "done"


@pytest.mark.asyncio
async def test_start_plan_404(api_client: AsyncClient):
    resp = await api_client.post("/api/task-plans/nonexistent/start")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_running_plan_uses_executor(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "Cancel running",
            "steps": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
        },
    )
    plan_id = create.json()["plan_id"]

    # Start it
    await api_client.post(f"/api/task-plans/{plan_id}/start")
    await asyncio.sleep(0.02)

    # Cancel while running
    resp = await api_client.post(f"/api/task-plans/{plan_id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"


# ---------------------------------------------------------------------------
# POST /task-plans/{plan_id}/pause
# POST /task-plans/{plan_id}/resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_plan_success(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "Pausable",
            "steps": [{"title": "A"}, {"title": "B"}, {"title": "C"}],
        },
    )
    plan_id = create.json()["plan_id"]

    await api_client.post(f"/api/task-plans/{plan_id}/start")
    await asyncio.sleep(0.1)

    resp = await api_client.post(f"/api/task-plans/{plan_id}/pause")
    assert resp.status_code == 200
    assert resp.json()["status"] == "waiting_user"


@pytest.mark.asyncio
async def test_pause_plan_invalid_state_400(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "Draft",
        },
    )
    plan_id = create.json()["plan_id"]

    resp = await api_client.post(f"/api/task-plans/{plan_id}/pause")
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_resume_plan_success(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "Resumable",
            "steps": [{"title": "A"}, {"title": "B"}],
        },
    )
    plan_id = create.json()["plan_id"]

    await api_client.post(f"/api/task-plans/{plan_id}/start")
    await asyncio.sleep(0.1)
    await api_client.post(f"/api/task-plans/{plan_id}/pause")

    resp = await api_client.post(f"/api/task-plans/{plan_id}/resume")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"


@pytest.mark.asyncio
async def test_resume_plan_invalid_state_400(api_client: AsyncClient):
    create = await api_client.post(
        "/api/task-plans",
        json={
            "session_id": "s1",
            "title": "Draft",
        },
    )
    plan_id = create.json()["plan_id"]

    resp = await api_client.post(f"/api/task-plans/{plan_id}/resume")
    assert resp.status_code == 400
