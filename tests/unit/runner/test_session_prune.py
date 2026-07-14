# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import datetime, timedelta

from agentscope.message import Msg, TextBlock

from hubos.app.runner.session import (
    compact_stale_session_messages,
    compact_stale_session_messages_locally,
    prune_empty_assistant_messages,
    prune_stale_session_messages,
)


class _Memory:
    def __init__(self, content):
        self.content = content


def _stamp(hours_ago: float) -> str:
    return (datetime.now() - timedelta(hours=hours_ago)).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


def test_prune_removes_old_system_role_tool_results_but_keeps_prompt():
    prompt = Msg(
        name="system",
        role="system",
        content=[TextBlock(type="text", text="System prompt")],
    )
    prompt.timestamp = _stamp(48)

    stale_tool_result = Msg(
        name="system",
        role="system",
        content=[
            {
                "type": "tool_result",
                "id": "call_old",
                "name": "web_search",
                "output": [{"type": "text", "text": "old result"}],
            },
        ],
    )
    stale_tool_result.timestamp = _stamp(48)

    stale_user = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text="old user")],
    )
    stale_user.timestamp = _stamp(48)

    recent_user = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text="recent user")],
    )
    recent_user.timestamp = _stamp(0.1)

    memory = _Memory(
        [
            (prompt, []),
            (stale_tool_result, []),
            (stale_user, []),
            (recent_user, []),
        ],
    )

    pruned = prune_stale_session_messages(
        memory,
        max_age_hours=2.0,
        min_keep=1,
    )

    assert pruned == 2
    assert [item[0] for item in memory.content] == [prompt, recent_user]


def test_prune_empty_assistant_messages_only_removes_blank_text_or_thinking():
    blank_thinking = Msg(
        name="Friday",
        role="assistant",
        content=[{"type": "thinking", "thinking": ""}],
    )
    real_text = Msg(
        name="Friday",
        role="assistant",
        content=[TextBlock(type="text", text="正常回复")],
    )
    real_thinking = Msg(
        name="Friday",
        role="assistant",
        content=[{"type": "thinking", "thinking": "real reasoning"}],
    )
    tool_use = Msg(
        name="Friday",
        role="assistant",
        content=[{"type": "tool_use", "id": "call_1", "name": "search"}],
    )

    memory = _Memory(
        [
            (blank_thinking, []),
            (real_text, []),
            (real_thinking, []),
            (tool_use, []),
        ],
    )

    pruned = prune_empty_assistant_messages(memory)

    assert pruned == 1
    assert [item[0] for item in memory.content] == [
        real_text,
        real_thinking,
        tool_use,
    ]


async def test_time_compaction_summarizes_before_archiving(monkeypatch):
    stale = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text="old decision")],
    )
    stale.timestamp = (datetime.now() - timedelta(hours=5)).isoformat(
        timespec="milliseconds",
    )
    recent = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text="current task")],
    )
    recent.timestamp = datetime.now().isoformat(timespec="milliseconds")

    memory = _Memory([(stale, []), (recent, [])])
    archived = []
    summaries = []

    async def _mark(messages):
        archived.extend(messages)
        return len(messages)

    async def _update(summary):
        summaries.append(summary)

    memory.mark_messages_compressed = _mark
    memory.update_compressed_summary = _update
    memory.get_compressed_summary = lambda: "previous summary"

    class _Manager:
        async def compact_memory(self, **kwargs):
            assert kwargs["messages"] == [stale]
            assert kwargs["previous_summary"] == "previous summary"
            return "previous summary + old decision"

    count = await compact_stale_session_messages(
        memory,
        _Manager(),
        max_age_hours=2,
        min_keep=1,
    )

    assert count == 1
    assert archived == [stale]
    assert summaries == ["previous summary + old decision"]


async def test_local_time_compaction_does_not_call_a_model():
    stale = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text="old decision")],
    )
    stale.timestamp = (datetime.now() - timedelta(hours=5)).isoformat(
        timespec="milliseconds",
    )
    recent = Msg(
        name="user",
        role="user",
        content=[TextBlock(type="text", text="current task")],
    )
    recent.timestamp = datetime.now().isoformat(timespec="milliseconds")
    memory = _Memory([(stale, []), (recent, [])])
    summaries = []

    async def _mark(messages):
        ids = {message.id for message in messages}
        memory.content = [
            item for item in memory.content if item[0].id not in ids
        ]
        return len(messages)

    async def _update(summary):
        summaries.append(summary)

    memory.mark_messages_compressed = _mark
    memory.update_compressed_summary = _update
    memory.get_compressed_summary = lambda: ""

    count = await compact_stale_session_messages_locally(
        memory,
        max_age_hours=2,
        min_keep=1,
    )

    assert count == 1
    assert [item[0] for item in memory.content] == [recent]
    assert "old decision" in summaries[0]
    assert "完整原文已保存" in summaries[0]
