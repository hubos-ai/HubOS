from __future__ import annotations

from agentscope.message import Msg

from hubos.core.memory.workspace_ledger import (
    get_workspace_memory_store,
    ledger_session_key,
    persist_memory_to_ledger,
    workspace_memory_root,
)


class _Memory:
    def __init__(self, content):
        self.content = content


def test_workspace_ledger_is_user_isolated_and_idempotent(tmp_path):
    tool_msg = Msg(
        name="assistant",
        role="assistant",
        content=[
            {
                "type": "tool_use",
                "id": "call-large",
                "name": "write_file",
                "input": {"content": "x" * 2_000, "path": "report.md"},
                "raw_input": "duplicate raw input",
            },
        ],
    )
    memory = _Memory([(tool_msg, [])])

    kwargs = {
        "memory": memory,
        "workspace_dir": tmp_path,
        "session_id": "feishu/user/session",
        "user_id": "user-a",
        "channel": "feishu",
        "agent_id": "default",
        "title": "生成报告",
    }
    assert persist_memory_to_ledger(**kwargs) == 1
    assert persist_memory_to_ledger(**kwargs) == 0

    store = get_workspace_memory_store(tmp_path, "user-a")
    loaded = store.load_session(ledger_session_key("feishu/user/session"))
    assert loaded is not None
    assert len(loaded["messages"]) == 1
    block = loaded["messages"][0]["content"][0]
    assert block["input"]["_stored_separately"] is True
    assert "raw_input" not in block
    assert (
        len(
            list(
                (
                    store.sessions_dir
                    / ledger_session_key("feishu/user/session")
                    / "tools"
                ).glob("*.json")
            )
        )
        == 1
    )

    assert workspace_memory_root(tmp_path, "user-a") != workspace_memory_root(
        tmp_path,
        "user-b",
    )
    other_store = get_workspace_memory_store(tmp_path, "user-b")
    assert other_store.load_session(ledger_session_key("feishu/user/session")) is None
