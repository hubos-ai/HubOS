# -*- coding: utf-8 -*-
"""Tests for normalize_pre_agent_status_order in runner/utils.py.

Verifies that "Context understanding" / "Experience matching" status tool
messages that appear before a user message get reordered after it, while
normal tool calls (edit_file, execute_shell_command, …) are left untouched.

Uses importlib to load utils.py directly, bypassing heavy dependencies.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SRC_ROOT = _HERE.parents[3] / "src"
_MODULE_PATH = _SRC_ROOT / "hubos" / "app" / "runner" / "utils.py"


def _load_utils_module():
    """Load utils.py without triggering agentscope imports in __init__.py."""
    import sys

    # Register parent packages so relative imports resolve.
    for pkg in ("hubos", "hubos.app", "hubos.app.runner"):
        if pkg not in sys.modules:
            sys.modules[pkg] = type(sys)(pkg)

    # Pre-register stub modules to satisfy the top-level imports.
    if "agentscope.message" not in sys.modules:
        m = type(sys)("agentscope.message")
        m.Msg = type("Msg", (), {})
        sys.modules["agentscope.message"] = m
    if "agentscope_runtime.engine.schemas.agent_schemas" not in sys.modules:
        schemas = type(sys)(
            "agentscope_runtime.engine.schemas.agent_schemas",
        )
        schemas.Message = type("Message", (), {})
        schemas.TextContent = type("TextContent", (), {})
        schemas.ImageContent = type("ImageContent", (), {})
        schemas.AudioContent = type("AudioContent", (), {})
        schemas.VideoContent = type("VideoContent", (), {})
        schemas.FileContent = type("FileContent", (), {})
        schemas.DataContent = type("DataContent", (), {})
        schemas.FunctionCall = type(
            "FunctionCall", (), {"model_dump": lambda self: {}}
        )
        schemas.FunctionCallOutput = type(
            "FunctionCallOutput",
            (),
            {"model_dump": lambda self, **kw: {}},
        )
        schemas.MessageType = type(
            "MessageType",
            (),
            {
                "MESSAGE": "message",
                "REASONING": "reasoning",
                "PLUGIN_CALL": "plugin_call",
                "PLUGIN_CALL_OUTPUT": "plugin_call_output",
            },
        )
        sys.modules["agentscope_runtime"] = type(sys)("agentscope_runtime")
        sys.modules["agentscope_runtime.engine"] = type(sys)(
            "agentscope_runtime.engine",
        )
        sys.modules["agentscope_runtime.engine.schemas"] = type(sys)(
            "agentscope_runtime.engine.schemas",
        )
        sys.modules[
            "agentscope_runtime.engine.schemas.agent_schemas"
        ] = schemas
    if "hubos.config" not in sys.modules:
        config = type(sys)("hubos.config")
        _cfg = type("Config", (), {"user_timezone": "UTC"})()
        config.load_config = lambda: _cfg
        sys.modules["hubos.config"] = config

    spec = importlib.util.spec_from_file_location(
        "hubos.app.runner.utils",
        str(_MODULE_PATH),
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hubos.app.runner.utils"] = mod
    spec.loader.exec_module(mod)
    return mod


_mod = _load_utils_module()
normalize_pre_agent_status_order = _mod.normalize_pre_agent_status_order


# Lightweight stub that satisfies the checks in normalize_pre_agent_status_order.


class _FakeContent:
    """Mimics DataContent with a ``data`` dict."""

    def __init__(self, data: dict):
        self.data = data


class _FakeMsg:
    """Minimal Message-like object used by the tests."""

    def __init__(
        self, role: str, msg_type: str = "message", name: str | None = None
    ):
        self.role = role
        self.type = msg_type
        self.content = []
        if name is not None and msg_type in (
            "plugin_call",
            "plugin_call_output",
        ):
            self.content.append(_FakeContent({"name": name}))


def _msg(role: str, msg_type: str = "message", name: str | None = None):
    return _FakeMsg(role, msg_type, name)


# ---- helpers ----


def _roles(messages: list) -> list[str]:
    return [m.role for m in messages]


def _types(messages: list) -> list[str]:
    return [m.type for m in messages]


def _names(messages: list) -> list[str | None]:
    out = []
    for m in messages:
        if m.content and hasattr(m.content[0], "data"):
            out.append(m.content[0].data.get("name"))
        else:
            out.append(None)
    return out


# ---- tests ----


class TestStatusToolReorder:
    """Status tools (Context understanding, Experience matching) before user
    message get moved after it."""

    def test_basic_reorder(self):
        """Status plugin_call + output before user → moved after user."""
        msgs = [
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "Context understanding"),
            _msg("tool", "plugin_call_output", "Context understanding"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert _roles(result) == [
            "assistant",
            "user",
            "assistant",
            "tool",
            "assistant",
        ]

    def test_two_status_tools_before_user(self):
        """Multiple status tool blocks before user get moved together."""
        msgs = [
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "Context understanding"),
            _msg("tool", "plugin_call_output", "Context understanding"),
            _msg("assistant", "plugin_call", "Experience matching"),
            _msg("tool", "plugin_call_output", "Experience matching"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert _names(result) == [
            None,  # assistant message
            None,  # user message
            "Context understanding",
            "Context understanding",
            "Experience matching",
            "Experience matching",
            None,  # assistant message
        ]

    def test_status_after_user_untouched(self):
        """Status tools already after user message are not moved."""
        msgs = [
            _msg("user", "message"),
            _msg("assistant", "plugin_call", "Context understanding"),
            _msg("tool", "plugin_call_output", "Context understanding"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert result == msgs

    def test_no_user_message_untouched(self):
        """Messages without a following user message stay in place."""
        msgs = [
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "Context understanding"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert result == msgs

    def test_empty_list(self):
        assert normalize_pre_agent_status_order([]) == []

    def test_single_user(self):
        msgs = [_msg("user", "message")]
        assert normalize_pre_agent_status_order(msgs) == msgs


class TestNormalToolsUntouched:
    """Non-status tools (edit_file, execute_shell_command, etc.) must never
    be reordered."""

    def test_edit_file_not_reordered(self):
        """edit_file plugin_call before user message stays in place."""
        msgs = [
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "edit_file"),
            _msg("tool", "plugin_call_output", "edit_file"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert result == msgs

    def test_execute_shell_command_not_reordered(self):
        msgs = [
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "execute_shell_command"),
            _msg("tool", "plugin_call_output", "execute_shell_command"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert result == msgs

    def test_mixed_status_and_normal(self):
        """Only status tools are moved; normal tools interleaved before user
        block the reordering of the whole contiguous run."""
        msgs = [
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "Context understanding"),
            _msg("tool", "plugin_call_output", "Context understanding"),
            _msg("assistant", "plugin_call", "edit_file"),
            _msg("tool", "plugin_call_output", "edit_file"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        # The contiguous block includes edit_file which is NOT a status tool,
        # so the entire block is left in place.
        assert result == msgs

    def test_web_search_not_reordered(self):
        msgs = [
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "web_search_prime"),
            _msg("tool", "plugin_call_output", "web_search_prime"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert result == msgs


class TestMultiTurnReorder:
    """Status tools should be reordered for ALL user messages, not just the
    first one.  This is the key bug — the old implementation only fixed the
    first user message."""

    def test_second_turn_status_reorder(self):
        """Turn 2 status tools before user2 → moved after user2."""
        msgs = [
            _msg("user", "message"),
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "Context understanding"),
            _msg("tool", "plugin_call_output", "Context understanding"),
            _msg("assistant", "plugin_call", "Experience matching"),
            _msg("tool", "plugin_call_output", "Experience matching"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert _names(result) == [
            None,  # user1
            None,  # assistant1
            None,  # user2 (moved forward)
            "Context understanding",  # ← moved after user2
            "Context understanding",
            "Experience matching",
            "Experience matching",
            None,  # assistant2
        ]

    def test_both_turns_reordered(self):
        """Both turn 1 and turn 2 status tools get reordered."""
        msgs = [
            _msg("assistant", "plugin_call", "Context understanding"),
            _msg("tool", "plugin_call_output", "Context understanding"),
            _msg("user", "message"),
            _msg("assistant", "message"),
            _msg("assistant", "plugin_call", "Context understanding"),
            _msg("tool", "plugin_call_output", "Context understanding"),
            _msg("user", "message"),
            _msg("assistant", "message"),
        ]
        result = normalize_pre_agent_status_order(msgs)
        assert _names(result) == [
            None,  # user1
            "Context understanding",  # ← moved after user1
            "Context understanding",
            None,  # assistant1
            None,  # user2 (moved forward)
            "Context understanding",  # ← moved after user2
            "Context understanding",
            None,  # assistant2
        ]
        # Also verify roles are correct
        assert _roles(result) == [
            "user",
            "assistant",
            "tool",
            "assistant",
            "user",
            "assistant",
            "tool",
            "assistant",
        ]
