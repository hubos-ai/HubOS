# -*- coding: utf-8 -*-
"""Tests for Run Control API endpoints (inlined FastAPI app)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Query
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from hubos.app.run_control import (
    RunControlStore,
    RunEntry,
    RunType,
    _serialise_entry,
    get_run_control_store,
    register_chat_cancel_handler,
)


# ---------------------------------------------------------------------------
# Inline router (avoids importing routers/__init__.py)
# ---------------------------------------------------------------------------


class GuidanceInput(BaseModel):
    text: str


def _create_test_app() -> FastAPI:
    _app = FastAPI()
    _store = get_run_control_store

    @_app.get("/run-control/runs")
    async def list_runs(session_id: str = Query(...), active_only: bool = Query(True)):
        s = _store()
        runs = await s.list_runs(session_id, active_only=active_only)
        return {"runs": [_serialise_entry(r) for r in runs], "count": len(runs)}

    @_app.get("/run-control/runs/{run_id}")
    async def get_run(run_id: str):
        s = _store()
        entry = await s.get_run(run_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="Not found")
        return _serialise_entry(entry)

    @_app.get("/run-control/runs/{run_id}/tree")
    async def get_run_tree(run_id: str):
        s = _store()
        tree = await s.get_run_tree(run_id)
        if not tree:
            raise HTTPException(status_code=404, detail="Not found")
        return {"runs": [_serialise_entry(r) for r in tree], "count": len(tree)}

    @_app.post("/run-control/runs/{run_id}/cancel")
    async def cancel_run(run_id: str):
        s = _store()
        ok = await s.cancel_run(run_id)
        if not ok:
            entry = await s.get_run(run_id)
            if entry is None:
                raise HTTPException(status_code=404)
            raise HTTPException(status_code=400, detail="terminal")
        return {"run_id": run_id, "cancelled": True}

    @_app.post("/run-control/runs/{run_id}/guidance")
    async def guidance_run(run_id: str, body: GuidanceInput):
        s = _store()
        ack = await s.request_guidance(run_id, body.text)
        if ack is None:
            entry = await s.get_run(run_id)
            if entry is None:
                raise HTTPException(status_code=404)
            raise HTTPException(status_code=400, detail="terminal")
        return {"run_id": run_id, "guidance_ack": ack, "guidance_text": body.text, "cancelled": True}

    @_app.get("/run-control/sessions/{session_id}/active")
    async def get_active(session_id: str):
        s = _store()
        runs = await s.list_runs(session_id, active_only=True)
        return {"runs": [_serialise_entry(r) for r in runs], "count": len(runs)}

    @_app.post("/run-control/sessions/{session_id}/cancel-all")
    async def cancel_all(session_id: str):
        s = _store()
        cancelled = await s.cancel_all(session_id)
        return {"session_id": session_id, "cancelled_count": len(cancelled), "cancelled_run_ids": cancelled}

    return _app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset():
    import hubos.app.run_control as _mod
    old = _mod._store
    _mod._store = None
    _mod._CHAT_CANCEL_HANDLER = None
    yield
    _mod._store = old


@pytest.fixture
def client():
    return AsyncClient(transport=ASGITransport(app=_create_test_app()), base_url="http://test")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_empty(client):
    resp = await client.get("/run-control/runs?session_id=s1")
    assert resp.status_code == 200
    assert resp.json()["count"] == 0


@pytest.mark.asyncio
async def test_list_runs_with_entries(client):
    store = get_run_control_store()
    await store.register(RunEntry(run_id="r1", run_type=RunType.SPAWN, session_id="s1"))
    resp = await client.get("/run-control/runs?session_id=s1")
    assert resp.json()["count"] == 1


@pytest.mark.asyncio
async def test_get_run_detail(client):
    store = get_run_control_store()
    await store.register(RunEntry(run_id="r1", run_type=RunType.PLAN, session_id="s1", plan_id="p1"))
    resp = await client.get("/run-control/runs/r1")
    assert resp.json()["plan_id"] == "p1"


@pytest.mark.asyncio
async def test_get_run_not_found(client):
    assert (await client.get("/run-control/runs/nope")).status_code == 404


@pytest.mark.asyncio
async def test_get_run_tree(client):
    store = get_run_control_store()
    r1 = await store.register(RunEntry(run_id="r1", run_type=RunType.CHAT, session_id="s1", chat_id="c1"))
    await store.register(RunEntry(run_id="r2", run_type=RunType.SPAWN, session_id="s1", monitor_task_id="m1", parent_run_id=r1))
    resp = await client.get(f"/run-control/runs/{r1}/tree")
    data = resp.json()
    assert data["count"] == 2


@pytest.mark.asyncio
async def test_cancel_run(client):
    store = get_run_control_store()
    await store.register(RunEntry(run_id="r1", run_type=RunType.SPAWN, session_id="s1", monitor_task_id="mon-1"))
    with patch("hubos.app.task_monitor_helpers.request_cancel_task", new_callable=AsyncMock, return_value=True):
        resp = await client.post("/run-control/runs/r1/cancel")
        assert resp.json()["cancelled"] is True


@pytest.mark.asyncio
async def test_cancel_run_not_found(client):
    assert (await client.post("/run-control/runs/nope/cancel")).status_code == 404


@pytest.mark.asyncio
async def test_guidance(client):
    store = get_run_control_store()
    register_chat_cancel_handler(AsyncMock(return_value=True))
    await store.register(RunEntry(run_id="r1", run_type=RunType.CHAT, session_id="s1", chat_id="c1"))
    resp = await client.post("/run-control/runs/r1/guidance", json={"text": "switch to plan B"})
    data = resp.json()
    assert data["guidance_text"] == "switch to plan B"
    assert data["cancelled"] is True
    assert "guidance_ack" in data


@pytest.mark.asyncio
async def test_guidance_terminal(client):
    store = get_run_control_store()
    rid = await store.register(RunEntry(run_id="r1", run_type=RunType.CHAT, session_id="s1", chat_id="c1"))
    await store.update_status(rid, "done")
    resp = await client.post("/run-control/runs/r1/guidance", json={"text": "test"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_active(client):
    store = get_run_control_store()
    await store.register(RunEntry(run_id="r1", run_type=RunType.SPAWN, session_id="s1"))
    resp = await client.get("/run-control/sessions/s1/active")
    assert resp.json()["count"] == 1


@pytest.mark.asyncio
async def test_cancel_all(client):
    store = get_run_control_store()
    await store.register(RunEntry(run_id="r1", run_type=RunType.SPAWN, session_id="s1", monitor_task_id="m1"))
    with patch("hubos.app.task_monitor_helpers.request_cancel_task", new_callable=AsyncMock, return_value=True):
        resp = await client.post("/run-control/sessions/s1/cancel-all")
        assert resp.json()["cancelled_count"] == 1
