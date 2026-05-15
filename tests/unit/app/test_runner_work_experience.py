# -*- coding: utf-8 -*-
"""Regression tests for runner work-experience/status-message wiring.

These tests intentionally inspect the runner source instead of importing the
full runner stack.  The runner depends on AgentScope/runtime integrations that
are expensive to import in unit tests; the contract we need to protect here is
small and concrete:

- pre-agent status cards are emitted for Context understanding / Experience
  matching so users see progress before the real agent starts;
- those status messages are persisted after session-state load with an
  internal memory mark so refresh keeps the cards while model context excludes
  them.
"""
from __future__ import annotations

import ast
from pathlib import Path

_RUNNER_PATH = Path(__file__).resolve().parents[3] / "src" / "hubos" / "app" / "runner" / "runner.py"
_SOURCE = _RUNNER_PATH.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)


def _string_literals() -> set[str]:
    return {node.value for node in ast.walk(_TREE) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def test_pre_agent_status_names_are_present() -> None:
    """The visible status cards should not be accidentally removed."""
    strings = _string_literals()
    assert "Context understanding" in strings
    assert "Experience matching" in strings
    assert "Knowledge injection" in strings


def test_status_messages_are_persisted_with_internal_memory_mark() -> None:
    """Status messages are persisted for UI history, but marked internal."""
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "add":
            continue
        owner = func.value
        if not (
            isinstance(owner, ast.Attribute)
            and owner.attr == "memory"
            and isinstance(owner.value, ast.Name)
            and owner.value.id == "agent"
        ):
            continue
        has_status_arg = (
            len(call.args) == 1
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "pre_agent_status_msgs"
        )
        has_internal_mark = any(
            kw.arg == "marks"
            and isinstance(kw.value, ast.Name)
            and kw.value.id == "_INTERNAL_STATUS_MEMORY_MARK"
            for kw in call.keywords
        )
        if has_status_arg and has_internal_mark:
            return
    raise AssertionError(
        "runner.py must persist pre_agent_status_msgs with "
        "_INTERNAL_STATUS_MEMORY_MARK",
    )


def test_internal_status_messages_are_filtered_from_model_memory() -> None:
    """The persisted UI cards must not be replayed into LLM context."""
    assert "_mark_internal_status_messages(agent.memory)" in _SOURCE
    assert "_install_internal_status_memory_filter(" in _SOURCE
    assert 'kwargs["exclude_mark"] = _INTERNAL_STATUS_MEMORY_MARK' in _SOURCE


def test_hallucinated_internal_status_calls_are_stripped() -> None:
    """Model-emitted fake calls to internal phases should be dropped."""
    assert "_strip_hallucinated_internal_status_blocks(msg)" in _SOURCE
    assert "_strip_hallucinated_internal_status_from_memory(agent.memory)" in _SOURCE
    assert "Dropped hallucinated internal status block" in _SOURCE


def test_hubos_status_stream_converter_registered() -> None:
    """Streaming adapter must convert hubos_status before it reaches the UI."""
    assert "self.out_type_converters" in _SOURCE
    assert '"hubos_status": _hubos_status_stream_converter' in _SOURCE
    assert "kind" in _SOURCE


def test_status_cards_use_internal_protocol_not_tool_calls() -> None:
    """Internal status cards must not masquerade as model tool calls."""
    strings = _string_literals()
    assert "hubos_status" in strings
    assert "_make_internal_status_msg" in _SOURCE
    assert '"type": "tool_use"' not in _SOURCE
    assert '"type": "tool_result"' not in _SOURCE
    assert "name" in strings


def test_experience_matching_no_longer_hardcoded_done() -> None:
    """Experience matching status card must use real status, not fixed 'done'."""
    strings = _string_literals()
    # f-string parts are separate string literals in the AST
    assert "matched: " in strings
    assert "no matching card · " in strings
    assert "model unavailable · " in strings
    assert "invalid model output · " in strings
    assert "model call failed · " in strings


def test_runner_includes_internal_phase_guard_for_llm() -> None:
    """The main model should be told not to call internal status phases."""
    assert "Internal runner note:" in _SOURCE
    assert "Context understanding, Experience matching, and " in _SOURCE
    assert "Knowledge injection are internal pre-execution stages " in _SOURCE
    assert "already handled by the Runner." in _SOURCE
    assert "Do not call them as tools, do not emit function calls " in _SOURCE
    assert "for them, and do not repeat them after your answer." in _SOURCE
