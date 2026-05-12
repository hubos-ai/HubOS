# -*- coding: utf-8 -*-
"""Task Plan API — create, list, detail, cancel, step management, and SSE stream.

Routes are mounted at ``/task-plans`` by the router aggregator.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ...app.task_plan import (
    _BroadcastEvent,
    PlanEventType,
    PlanStatus,
    PlanStepStatus,
    TaskPlan,
    TaskPlanStep,
    get_plan_store,
)
from ...app.task_plan_executor import get_plan_executor

router = APIRouter(prefix="/task-plans", tags=["task-plans"])

_SSE_HEARTBEAT_INTERVAL = 15


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateStepInput(BaseModel):
    title: str
    description: str = ""
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    depends_on: List[str] = []
    metadata: Optional[Dict[str, Any]] = None
    after_step_id: Optional[str] = None


class CreatePlanInput(BaseModel):
    session_id: str
    title: str
    steps: List[CreateStepInput] = []
    metadata: Optional[Dict[str, Any]] = None


class UpdateStepStatusInput(BaseModel):
    status: str
    error: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _serialise_step(step: TaskPlanStep) -> Dict[str, Any]:
    return {
        "step_id": step.step_id,
        "title": step.title,
        "description": step.description,
        "status": step.status.value
        if isinstance(step.status, PlanStepStatus)
        else step.status,
        "order": step.order,
        "agent_id": step.agent_id,
        "tool_name": step.tool_name,
        "depends_on": step.depends_on,
        "metadata": step.metadata,
        "created_at": step.created_at,
        "updated_at": step.updated_at,
        "finished_at": step.finished_at,
        "error": step.error,
    }


def _serialise_plan(plan: TaskPlan) -> Dict[str, Any]:
    sorted_steps = sorted(plan.steps, key=lambda s: s.order)
    return {
        "plan_id": plan.plan_id,
        "session_id": plan.session_id,
        "title": plan.title,
        "status": plan.status.value
        if isinstance(plan.status, PlanStatus)
        else plan.status,
        "steps": [_serialise_step(s) for s in sorted_steps],
        "current_step_id": plan.current_step_id,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
        "finished_at": plan.finished_at,
        "metadata": plan.metadata,
    }


def _serialise_broadcast(event: _BroadcastEvent) -> Dict[str, Any]:
    return {
        "type": event.event_type.value
        if isinstance(event.event_type, PlanEventType)
        else event.event_type,
        "plan_id": event.plan_id,
        "data": event.data,
        "timestamp": event.timestamp,
    }


def _validate_plan_status(value: str) -> PlanStatus:
    try:
        return PlanStatus(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{value}'. Must be one of: {[s.value for s in PlanStatus]}",
        )


def _validate_step_status(value: str) -> PlanStepStatus:
    try:
        return PlanStepStatus(value)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{value}'. Must be one of: {[s.value for s in PlanStepStatus]}",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", summary="List plans")
async def list_plans(
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[str] = Query(None),
    session_id: Optional[str] = Query(None),
) -> dict:
    store = get_plan_store()
    status_enum = _validate_plan_status(status) if status else None
    plans = await store.list_plans(
        limit=limit,
        status=status_enum,
        session_id=session_id,
    )
    return {
        "plans": [_serialise_plan(p) for p in plans],
        "count": len(plans),
    }


@router.get("/stream", summary="SSE real-time plan events")
async def stream_events(
    session_id: Optional[str] = Query(None),
) -> StreamingResponse:
    store = get_plan_store()
    sub_id, queue = store.subscribe()

    async def _event_generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        timeout=_SSE_HEARTBEAT_INTERVAL,
                    )
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue

                if session_id is not None:
                    event_sid = event.data.get("session_id")
                    if event_sid and event_sid != session_id:
                        continue

                payload = _serialise_broadcast(event)
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            store.unsubscribe(sub_id)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{plan_id}", summary="Get plan detail")
async def get_plan(plan_id: str) -> dict:
    store = get_plan_store()
    plan = await store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan not found: {plan_id}",
        )
    return _serialise_plan(plan)


@router.post("", summary="Create plan")
async def create_plan(body: CreatePlanInput) -> dict:
    store = get_plan_store()
    steps_dicts = [s.model_dump() for s in body.steps]
    plan = await store.create_plan(
        session_id=body.session_id,
        title=body.title,
        steps=steps_dicts if steps_dicts else None,
        metadata=body.metadata,
    )
    return _serialise_plan(plan)


@router.post("/{plan_id}/start", summary="Start plan execution")
async def start_plan(plan_id: str) -> dict:
    executor = get_plan_executor()
    try:
        started = await executor.start_plan(plan_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Plan not found: {plan_id}",
        )
    return {"plan_id": plan_id, "started": started}


@router.post("/{plan_id}/cancel", summary="Cancel plan")
async def cancel_plan(plan_id: str) -> dict:
    executor = get_plan_executor()
    store = get_plan_store()

    # If the executor is running this plan, use executor cancel
    if executor.is_running(plan_id):
        await executor.cancel_plan(plan_id)
        plan = await store.get_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=404,
                detail=f"Plan not found: {plan_id}",
            )
        return _serialise_plan(plan)

    # Otherwise just cancel in store (draft / already finished)
    try:
        plan = await store.cancel_plan(plan_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Plan not found: {plan_id}",
        )
    return _serialise_plan(plan)


@router.post("/{plan_id}/pause", summary="Pause running plan")
async def pause_plan(plan_id: str) -> dict:
    executor = get_plan_executor()
    store = get_plan_store()

    if executor.is_running(plan_id):
        ok = await executor.pause_plan(plan_id)
        if not ok:
            raise HTTPException(status_code=400, detail="Cannot pause plan")
    else:
        try:
            await store.pause_plan(plan_id)
        except KeyError:
            raise HTTPException(
                status_code=404,
                detail=f"Plan not found: {plan_id}",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    plan = await store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan not found: {plan_id}",
        )
    return _serialise_plan(plan)


@router.post("/{plan_id}/resume", summary="Resume paused plan")
async def resume_plan(plan_id: str) -> dict:
    executor = get_plan_executor()
    store = get_plan_store()

    ok = await executor.resume_plan(plan_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Cannot resume plan")

    plan = await store.get_plan(plan_id)
    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"Plan not found: {plan_id}",
        )
    return _serialise_plan(plan)


@router.post("/{plan_id}/steps", summary="Add step to plan")
async def add_step(plan_id: str, body: CreateStepInput) -> dict:
    store = get_plan_store()

    title = body.title
    description = body.description
    agent_id = body.agent_id
    metadata = body.metadata
    after_step_id = body.after_step_id
    is_chat_insert = metadata and metadata.get("inserted_from_chat")

    if is_chat_insert or after_step_id is None:
        # Need plan info for auto-insert and/or agent routing
        plan = await store.get_plan(plan_id)
        if plan is None:
            raise HTTPException(
                status_code=404,
                detail=f"Plan not found: {plan_id}",
            )

        if is_chat_insert:
            # Auto-assign after_step_id from current_step
            if after_step_id is None and plan.current_step_id:
                after_step_id = plan.current_step_id

            # Auto-assign agent_id
            if not agent_id:
                from ..task_plan_autogen import build_inserted_step

                original_text = ""
                if plan.metadata:
                    original_text = plan.metadata.get("original_user_text", "")
                auto = build_inserted_step(
                    body.title,
                    plan_title=plan.title,
                    original_user_text=original_text,
                )
                title = auto["title"]
                description = auto["description"]
                agent_id = auto.get("agent_id")
                metadata = auto.get("metadata", metadata)

    try:
        step = await store.add_step(
            plan_id,
            title=title,
            description=description,
            agent_id=agent_id,
            tool_name=body.tool_name,
            depends_on=body.depends_on or None,
            metadata=metadata,
            after_step_id=after_step_id,
        )
    except KeyError as exc:
        msg = str(exc)
        if "Plan" in msg:
            raise HTTPException(
                status_code=404,
                detail=f"Plan not found: {plan_id}",
            )
        raise HTTPException(status_code=404, detail=msg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _serialise_step(step)


@router.post("/{plan_id}/steps/{step_id}/status", summary="Update step status")
async def update_step_status(
    plan_id: str,
    step_id: str,
    body: UpdateStepStatusInput,
) -> dict:
    store = get_plan_store()
    status_enum = _validate_step_status(body.status)
    try:
        step = await store.update_step(
            plan_id,
            step_id,
            status=status_enum,
            error=body.error,
            metadata=body.metadata,
        )
    except KeyError as exc:
        key = str(exc)
        if "Plan" in key:
            raise HTTPException(
                status_code=404,
                detail=f"Plan not found: {plan_id}",
            )
        raise HTTPException(
            status_code=404,
            detail=f"Step not found: {step_id}",
        )
    return _serialise_step(step)
