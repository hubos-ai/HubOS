# -*- coding: utf-8 -*-
"""Unit tests for BaseChannel message filtering hooks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentscope_runtime.engine.schemas.agent_schemas import (
    ContentType,
    MessageType,
    RunStatus,
    TextContent,
)

from hubos.app.channels.base import BaseChannel


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
