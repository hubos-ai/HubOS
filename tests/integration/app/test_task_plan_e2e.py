# -*- coding: utf-8 -*-
"""End-to-end smoke tests for the Task Plan lifecycle.

Validates the full chain: API → Store → Executor → TaskMonitor.
No real agents, no LLM, no external network — pure in-process integration.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hubos.app.routers.task_monitor import router as task_monitor_router
from hubos.app.routers.task_plan import router as task_plan_router
from hubos.app.task_plan import PlanStatus, get_plan_store
from hubos.app.task_monitor import TaskStatus

# ---------------------------------------------------------------------------
# App setup — both routers mounted under /api
# ---------------------------------------------------------------------------

app = FastAPI()
app.include_router(task_plan_router, prefix="/api")
app.include_router(task_monitor_router, prefix="/api")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_all_stores():
    """Reset plan store, executor, and monitor store between tests."""
    import hubos.app.task_plan as _plan_mod
    import hubos.app.task_plan_executor as _exec_mod
    import hubos.app.task_monitor_helpers as _mon_mod

    old_plan = _plan_mod._store
    old_exec = _exec_mod._executor
    old_mon = _mon_mod._store
    old_handlers = dict(_mon_mod._cancel_handlers)
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
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_plan(client: AsyncClient, plan_id: str) -> dict:
    resp = await client.get(f"/api/task-plans/{plan_id}")
    assert resp.status_code == 200
    return resp.json()


async def wait_for_plan_status(
    client: AsyncClient,
    plan_id: str,
    target: str,
    timeout: float = 3.0,
) -> dict:
    """Poll plan detail until status matches target or timeout."""
    interval = 0.05
    elapsed = 0.0
    while elapsed < timeout:
        plan = await _get_plan(client, plan_id)
        if plan["status"] == target:
            return plan
        await asyncio.sleep(interval)
        elapsed += interval
    # Final attempt — return whatever we have (test will assert)
    return await _get_plan(client, plan_id)


# ---------------------------------------------------------------------------
# Test 1: Basic lifecycle — create → insert → start → done → monitor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_plan_basic_lifecycle_e2e(client: AsyncClient):
    # Create draft plan with 2 agent-less steps
    resp = await client.post(
        "/api/task-plans",
        json={
            "session_id": "e2e-s1",
            "title": "E2E basic lifecycle",
            "steps": [
                {"title": "Step A"},
                {"title": "Step B"},
            ],
        },
    )
    assert resp.status_code == 200
    plan = resp.json()
    plan_id = plan["plan_id"]
    assert plan["status"] == "draft"
    assert len(plan["steps"]) == 2

    # Confirm it appears in list
    resp = await client.get("/api/task-plans", params={"session_id": "e2e-s1"})
    assert resp.status_code == 200
    assert resp.json()["count"] >= 1

    # Insert a chat-inserted step
    resp = await client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={
            "title": "Inserted step",
            "metadata": {"inserted_from_chat": True},
        },
    )
    assert resp.status_code == 200
    inserted = resp.json()
    assert inserted["status"] == "pending"

    # Start execution
    resp = await client.post(f"/api/task-plans/{plan_id}/start")
    assert resp.status_code == 200
    assert resp.json()["started"] is True

    # Wait for done
    plan = await wait_for_plan_status(client, plan_id, "done")
    assert plan["status"] == "done"
    assert plan["current_step_id"] is None

    # All steps done
    steps = sorted(plan["steps"], key=lambda s: s["order"])
    assert len(steps) == 3
    for s in steps:
        assert s["status"] == "done"

    # Orders are sequential
    orders = [s["order"] for s in steps]
    assert orders == sorted(orders)

    # TaskMonitor: find the corresponding monitor task
    resp = await client.get(
        "/api/task-monitor/tasks",
        params={"tool_name": "task_plan_executor"},
    )
    assert resp.status_code == 200
    mon_tasks = resp.json()["tasks"]
    matching = [
        t for t in mon_tasks if t.get("metadata", {}).get("plan_id") == plan_id
    ]
    assert len(matching) >= 1
    assert matching[0]["status"] == "done"


# ---------------------------------------------------------------------------
# Test 2: Pause → Resume → Done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_plan_pause_resume_e2e(client: AsyncClient):
    resp = await client.post(
        "/api/task-plans",
        json={
            "session_id": "e2e-s2",
            "title": "Pause/Resume E2E",
            "steps": [
                {"title": "A"},
                {"title": "B"},
                {"title": "C"},
                {"title": "D"},
                {"title": "E"},
            ],
        },
    )
    plan_id = resp.json()["plan_id"]

    # Start
    resp = await client.post(f"/api/task-plans/{plan_id}/start")
    assert resp.json()["started"] is True
    await asyncio.sleep(0.08)

    # Pause
    resp = await client.post(f"/api/task-plans/{plan_id}/pause")
    assert resp.status_code == 200
    plan = resp.json()
    assert plan["status"] == "waiting_user"

    # Resume
    resp = await client.post(f"/api/task-plans/{plan_id}/resume")
    assert resp.status_code == 200

    # Wait for done
    plan = await wait_for_plan_status(client, plan_id, "done")
    assert plan["status"] == "done"


# ---------------------------------------------------------------------------
# Test 3: Cancel during execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_plan_cancel_e2e(client: AsyncClient):
    resp = await client.post(
        "/api/task-plans",
        json={
            "session_id": "e2e-s3",
            "title": "Cancel E2E",
            "steps": [
                {"title": "A"},
                {"title": "B"},
                {"title": "C"},
                {"title": "D"},
                {"title": "E"},
            ],
        },
    )
    plan_id = resp.json()["plan_id"]

    # Start
    resp = await client.post(f"/api/task-plans/{plan_id}/start")
    assert resp.json()["started"] is True
    await asyncio.sleep(0.05)

    # Cancel
    resp = await client.post(f"/api/task-plans/{plan_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"

    # Verify final state
    plan = await _get_plan(client, plan_id)
    assert plan["status"] == "cancelled"

    # TaskMonitor: find and check cancelled
    resp = await client.get(
        "/api/task-monitor/tasks",
        params={"tool_name": "task_plan_executor"},
    )
    matching = [
        t
        for t in resp.json()["tasks"]
        if t.get("metadata", {}).get("plan_id") == plan_id
    ]
    assert len(matching) >= 1
    assert matching[0]["status"] in ("cancelled", "failed")


# ---------------------------------------------------------------------------
# Test 4: Insert step while paused → resume → done
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_plan_insert_while_paused_e2e(client: AsyncClient):
    resp = await client.post(
        "/api/task-plans",
        json={
            "session_id": "e2e-s4",
            "title": "Insert while paused E2E",
            "steps": [
                {"title": "A"},
                {"title": "B"},
                {"title": "C"},
                {"title": "D"},
                {"title": "E"},
            ],
        },
    )
    plan_id = resp.json()["plan_id"]

    # Start and pause
    await client.post(f"/api/task-plans/{plan_id}/start")
    await asyncio.sleep(0.08)
    resp = await client.post(f"/api/task-plans/{plan_id}/pause")
    assert resp.status_code == 200

    # Insert step while paused
    resp = await client.post(
        f"/api/task-plans/{plan_id}/steps",
        json={
            "title": "Inserted while paused",
            "metadata": {"inserted_from_chat": True},
        },
    )
    assert resp.status_code == 200

    # Resume and wait for done
    await client.post(f"/api/task-plans/{plan_id}/resume")
    plan = await wait_for_plan_status(client, plan_id, "done", timeout=4.0)
    assert plan["status"] == "done"

    # Verify the inserted step is present and done
    titles = [s["title"] for s in plan["steps"]]
    assert "Inserted while paused" in titles
    for s in plan["steps"]:
        assert s["status"] == "done"


# ---------------------------------------------------------------------------
# Test 5: High-risk plan requires confirmation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_high_risk_plan_requires_confirmation_e2e(client: AsyncClient):
    resp = await client.post(
        "/api/task-plans",
        json={
            "session_id": "e2e-s5",
            "title": "Deploy to production",
            "steps": [{"title": "deploy"}],
            "metadata": {
                "requires_confirmation": True,
                "risk_level": "high",
                "risk_reasons": ["test"],
            },
        },
    )
    plan_id = resp.json()["plan_id"]

    # Start should gate to waiting_user
    resp = await client.post(f"/api/task-plans/{plan_id}/start")
    assert resp.status_code == 200
    body = resp.json()
    # Either started=False (gated by executor) or the plan is now waiting_user
    plan = await _get_plan(client, plan_id)
    assert plan["status"] == "waiting_user"

    # Resume acts as confirmation
    resp = await client.post(f"/api/task-plans/{plan_id}/resume")
    assert resp.status_code == 200

    # Wait for done
    plan = await wait_for_plan_status(client, plan_id, "done")
    assert plan["status"] == "done"
