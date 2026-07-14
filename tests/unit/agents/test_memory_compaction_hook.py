# -*- coding: utf-8 -*-
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hubos.agents.hooks.memory_compaction import MemoryCompactionHook


class _TokenCounter:
    async def count(self, messages=None, text=None):
        if text and '"function"' in text:
            return 11
        return 7


class _Toolkit:
    def get_json_schemas(self):
        return [
            {
                "type": "function",
                "function": {"name": "lookup"},
            },
        ]


@pytest.mark.asyncio
async def test_compaction_budget_includes_tools_and_output_reserve(
    monkeypatch,
) -> None:
    running = SimpleNamespace(
        memory_compact_threshold=100,
        memory_compact_reserve=10,
        context_compact=SimpleNamespace(output_reserve_tokens=20),
        tool_result_compact=SimpleNamespace(enabled=False),
    )
    config = SimpleNamespace(running=running)
    monkeypatch.setattr(
        "hubos.agents.hooks.memory_compaction.load_agent_config",
        lambda _agent_id: config,
    )
    monkeypatch.setattr(
        "hubos.agents.hooks.memory_compaction.get_hubos_token_counter",
        lambda _config: _TokenCounter(),
    )

    memory = SimpleNamespace(
        get_compressed_summary=lambda: "summary",
        get_memory=AsyncMock(return_value=[]),
    )
    memory_manager = SimpleNamespace(
        agent_id="agent",
        compact_tool_result=AsyncMock(),
        check_context=AsyncMock(return_value=([], 0, True)),
    )
    agent = SimpleNamespace(
        memory=memory,
        sys_prompt="system",
        toolkit=_Toolkit(),
    )

    await MemoryCompactionHook(memory_manager)(agent, {})

    assert (
        memory_manager.check_context.await_args.kwargs[
            "memory_compact_threshold"
        ]
        == 62
    )
