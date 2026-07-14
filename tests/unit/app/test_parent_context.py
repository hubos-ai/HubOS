# -*- coding: utf-8 -*-
"""Tests for parent_context module — recall_parent_context tool."""
import json
import os
import tempfile
from pathlib import Path

import pytest

from hubos.core.parent_context import (
    _extract_text_from_content,
    _keyword_search,
    _load_parent_messages,
    auto_briefing,
    create_parent_context_tool,
)
from hubos.app.runner.session import sanitize_filename


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_session_file(
    tmp_path: Path,
    session_id: str,
    messages: list[dict],
    agent_id: str = "default",
) -> Path:
    """Create a fake session JSON file matching SafeJSONSession format."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    safe_sid = sanitize_filename(session_id)
    filename = f"{agent_id}_{safe_sid}.json"
    filepath = sessions_dir / filename

    # Build agentscope session format
    content = []
    for msg in messages:
        content.append([msg, []])  # [msg_dict, marks_list]

    data = {
        "agent": {
            "memory": {
                "content": content,
                "_compressed_summary": "",
            },
        },
    }
    filepath.write_text(json.dumps(data, ensure_ascii=False))
    return filepath


@pytest.fixture
def sample_messages():
    """Sample parent conversation messages."""
    return [
        {
            "id": "1",
            "name": "assistant",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "I analyzed stock_live.py. It has 35 price-derived "
                        "factors and 889 lines. Key functions: "
                        "update_data(), compute_factors(), train_model()."
                    ),
                },
            ],
            "metadata": {},
            "timestamp": "2026-05-24T10:00:00",
        },
        {
            "id": "2",
            "name": "system",
            "role": "system",
            "content": [
                {
                    "type": "tool_result",
                    "id": "call_1",
                    "name": "execute_shell_command",
                    "output": [
                        {
                            "type": "text",
                            "text": "akshare stock_individual_fund_flow: "
                            "Connection refused\n"
                            "stock_financial_abstract_ths: OK, 300 stocks",
                        },
                    ],
                },
            ],
            "metadata": {},
            "timestamp": "2026-05-24T10:01:00",
        },
        {
            "id": "3",
            "name": "assistant",
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Conclusion: use stock_financial_abstract_ths for "
                        "fundamental data. It returned 15 useful fields "
                        "including roe, net_margin, gross_margin."
                    ),
                },
            ],
            "metadata": {},
            "timestamp": "2026-05-24T10:02:00",
        },
        {
            "id": "4",
            "name": "assistant",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_2",
                    "name": "read_file",
                    "input": {
                        "file_path": "scripts/stock_live.py",
                    },
                },
            ],
            "metadata": {},
            "timestamp": "2026-05-24T10:03:00",
        },
    ]


@pytest.fixture
def session_dir(tmp_path, sample_messages):
    """Create a session directory with sample data."""
    _make_session_file(tmp_path, "parent-session-123", sample_messages)
    return tmp_path / "sessions"


# ---------------------------------------------------------------------------
# Tests: _extract_text_from_content
# ---------------------------------------------------------------------------


class TestExtractText:
    def test_string_content(self):
        assert _extract_text_from_content("hello") == "hello"

    def test_text_block(self):
        content = [{"type": "text", "text": "hello world"}]
        assert _extract_text_from_content(content) == "hello world"

    def test_tool_result_block(self):
        content = [
            {
                "type": "tool_result",
                "id": "call_1",
                "name": "execute_shell_command",
                "output": [{"type": "text", "text": "42 records found"}],
            },
        ]
        text = _extract_text_from_content(content)
        assert "42 records found" in text

    def test_tool_use_block(self):
        content = [
            {
                "type": "tool_use",
                "id": "call_1",
                "name": "read_file",
                "input": {"file_path": "test.py"},
            },
        ]
        text = _extract_text_from_content(content)
        assert "[tool_use: read_file]" in text
        assert "test.py" in text

    def test_empty_content(self):
        assert _extract_text_from_content(None) == ""
        assert _extract_text_from_content([]) == ""

    def test_mixed_blocks(self):
        content = [
            {"type": "text", "text": "Part 1"},
            {
                "type": "tool_result",
                "id": "c1",
                "name": "cmd",
                "output": [{"type": "text", "text": "Part 2"}],
            },
        ]
        text = _extract_text_from_content(content)
        assert "Part 1" in text
        assert "Part 2" in text


# ---------------------------------------------------------------------------
# Tests: _load_parent_messages
# ---------------------------------------------------------------------------


class TestLoadParentMessages:
    def test_loads_messages(self, session_dir):
        msgs = _load_parent_messages("parent-session-123", session_dir)
        assert len(msgs) == 4
        # All have text
        assert all("text" in m for m in msgs)

    def test_returns_assistant_text(self, session_dir):
        msgs = _load_parent_messages("parent-session-123", session_dir)
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert len(assistant_msgs) >= 1
        assert "stock_live.py" in assistant_msgs[0]["text"]

    def test_missing_session(self, session_dir):
        msgs = _load_parent_messages("nonexistent", session_dir)
        assert msgs == []

    def test_empty_dir(self, tmp_path):
        empty = tmp_path / "empty_sessions"
        empty.mkdir()
        msgs = _load_parent_messages("whatever", empty)
        assert msgs == []


# ---------------------------------------------------------------------------
# Tests: _keyword_search
# ---------------------------------------------------------------------------


class TestKeywordSearch:
    def test_finds_relevant_messages(self, sample_messages):
        # Convert to internal format
        msgs = []
        for m in sample_messages:
            text = _extract_text_from_content(m["content"])
            if text.strip():
                msgs.append(
                    {
                        "role": m["role"],
                        "name": m["name"],
                        "text": text,
                        "timestamp": m["timestamp"],
                    },
                )

        results = _keyword_search(msgs, "stock_live.py functions")
        assert len(results) >= 1
        assert any("stock_live.py" in r["text"] for r in results)

    def test_finds_akshare_results(self, sample_messages):
        msgs = []
        for m in sample_messages:
            text = _extract_text_from_content(m["content"])
            if text.strip():
                msgs.append(
                    {
                        "role": m["role"],
                        "name": m["name"],
                        "text": text,
                        "timestamp": m["timestamp"],
                    },
                )

        results = _keyword_search(msgs, "akshare test results")
        assert len(results) >= 1
        assert any(
            "stock_financial_abstract_ths" in r["text"] for r in results
        )

    def test_no_results_for_unrelated_query(self, sample_messages):
        msgs = []
        for m in sample_messages:
            text = _extract_text_from_content(m["content"])
            if text.strip():
                msgs.append(
                    {
                        "role": m["role"],
                        "name": m["name"],
                        "text": text,
                        "timestamp": m["timestamp"],
                    },
                )

        results = _keyword_search(msgs, "quantum computing physics")
        assert len(results) == 0

    def test_empty_keywords_returns_recent(self):
        msgs = [
            {
                "role": "assistant",
                "name": "a",
                "text": "old msg",
                "timestamp": "1",
            },
            {
                "role": "user",
                "name": "u",
                "text": "user msg",
                "timestamp": "2",
            },
            {
                "role": "assistant",
                "name": "a",
                "text": "recent msg",
                "timestamp": "3",
            },
        ]
        results = _keyword_search(msgs, "", top_k=2)
        # Should return last 2 assistant messages
        assert len(results) == 2

    def test_top_k_limit(self, sample_messages):
        msgs = []
        for m in sample_messages:
            text = _extract_text_from_content(m["content"])
            if text.strip():
                msgs.append(
                    {
                        "role": m["role"],
                        "name": m["name"],
                        "text": text,
                        "timestamp": m["timestamp"],
                    },
                )

        results = _keyword_search(msgs, "stock", top_k=1)
        assert len(results) <= 1


# ---------------------------------------------------------------------------
# Tests: create_parent_context_tool
# ---------------------------------------------------------------------------


class TestCreateParentContextTool:
    def test_tool_returns_results(self, tmp_path, sample_messages):
        _make_session_file(tmp_path, "test-parent-001", sample_messages)
        sessions_dir = tmp_path / "sessions"

        tool = create_parent_context_tool(
            parent_session_id="test-parent-001",
            workspace_dir=str(tmp_path),
        )

        result = tool("stock_live functions")
        assert "stock_live.py" in result
        assert "Parent context" in result

    def test_tool_handles_missing_session(self, tmp_path):
        # Need sessions dir to exist, but no matching file
        (tmp_path / "sessions").mkdir()

        tool = create_parent_context_tool(
            parent_session_id="nonexistent-session",
            workspace_dir=str(tmp_path),
        )

        result = tool("anything")
        assert "No messages found" in result

    def test_tool_handles_no_results(self, tmp_path, sample_messages):
        _make_session_file(tmp_path, "test-parent-002", sample_messages)

        tool = create_parent_context_tool(
            parent_session_id="test-parent-002",
            workspace_dir=str(tmp_path),
        )

        result = tool("quantum physics extraterrestrial")
        assert "No relevant" in result

    def test_tool_function_name(self, tmp_path):
        tool = create_parent_context_tool(
            parent_session_id="test",
            workspace_dir=str(tmp_path),
        )
        assert tool.__name__ == "recall_parent_context"

    def test_tool_truncates_long_output(self, tmp_path):
        # Create a session with very long messages
        long_msgs = [
            {
                "id": str(i),
                "name": "assistant",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "word " * 10000,  # ~50K chars
                    },
                ],
                "metadata": {},
                "timestamp": f"2026-05-24T10:0{i}:00",
            }
            for i in range(10)
        ]
        _make_session_file(tmp_path, "test-long-001", long_msgs)

        tool = create_parent_context_tool(
            parent_session_id="test-long-001",
            workspace_dir=str(tmp_path),
        )

        result = tool("word")
        # Should be truncated, not 500K chars
        assert len(result) < 10000
        assert "truncated" in result


# ---------------------------------------------------------------------------
# Tests: auto_briefing
# ---------------------------------------------------------------------------


class TestAutoBriefing:
    def test_explicit_handoff_survives_without_saved_parent_session(
        self,
        tmp_path,
    ):
        result = auto_briefing(
            prompt="分析失败原因",
            parent_session_id="not-saved-yet",
            workspace_dir=str(tmp_path),
            handoff={
                "objective": "定位 GLM 超时",
                "known_context": "请求体约 52k tokens",
                "constraints": ["不要中断正在执行的任务"],
                "artifacts": ["logs/model.log"],
            },
        )

        assert "父代理交接包" in result
        assert "请求体约 52k tokens" in result
        assert "不要中断" in result
        assert result.endswith("分析失败原因")

    def test_prepends_relevant_context(self, tmp_path, sample_messages):
        _make_session_file(tmp_path, "brief-parent-001", sample_messages)

        result = auto_briefing(
            prompt="升级 stock_live.py 补全基本面数据",
            parent_session_id="brief-parent-001",
            workspace_dir=str(tmp_path),
        )

        # Should have briefing header
        assert "父会话上下文" in result
        # Should have relevant content from parent
        assert "stock_live.py" in result or "stock_financial" in result
        # Original prompt should still be there
        assert "升级 stock_live.py 补全基本面数据" in result
        # Briefing should come before the original prompt
        briefing_pos = result.index("父会话上下文")
        prompt_pos = result.index("升级 stock_live.py")
        assert briefing_pos < prompt_pos

    def test_mentions_recall_tool(self, tmp_path, sample_messages):
        _make_session_file(tmp_path, "brief-parent-002", sample_messages)

        result = auto_briefing(
            prompt="分析 stock_live.py",
            parent_session_id="brief-parent-002",
            workspace_dir=str(tmp_path),
        )

        assert "recall_parent_context" in result

    def test_returns_original_if_no_match(self, tmp_path, sample_messages):
        _make_session_file(tmp_path, "brief-parent-003", sample_messages)

        original = "quantum physics experiment"
        result = auto_briefing(
            prompt=original,
            parent_session_id="brief-parent-003",
            workspace_dir=str(tmp_path),
        )

        # No relevant context → return original unchanged
        assert result == original

    def test_returns_original_if_no_session(self, tmp_path):
        result = auto_briefing(
            prompt="do something",
            parent_session_id="nonexistent",
            workspace_dir=str(tmp_path),
        )

        assert result == "do something"

    def test_briefing_size_capped(self, tmp_path):
        # Create session with many long messages
        long_msgs = [
            {
                "id": str(i),
                "name": "assistant",
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": f"stock data analysis result {i}: "
                        + "word " * 2000,  # ~10K chars each
                    },
                ],
                "metadata": {},
                "timestamp": f"2026-05-24T10:0{i}:00",
            }
            for i in range(10)
        ]
        _make_session_file(tmp_path, "brief-long-001", long_msgs)

        result = auto_briefing(
            prompt="stock data analysis",
            parent_session_id="brief-long-001",
            workspace_dir=str(tmp_path),
        )

        # Briefing section should be bounded
        briefing_end = result.index("\n---\n\n")
        briefing = result[:briefing_end]
        # Should be capped around _BRIEFING_MAX_CHARS
        assert len(briefing) < 5000

    def test_no_briefing_for_missing_sessions_dir(self, tmp_path):
        # tmp_path has no sessions/ dir
        result = auto_briefing(
            prompt="do something",
            parent_session_id="any",
            workspace_dir=str(tmp_path),
        )

        assert result == "do something"
