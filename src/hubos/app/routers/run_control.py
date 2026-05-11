# -*- coding: utf-8 -*-
"""Run Control API — unified list, detail, cancel, guidance, and tree.

Routes are mounted at ``/run-control`` by the router aggregator.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ...app.run_control import RunControlStore, get_run_control_store

router = APIRouter(prefix="/run-control", tags=["run-control"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GuidanceInput(BaseModel):
    text: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _serialise(entry) -> Dict[str, Any]:
    from ...app.run_control import _serialise_entry
    return _serialise_entry(entry)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/runs", summary="List runs for a session")
async def list_runs(
    session_id: Optional[str] = Query(
        None,
        description="Optional session ID. Omit to list runs across sessions.",
    ),
    active_only: bool = Query(True, description="Only show active (non-terminal) runs"),
) -> dict:
    store = get_run_control_store()
    runs = await store.list_runs(session_id, active_only=active_only)
    return {
        "runs": [_serialise(r) for r in runs],
        "count": len(runs),
    }


@router.get("/runs/{run_id}", summary="Get run detail")
async def get_run(run_id: str) -> dict:
    store = get_run_control_store()
    entry = await store.get_run(run_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return _serialise(entry)


@router.get("/runs/{run_id}/tree", summary="Get run tree (run + all descendants)")
async def get_run_tree(run_id: str) -> dict:
    store = get_run_control_store()
    tree = await store.get_run_tree(run_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
    return {
        "runs": [_serialise(r) for r in tree],
        "count": len(tree),
    }


@router.post("/runs/{run_id}/cancel", summary="Cancel a specific run (and its children)")
async def cancel_run(run_id: str) -> dict:
    store = get_run_control_store()
    ok = await store.cancel_run(run_id)
    if not ok:
        entry = await store.get_run(run_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        raise HTTPException(
            status_code=400,
            detail=f"Run {run_id} is already in terminal state: {entry.status}",
        )
    return {"run_id": run_id, "cancelled": True}


@router.post("/runs/{run_id}/guidance", summary="Send guidance to a running run")
async def guidance_run(run_id: str, body: GuidanceInput) -> dict:
    store = get_run_control_store()
    result = await store.request_guidance(run_id, body.text)
    if result is None:
        entry = await store.get_run(run_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"Run not found: {run_id}")
        raise HTTPException(
            status_code=400,
            detail=f"Run {run_id} is already in terminal state: {entry.status}",
        )
    return {
        "run_id": run_id,
        "cancelled": True,
        **result,
    }


@router.get("/sessions/{session_id}/active", summary="Get active runs for a session")
async def get_active_runs(session_id: str) -> dict:
    store = get_run_control_store()
    runs = await store.list_runs(session_id, active_only=True)
    return {
        "runs": [_serialise(r) for r in runs],
        "count": len(runs),
    }


@router.post("/sessions/{session_id}/cancel-all", summary="Cancel all active runs for a session")
async def cancel_all(session_id: str) -> dict:
    store = get_run_control_store()
    cancelled = await store.cancel_all(session_id)
    return {
        "session_id": session_id,
        "cancelled_count": len(cancelled),
        "cancelled_run_ids": cancelled,
    }
