# -*- coding: utf-8 -*-
from hubos.providers.openai_chat_model_compat import (
    _safe_glm_max_tokens,
)


def test_glm_request_caps_large_output_at_safe_agent_limit() -> None:
    messages = [{"role": "user", "content": "hello"}]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Look up a value",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    ]

    result = _safe_glm_max_tokens(
        "GLM-5.1",
        messages,
        tools,
        131_072,
    )

    assert result == 16_384


def test_glm_request_without_tools_uses_the_same_output_cap() -> None:
    result = _safe_glm_max_tokens(
        "GLM-5.1",
        [{"role": "user", "content": "hello"}],
        None,
        131_072,
    )

    assert result == 16_384


def test_glm_tool_request_keeps_smaller_output_limit() -> None:
    result = _safe_glm_max_tokens(
        "glm-5.1",
        [{"role": "user", "content": "hello"}],
        [{"type": "function", "function": {"name": "lookup"}}],
        4_096,
    )

    assert result == 4_096


def test_glm_request_reserves_space_for_large_input() -> None:
    messages = [{"role": "user", "content": "x" * 600_000}]

    result = _safe_glm_max_tokens(
        "glm-5.1",
        messages,
        None,
        131_072,
    )

    assert result is not None
    assert result < 65_536


def test_context_budget_does_not_change_other_models() -> None:
    result = _safe_glm_max_tokens(
        "gpt-4.1",
        [{"role": "user", "content": "hello"}],
        None,
        131_072,
    )

    assert result is None
