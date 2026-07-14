# -*- coding: utf-8 -*-
"""Console APIs: push messages, chat, and file upload for chat."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from contextlib import suppress
from pathlib import Path
from typing import AsyncGenerator, Union

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile
from starlette.responses import StreamingResponse

from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest
from ..agent_context import get_agent_for_request

# SSE heartbeat interval (seconds).  Keeps the HTTP response alive during
# long-running LLM calls / tool invocations where no real events are produced.
# Chromium / Electron may stall a ReadableStream if no bytes arrive for ~30s.
_SSE_HEARTBEAT_INTERVAL = 15


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/console", tags=["console"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def _safe_filename(name: str) -> str:
    """Safe basename, alphanumeric/./-/_, max 200 chars."""
    base = Path(name).name if name else "file"
    return re.sub(r"[^\w.\-]", "_", base)[:200] or "file"


def _extract_session_and_payload(request_data: Union[AgentRequest, dict]):
    """Extract run_key (ChatSpec.id), session_id, and native payload.

    run_key must be ChatSpec.id (chat_id) so it matches list_chats/get_chat.
    """
    if isinstance(request_data, AgentRequest):
        channel_id = request_data.channel or "console"
        sender_id = request_data.user_id or "default"
        session_id = request_data.session_id or "default"
        content_parts = (
            list(request_data.input[0].content) if request_data.input else []
        )
    else:
        channel_id = request_data.get("channel", "console")
        sender_id = request_data.get("user_id", "default")
        session_id = request_data.get("session_id", "default")
        input_data = request_data.get("input", [])
        content_parts = []
        for content_part in input_data:
            if hasattr(content_part, "content"):
                content_parts.extend(list(content_part.content or []))
            elif isinstance(content_part, dict) and "content" in content_part:
                content_parts.extend(content_part["content"] or [])

    native_payload = {
        "channel_id": channel_id,
        "sender_id": sender_id,
        "content_parts": content_parts,
        "meta": {
            "session_id": session_id,
            "user_id": sender_id,
        },
    }
    # Forward biz_params (e.g. runtime_guidance, guided_from_run_id)
    if isinstance(request_data, dict):
        _biz = request_data.get("biz_params")
        if not isinstance(_biz, dict):
            _biz = {}
        # Some frontend callers flatten biz params into the root body. Keep
        # this tolerant so runtime_guidance still reaches TaskTracker.force_new.
        for _key in (
            "runtime_guidance",
            "guidance_text",
            "guided_from_run_id",
            "guidance_ack",
        ):
            if _key in request_data and _key not in _biz:
                _biz[_key] = request_data[_key]
    else:
        _biz = getattr(request_data, "biz_params", None)
    if _biz:
        native_payload["biz_params"] = _biz
    return native_payload


@router.post(
    "/chat",
    status_code=200,
    summary="Chat with console (streaming response)",
    description="Agent API Request Format. "
    "Use body.reconnect=true to attach to a running stream.",
)
async def post_console_chat(
    request_data: Union[AgentRequest, dict],
    request: Request,
) -> StreamingResponse:
    """Stream agent response. Run continues in background after disconnect.
    Stop via POST /console/chat/stop. Reconnect with body.reconnect=true.
    """
    workspace = await get_agent_for_request(request)
    console_channel = await workspace.channel_manager.get_channel("console")
    if console_channel is None:
        raise HTTPException(
            status_code=503,
            detail="Channel Console not found",
        )
    try:
        native_payload = _extract_session_and_payload(request_data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session_id = console_channel.resolve_session_id(
        sender_id=native_payload["sender_id"],
        channel_meta=native_payload["meta"],
    )
    name = "New Chat"
    if len(native_payload["content_parts"]) > 0:
        content = native_payload["content_parts"][0]
        if content:
            name = content.text[:10]
        else:
            name = "Media Message"
    chat = await workspace.chat_manager.get_or_create_chat(
        session_id,
        native_payload["sender_id"],
        native_payload["channel_id"],
        name=name,
    )
    tracker = workspace.task_tracker

    is_reconnect = False
    chat_run_id: str | None = None
    if isinstance(request_data, dict):
        is_reconnect = request_data.get("reconnect") is True

    if is_reconnect:
        queue = await tracker.attach(chat.id)
        if queue is None:
            return
    else:
        try:
            from ..run_control import (
                RunEntry,
                RunType,
                get_run_control_store,
                register_chat_cancel_handler,
                set_current_run_id,
            )

            biz_params = native_payload.get("biz_params")
            if not isinstance(biz_params, dict):
                biz_params = {}
            chat_run_id = await get_run_control_store().register(
                RunEntry(
                    run_id="",
                    run_type=RunType.CHAT,
                    session_id=session_id,
                    chat_id=chat.id,
                    workspace_id=workspace.agent_id,
                    guided_from_run_id=biz_params.get("guided_from_run_id"),
                    guidance_text=biz_params.get("guidance_text"),
                ),
            )
            register_chat_cancel_handler(
                tracker.request_stop,
                workspace_id=workspace.agent_id,
                chat_id=chat.id,
                run_id=chat_run_id,
            )
            # The producer task is created inside attach_or_start, so set the
            # context before calling it.  Child tool runs inherit this run id.
            set_current_run_id(chat_run_id)
        except Exception:  # noqa: BLE001
            logger.exception("Console RunControl registration failed")

        # If runtime_guidance, force_new ensures the old producer is cancelled
        # atomically inside attach_or_start (no race with buffer replay).
        is_guidance = bool(biz_params.get("runtime_guidance"))
        queue, is_new_run = await tracker.attach_or_start(
            chat.id,
            native_payload,
            console_channel.stream_one,
            force_new=is_guidance,
            reconnect=is_reconnect,
        )
        with suppress(Exception):
            from ..run_control import get_run_control_store, set_current_run_id

            if chat_run_id and not is_new_run:
                # attach_or_start attached to an existing producer; this
                # request should not show as a separate active run.
                await get_run_control_store().unregister(chat_run_id)
                chat_run_id = None
            set_current_run_id(None)

        if chat_run_id and is_new_run:

            async def _watch_chat_done(run_id: str, run_key: str) -> None:
                """Mirror TaskTracker completion into RunControl.

                The HTTP stream may disconnect before the producer completes,
                so the run status cannot be tied only to event_generator.
                """
                try:
                    while await tracker.get_status(run_key) == "running":
                        await asyncio.sleep(1)
                    from ..run_control import get_run_control_store

                    store = get_run_control_store()
                    entry = await store.get_run(run_id)
                    if entry is not None and entry.status not in {
                        "done",
                        "failed",
                        "cancelled",
                    }:
                        await store.update_status(run_id, "done")
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Console RunControl watcher failed",
                        exc_info=True,
                    )

            asyncio.create_task(_watch_chat_done(chat_run_id, chat.id))

    async def event_generator() -> AsyncGenerator[str, None]:
        """Yield SSE events from *stream_it*, injecting heartbeat comments
        when the backend is silent (LLM thinking, tool execution, etc.).

        SSE comments (``: ping\\n\\n``) are ignored by spec-compliant parsers
        but keep the TCP connection alive and prevent Chromium's
        ReadableStream from stalling.
        """
        # Hold iterator so finally can aclose(); guarantees stream_from_queue's
        # finally (detach_subscriber) on client abort / generator teardown.
        stream_it = tracker.stream_from_queue(queue, chat.id)

        # Heartbeat task: periodically push ``: ping`` SSE comments so the
        # client never sees a prolonged silence that could be mistaken for a
        # dead connection or trigger browser-level buffer stalls.
        hb_queue: asyncio.Queue[str] = asyncio.Queue()
        hb_stop = asyncio.Event()

        async def _heartbeat_loop() -> None:
            try:
                while not hb_stop.is_set():
                    try:
                        await asyncio.wait_for(
                            hb_stop.wait(),
                            timeout=_SSE_HEARTBEAT_INTERVAL,
                        )
                    except asyncio.TimeoutError:
                        await hb_queue.put(": ping\n\n")
            except asyncio.CancelledError:
                pass

        hb_task = asyncio.create_task(_heartbeat_loop())
        try:
            try:
                async for event_data in stream_it:
                    yield event_data
                    # Also drain any queued heartbeats so they don't pile up.
                    while not hb_queue.empty():
                        yield hb_queue.get_nowait()
            except Exception as e:
                logger.exception("Console chat stream error")
                yield f"data: {json.dumps({'error': str(e)})}\n\n"
            finally:
                hb_stop.set()
                hb_task.cancel()
                try:
                    await hb_task
                except asyncio.CancelledError:
                    pass
                # Drain any remaining heartbeats.
                while not hb_queue.empty():
                    yield hb_queue.get_nowait()
        finally:
            await stream_it.aclose()
            if chat_run_id:
                with suppress(Exception):
                    from ..run_control import get_run_control_store

                    store = get_run_control_store()
                    entry = await store.get_run(chat_run_id)
                    if entry is not None and entry.status not in {
                        "done",
                        "failed",
                        "cancelled",
                    }:
                        status = await tracker.get_status(chat.id)
                        await store.update_status(
                            chat_run_id,
                            "running" if status == "running" else "done",
                        )

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@router.post(
    "/chat/stop",
    status_code=200,
    summary="Stop running console chat",
)
async def post_console_chat_stop(
    request: Request,
    chat_id: str = Query(..., description="Chat id (ChatSpec.id) to stop"),
) -> dict:
    """Stop the running chat. Only stops when called."""
    workspace = await get_agent_for_request(request)
    stopped = await workspace.task_tracker.request_stop(chat_id)
    return {"stopped": stopped}


@router.post("/upload", response_model=dict, summary="Upload file for chat")
async def post_console_upload(
    request: Request,
    file: UploadFile = File(..., description="File to attach"),
) -> dict:
    """Save to console channel media_dir."""

    workspace = await get_agent_for_request(request)
    console_channel = await workspace.channel_manager.get_channel("console")
    if console_channel is None:
        raise HTTPException(
            status_code=503,
            detail="Channel Console not found",
        )
    media_dir = console_channel.media_dir
    media_dir.mkdir(parents=True, exist_ok=True)
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail="File too large (max "
            f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB)",
        )
    safe_name = _safe_filename(file.filename or "file")
    stored_name = f"{uuid.uuid4().hex}_{safe_name}"

    path = (media_dir / stored_name).resolve()
    path.write_bytes(data)
    return {
        "url": path,
        "file_name": safe_name,
        "size": len(data),
    }


@router.get("/push-messages")
async def get_push_messages(
    session_id: str | None = Query(None, description="Optional session id"),
):
    """
    Return pending push messages. Without session_id: recent messages
    (all sessions, last 60s), not consumed so every tab sees them.
    """
    from ..console_push_store import get_recent, take

    if session_id:
        messages = await take(session_id)
    else:
        messages = await get_recent()
    return {"messages": messages}
