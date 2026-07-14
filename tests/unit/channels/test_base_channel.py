# -*- coding: utf-8 -*-
"""Unit tests for BaseChannel message filtering hooks."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentscope_runtime.engine.schemas.agent_schemas import (
    ContentType,
    MessageType,
    RunStatus,
    TextContent,
)

from hubos.app.channels.base import BaseChannel
from hubos.app.channels.delivery_context import (
    DeliveryContext,
    get_current_delivery_context,
)
from hubos.app.runner.task_tracker import TaskTracker


async def _dummy_process(_request):
    if False:  # pragma: no cover
        yield None


class _DummyChannel(BaseChannel):
    channel = "dummy"

    def __init__(self):
        super().__init__(process=_dummy_process)
        self.sent_parts = []

    async def send_content_parts(self, to_handle, parts, meta=None) -> None:
        self.sent_parts.append((to_handle, parts, meta))

    async def send(self, to_handle: str, text: str, meta=None) -> None:
        self.sent_parts.append((to_handle, text, meta))

    def build_agent_request_from_native(self, native_payload):
        return SimpleNamespace(
            session_id=native_payload.get("session_id", ""),
            user_id=native_payload.get("user_id", ""),
            channel=native_payload.get("channel_id", self.channel),
            input=[],
            channel_meta=native_payload.get("meta") or {},
        )


class _ProgressDummyChannel(_DummyChannel):
    channel = "feishu"
    _PROGRESS_HEARTBEAT_SECONDS = 0.02
    _PROGRESS_INITIAL_SECONDS = 0.02
    _PROGRESS_INTERVAL_SECONDS = 0.05


def _event(content):
    return SimpleNamespace(
        object="message",
        status=RunStatus.Completed,
        type=MessageType.MESSAGE,
        content=content,
    )


@pytest.mark.asyncio
async def test_send_message_content_skips_hubos_status_event() -> None:
    channel = _DummyChannel()
    message = _event(
        [
            SimpleNamespace(
                type=ContentType.DATA,
                data={"kind": "hubos_status", "name": "Context understanding"},
            ),
        ],
    )

    await channel.send_message_content("u1", message, {})

    assert channel.sent_parts == []


@pytest.mark.asyncio
async def test_send_message_content_sends_normal_text_event() -> None:
    channel = _DummyChannel()
    message = _event([TextContent(type=ContentType.TEXT, text="hello")])

    await channel.send_message_content("u1", message, {})

    assert len(channel.sent_parts) == 1
    _to_handle, parts, _meta = channel.sent_parts[0]
    assert getattr(parts[0], "text", "") == "hello"


@pytest.mark.asyncio
async def test_send_event_skips_hubos_status_event() -> None:
    channel = _DummyChannel()
    message = _event(
        [
            SimpleNamespace(
                type=ContentType.DATA,
                data={"kind": "hubos_status", "name": "Knowledge injection"},
            ),
        ],
    )

    await channel.send_event(
        user_id="u1",
        session_id="s1",
        event=message,
        meta={},
    )

    assert channel.sent_parts == []


@pytest.mark.asyncio
async def test_send_message_content_skips_raw_dict_hubos_status_event() -> None:
    channel = _DummyChannel()
    message = _event(
        [
            {
                "type": "hubos_status",
                "name": "Experience matching",
                "status": "completed",
                "output": "matched",
            },
        ],
    )

    await channel.send_message_content("u1", message, {})

    assert channel.sent_parts == []


@pytest.mark.asyncio
async def test_run_process_loop_exposes_delivery_context_to_tools() -> None:
    """Channel process loop should install direct-send delivery context."""
    seen = {}

    async def _process(request):
        ctx = get_current_delivery_context()
        seen["channel"] = None if ctx is None else ctx.channel
        seen["to_handle"] = None if ctx is None else ctx.to_handle
        seen["meta"] = None if ctx is None else dict(ctx.meta)
        if False:  # pragma: no cover
            yield None

    channel = _DummyChannel()
    channel._process = _process  # pylint: disable=protected-access
    request = SimpleNamespace(session_id="s1", user_id="u1")
    send_meta = {"user_id": "u1", "session_id": "s1"}

    await channel._run_process_loop(
        request,
        "u1",
        send_meta,
    )  # pylint: disable=protected-access

    assert seen["channel"] == "dummy"
    assert seen["to_handle"] == "u1"
    assert seen["meta"]["session_id"] == "s1"


class _DummyChatManager:
    async def get_or_create_chat(self, *_args, **_kwargs):
        return SimpleNamespace(id="chat-1")


class _DummyWorkspace:
    agent_id = "ws-dummy"
    workspace_id = "ws-dummy"

    def __init__(self):
        self.chat_manager = _DummyChatManager()
        self.task_tracker = TaskTracker()


class _StopChatManager:
    async def get_chat_id_by_session(self, *_args, **_kwargs):
        return "chat-stop"


class _StopTaskTracker:
    def __init__(self):
        self.stopped = []

    async def request_stop(self, chat_id):
        self.stopped.append(chat_id)
        return True


class _StopWorkspace:
    agent_id = "feishu_ou_stop"
    workspace_id = "feishu_ou_stop"
    channel_manager = None

    def __init__(self):
        self.chat_manager = _StopChatManager()
        self.task_tracker = _StopTaskTracker()


class _CommandRegistry:
    def is_control_command(self, _query):
        return True


class _SharedChannelManager:
    def __init__(self):
        self.cleared = []

    async def clear_queue(self, channel_id, session_id, priority_level):
        self.cleared.append((channel_id, session_id, priority_level))
        return 1


@pytest.mark.asyncio
async def test_consume_with_tracker_drains_new_run_and_sends_message() -> None:
    """New TaskTracker runs must be drained, not treated as already running."""

    async def _process(_request):
        yield _event(
            [TextContent(type=ContentType.TEXT, text="tracked hello")],
        )

    channel = _DummyChannel()
    channel._process = _process  # pylint: disable=protected-access
    workspace = _DummyWorkspace()
    channel.set_workspace(workspace)
    request = SimpleNamespace(
        session_id="s-tracked",
        user_id="u1",
        channel="dummy",
        input=[],
    )

    await channel._consume_with_tracker(
        request,
        request,
    )  # pylint: disable=protected-access

    assert channel.sent_parts
    _to_handle, parts, _meta = channel.sent_parts[0]
    assert getattr(parts[0], "text", "") == "tracked hello"


@pytest.mark.asyncio
async def test_quiet_external_task_gets_progress_and_watchdog_heartbeat() -> None:
    """A busy main-agent run must not look idle while it emits no SSE."""

    async def _quiet_process(_request):
        await asyncio.sleep(0.14)
        yield _event([TextContent(type=ContentType.TEXT, text="finished")])

    channel = _ProgressDummyChannel()
    channel._process = _quiet_process  # pylint: disable=protected-access
    workspace = _DummyWorkspace()
    workspace.task_tracker._MAX_RUN_SECONDS = (
        0.05  # pylint: disable=protected-access
    )
    channel.set_workspace(workspace)
    request = SimpleNamespace(
        session_id="s-progress",
        user_id="u-progress",
        channel="feishu",
        input=[],
        channel_meta={},
    )

    await channel._consume_with_tracker(
        request,
        request,
    )  # pylint: disable=protected-access

    texts = [
        getattr(parts[0], "text", "")
        for _target, parts, _meta in channel.sent_parts
        if isinstance(parts, list) and parts
    ]
    progress = [text for text in texts if "我还在处理这项任务" in text]
    assert len(progress) >= 2
    assert "finished" in texts


@pytest.mark.asyncio
async def test_delivery_context_deduplicates_concurrent_progress() -> None:
    """Generic and specialized progress reporters must not double-send."""
    sent = []

    async def _send_parts(to_handle, parts, meta):
        sent.append((to_handle, parts, meta))

    ctx = DeliveryContext(
        channel="feishu",
        to_handle="u-progress",
        meta={},
        send_parts=_send_parts,
    )
    first = [TextContent(type=ContentType.TEXT, text="generic")]
    second = [TextContent(type=ContentType.TEXT, text="specialized")]

    covered = await asyncio.gather(
        ctx.send_progress_parts(first, min_interval_seconds=120.0),
        ctx.send_progress_parts(second, min_interval_seconds=120.0),
    )

    assert covered == [True, True]
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_stream_with_tracker_exposes_delivery_context_to_tools() -> None:
    """TaskTracker stream path should also install direct-send context."""
    seen = {}

    async def _process(_request):
        ctx = get_current_delivery_context()
        seen["channel"] = None if ctx is None else ctx.channel
        seen["to_handle"] = None if ctx is None else ctx.to_handle
        seen["meta"] = None if ctx is None else dict(ctx.meta)
        if False:  # pragma: no cover
            yield None

    channel = _DummyChannel()
    channel._process = _process  # pylint: disable=protected-access
    request = SimpleNamespace(
        session_id="s1",
        user_id="u1",
        channel="dummy",
        input=[],
        channel_meta={"source": "test"},
    )

    async for _ in channel._stream_with_tracker(
        request,
    ):  # pylint: disable=protected-access
        pass

    assert seen["channel"] == "dummy"
    assert seen["to_handle"] == "u1"
    assert seen["meta"]["source"] == "test"


@pytest.mark.asyncio
async def test_stop_control_command_uses_owner_workspace_and_shared_channel() -> None:
    """Feishu-style /stop should use owner workspace plus shared channel queue."""
    channel = _DummyChannel()
    owner_ws = _StopWorkspace()
    shared_manager = _SharedChannelManager()
    channel.set_workspace(
        SimpleNamespace(agent_id="default"),
        _CommandRegistry(),
    )
    channel._channel_manager = (
        shared_manager  # pylint: disable=protected-access
    )

    async def _resolve_owner(_request, _payload):
        return owner_ws

    channel._owner_workspace_resolver = (
        _resolve_owner  # pylint: disable=protected-access
    )
    payload = {
        "channel_id": "dummy",
        "session_id": "s-stop",
        "user_id": "ou_stop",
        "content_parts": [TextContent(type=ContentType.TEXT, text="/stop")],
        "meta": {"source": "feishu"},
    }

    await channel._consume_one_request(
        payload,
    )  # pylint: disable=protected-access

    assert owner_ws.task_tracker.stopped == ["chat-stop"]
    assert shared_manager.cleared == [("dummy", "s-stop", 20)]
    assert channel.sent_parts
    _to_handle, parts, _meta = channel.sent_parts[-1]
    assert "Task Stopped" in getattr(parts[0], "text", "")
