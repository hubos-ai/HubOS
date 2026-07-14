# -*- coding: utf-8 -*-
import pytest
from agentscope.message import Msg

from hubos.agents.model_factory import (
    _create_formatter_instance,
    _inject_reasoning_content_best_effort,
    _map_reasoning_by_tool_call_id,
)
from hubos.providers.openai_chat_model_compat import OpenAIChatModelCompat

try:
    from agentscope.formatter import GeminiChatFormatter
    from agentscope.model import GeminiChatModel
except ImportError:  # pragma: no cover
    GeminiChatFormatter = None
    GeminiChatModel = None


def test_formatter_applies_configured_input_limit() -> None:
    token_counter = object()

    formatter = _create_formatter_instance(
        OpenAIChatModelCompat,
        token_counter=token_counter,
        max_tokens=12_345,
    )

    assert formatter.token_counter is token_counter
    assert formatter.max_tokens == 12_345


@pytest.mark.asyncio
async def test_openai_formatter_preserves_reasoning_content_for_tool_call():
    formatter = _create_formatter_instance(OpenAIChatModelCompat)
    msgs = [
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "need shell"},
                {
                    "type": "tool_use",
                    "id": "call1",
                    "name": "execute_shell_command",
                    "input": {"command": "echo hi"},
                },
            ],
        ),
        Msg(
            name="system",
            role="system",
            content=[
                {
                    "type": "tool_result",
                    "id": "call1",
                    "name": "execute_shell_command",
                    "output": [{"type": "text", "text": "hi"}],
                },
            ],
        ),
    ]

    formatted = await formatter._format(msgs)

    assistant_messages = [m for m in formatted if m.get("role") == "assistant"]
    assert assistant_messages
    assert assistant_messages[0]["reasoning_content"] == "need shell"
    assert assistant_messages[0]["tool_calls"][0]["id"] == "call1"


@pytest.mark.asyncio
@pytest.mark.skipif(GeminiChatModel is None, reason="Gemini unavailable")
async def test_gemini_formatter_drops_plain_tool_call_id_as_thought_signature():
    formatter = _create_formatter_instance(GeminiChatModel)
    msgs = [
        Msg(
            name="user",
            role="user",
            content=[{"type": "text", "text": "run a command"}],
        ),
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "call_ccb52513c01a4f3daf53779c",
                    "name": "execute_shell_command",
                    "input": {"command": "echo hi"},
                },
            ],
        ),
        Msg(
            name="system",
            role="system",
            content=[
                {
                    "type": "tool_result",
                    "id": "call_ccb52513c01a4f3daf53779c",
                    "name": "execute_shell_command",
                    "output": [{"type": "text", "text": "hi"}],
                },
            ],
        ),
    ]

    formatted = await formatter._format(msgs)

    model_msgs = [m for m in formatted if m.get("role") == "model"]
    assert model_msgs
    part = model_msgs[0]["parts"][0]
    assert "function_call" in part
    assert "thought_signature" not in part


@pytest.mark.asyncio
@pytest.mark.skipif(GeminiChatModel is None, reason="Gemini unavailable")
async def test_gemini_formatter_preserves_valid_base64_thought_signature():
    formatter = _create_formatter_instance(GeminiChatModel)
    msgs = [
        Msg(
            name="user",
            role="user",
            content=[{"type": "text", "text": "run a command"}],
        ),
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "call1",
                    "name": "execute_shell_command",
                    "input": {"command": "echo hi"},
                    "extra_content": "YWJjZA==",
                },
            ],
        ),
        Msg(
            name="system",
            role="system",
            content=[
                {
                    "type": "tool_result",
                    "id": "call1",
                    "name": "execute_shell_command",
                    "output": [{"type": "text", "text": "hi"}],
                },
            ],
        ),
    ]

    formatted = await formatter._format(msgs)

    model_msgs = [m for m in formatted if m.get("role") == "model"]
    assert model_msgs
    assert model_msgs[0]["parts"][0]["thought_signature"] == "YWJjZA=="


@pytest.mark.asyncio
@pytest.mark.skipif(GeminiChatModel is None, reason="Gemini unavailable")
async def test_gemini_formatter_preserves_valid_base64_tool_use_id():
    formatter = _create_formatter_instance(GeminiChatModel)
    msgs = [
        Msg(
            name="user",
            role="user",
            content=[{"type": "text", "text": "run a command"}],
        ),
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "U2lnbmF0dXJlIEE=",
                    "name": "execute_shell_command",
                    "input": {"command": "echo hi"},
                },
            ],
        ),
        Msg(
            name="system",
            role="system",
            content=[
                {
                    "type": "tool_result",
                    "id": "U2lnbmF0dXJlIEE=",
                    "name": "execute_shell_command",
                    "output": [{"type": "text", "text": "hi"}],
                },
            ],
        ),
    ]

    formatted = await formatter._format(msgs)

    model_msgs = [m for m in formatted if m.get("role") == "model"]
    assert model_msgs
    assert model_msgs[0]["parts"][0]["thought_signature"] == "U2lnbmF0dXJlIEE="


@pytest.mark.skipif(GeminiChatModel is None, reason="Gemini unavailable")
def test_gemini_model_subclass_still_uses_gemini_formatter():
    class CustomGeminiChatModel(GeminiChatModel):
        pass

    formatter = _create_formatter_instance(CustomGeminiChatModel)

    assert isinstance(formatter, GeminiChatFormatter)


def test_reasoning_injection_best_effort_on_count_mismatch():
    msgs = [
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "first reasoning"},
                {"type": "text", "text": "first"},
            ],
        ),
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "second reasoning"},
                {"type": "text", "text": "second"},
            ],
        ),
    ]
    # Simulate the parent formatter dropping one assistant message. The helper
    # must still inject what it can instead of skipping all reasoning_content.
    formatted = [{"role": "assistant", "content": "survivor"}]

    _inject_reasoning_content_best_effort(msgs, formatted)

    assert formatted[0]["reasoning_content"] == "first reasoning"


def test_reasoning_injection_prefers_matching_tool_call_id():
    msgs = [
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "reasoning for call2"},
                {
                    "type": "tool_use",
                    "id": "call2",
                    "name": "execute_shell_command",
                    "input": {"command": "pwd"},
                },
            ],
        ),
    ]
    formatted = [
        {
            "role": "assistant",
            "content": "different survivor",
            "tool_calls": [
                {
                    "id": "call2",
                    "type": "function",
                    "function": {"name": "execute_shell_command"},
                },
            ],
        },
    ]

    _inject_reasoning_content_best_effort(msgs, formatted)

    assert formatted[0]["reasoning_content"] == "reasoning for call2"


def test_reasoning_injection_marks_synthetic_tool_call_with_empty_string():
    msgs = [
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "some prior reasoning"},
                {"type": "text", "text": "plain"},
            ],
        ),
    ]
    formatted = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "status-call",
                    "type": "function",
                    "function": {"name": "Context understanding"},
                },
            ],
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "status-call-2",
                    "type": "function",
                    "function": {"name": "Knowledge injection"},
                },
            ],
        },
    ]

    _inject_reasoning_content_best_effort(msgs, formatted)

    assert formatted[0]["reasoning_content"] == "some prior reasoning"
    assert formatted[1]["reasoning_content"] == ""


def test_reasoning_by_tool_call_id_mapping():
    msgs = [
        Msg(
            name="assistant",
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "map me"},
                {
                    "type": "tool_use",
                    "id": "call-a",
                    "name": "read_file",
                    "input": {},
                },
                {
                    "type": "tool_use",
                    "id": "call-b",
                    "name": "grep_search",
                    "input": {},
                },
            ],
        ),
    ]

    assert _map_reasoning_by_tool_call_id(msgs) == {
        "call-a": "map me",
        "call-b": "map me",
    }


def test_reasoning_injection_ignores_non_thinking_messages():
    msgs = [
        Msg(
            name="assistant",
            role="assistant",
            content=[{"type": "text", "text": "plain"}],
        ),
    ]
    formatted = [{"role": "assistant", "content": "plain"}]

    _inject_reasoning_content_best_effort(msgs, formatted)

    assert "reasoning_content" not in formatted[0]
