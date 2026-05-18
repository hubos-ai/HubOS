# -*- coding: utf-8 -*-
"""Unit tests for channel message renderer."""
from __future__ import annotations

from types import SimpleNamespace

from agentscope_runtime.engine.schemas.agent_schemas import ContentType
from agentscope_runtime.engine.schemas.agent_schemas import MessageType

from hubos.app.channels.renderer import MessageRenderer, RenderStyle


def _message(msg_type, content):
    return SimpleNamespace(type=msg_type, content=content)


def test_message_renderer_renders_dict_text_block() -> None:
    renderer = MessageRenderer()
    message = _message(
        MessageType.MESSAGE,
        [{"type": "text", "text": "hello from dict block"}],
    )

    parts = renderer.message_to_parts(message)

    assert len(parts) == 1
    assert getattr(parts[0], "text", "") == "hello from dict block"


def test_message_renderer_renders_dict_thinking_block() -> None:
    renderer = MessageRenderer(RenderStyle(filter_thinking=False))
    message = _message(
        MessageType.MESSAGE,
        [{"type": "thinking", "thinking": "step-by-step reasoning"}],
    )

    parts = renderer.message_to_parts(message)

    assert len(parts) == 1
    assert getattr(parts[0], "text", "") == "step-by-step reasoning"


def test_message_renderer_filters_dict_thinking_block() -> None:
    renderer = MessageRenderer(RenderStyle(filter_thinking=True))
    message = _message(
        MessageType.MESSAGE,
        [{"type": "thinking", "thinking": "hidden reasoning"}],
    )

    parts = renderer.message_to_parts(message)

    assert parts == []


def test_message_renderer_renders_hubos_status_block_with_output() -> None:
    renderer = MessageRenderer()
    message = _message(
        MessageType.MESSAGE,
        [
            {
                "type": "hubos_status",
                "name": "Context understanding",
                "status": "completed",
                "output": "depth=deep",
            },
        ],
    )

    parts = renderer.message_to_parts(message)

    assert len(parts) == 1
    text = getattr(parts[0], "text", "")
    assert "Context understanding" in text
    assert "depth=deep" in text


def test_message_renderer_renders_hubos_status_block_without_output() -> None:
    renderer = MessageRenderer()
    message = _message(
        MessageType.MESSAGE,
        [
            {
                "type": "hubos_status",
                "name": "Knowledge injection",
                "status": "in_progress",
            },
        ],
    )

    parts = renderer.message_to_parts(message)

    assert len(parts) == 1
    assert "Knowledge injection" in getattr(parts[0], "text", "")


def test_message_renderer_renders_datacontent_hubos_status_block() -> None:
    renderer = MessageRenderer()
    message = _message(
        MessageType.MESSAGE,
        [
            SimpleNamespace(
                type=ContentType.DATA,
                data={
                    "kind": "hubos_status",
                    "name": "Experience matching",
                    "status": "completed",
                    "output": "matched: customer_development",
                },
            ),
        ],
    )

    parts = renderer.message_to_parts(message)

    assert len(parts) == 1
    text = getattr(parts[0], "text", "")
    assert "Experience matching" in text
    assert "matched: customer_development" in text


def test_message_renderer_does_not_emit_placeholder_for_empty_message() -> (
    None
):
    renderer = MessageRenderer(RenderStyle(filter_thinking=True))
    message = _message(MessageType.MESSAGE, [])

    parts = renderer.message_to_parts(message)

    assert parts == []
