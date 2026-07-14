# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from datetime import datetime, timedelta

from hubos.core.memory.session_migration import (
    compact_memory_state_locally,
    compact_tool_payloads_in_state,
    discover_sessions,
    migrate_session_file,
)
from hubos.core.memory.workspace_ledger import (
    get_workspace_memory_store,
    ledger_session_key,
)


def _message(index: int, *, hours_ago: float) -> list:
    timestamp = (datetime.now() - timedelta(hours=hours_ago)).isoformat(
        timespec="milliseconds",
    )
    return [
        {
            "id": f"msg-{index}",
            "name": "user",
            "role": "user",
            "timestamp": timestamp,
            "content": [{"type": "text", "text": f"decision {index}"}],
        },
        [],
    ]


def test_compact_memory_state_locally_keeps_recent_messages():
    state = {
        "content": [
            _message(1, hours_ago=6),
            _message(2, hours_ago=5),
            _message(3, hours_ago=0.2),
        ],
        "_compressed_summary": "",
    }

    compacted, kept = compact_memory_state_locally(state, min_keep=1)

    assert compacted == 2
    assert kept == 1
    assert state["content"][0][0]["id"] == "msg-3"
    assert "decision 1" in state["_compressed_summary"]
    assert "decision 2" in state["_compressed_summary"]


def test_compact_tool_payloads_in_state_archives_inputs_and_outputs(tmp_path):
    state = {
        "content": [
            [
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-big",
                            "name": "write_file",
                            "input": {"content": "x" * 3_000},
                        },
                    ],
                },
                [],
            ],
            [
                {
                    "id": "result-1",
                    "role": "system",
                    "content": [
                        {
                            "type": "tool_result",
                            "id": "call-big",
                            "name": "write_file",
                            "output": [{"type": "text", "text": "y" * 4_000}],
                        },
                    ],
                },
                [],
            ],
        ],
        "_compressed_summary": "",
    }

    count = compact_tool_payloads_in_state(
        state,
        workspace_dir=tmp_path,
        session_id="session-1",
    )

    assert count == 2
    tool_use = state["content"][0][0]["content"][0]
    tool_result = state["content"][1][0]["content"][0]
    assert tool_use["input"]["_archived_tool_input"] is True
    assert "Tool output archived:" in tool_result["output"][0]["text"]
    assert len(list((tmp_path / "refs" / "session-1").iterdir())) == 2


def test_migrate_session_archives_raw_history_and_writes_backup(tmp_path):
    workspaces = tmp_path / "workspaces"
    workspace = workspaces / "default"
    sessions = workspace / "sessions"
    sessions.mkdir(parents=True)
    (workspace / "chats.json").write_text(
        json.dumps(
            {
                "version": 1,
                "chats": [
                    {
                        "user_id": "user-a",
                        "session_id": "chat:123",
                        "channel": "feishu",
                        "name": "migration test",
                    },
                ],
            },
        ),
        encoding="utf-8",
    )
    path = sessions / "user-a_chat--123.json"
    path.write_text(
        json.dumps(
            {
                "agent": {
                    "name": "default",
                    "memory": {
                        "content": [
                            _message(1, hours_ago=8),
                            _message(2, hours_ago=7),
                            _message(3, hours_ago=0.2),
                        ],
                        "_compressed_summary": "",
                    },
                    "toolkit": {},
                },
            },
        ),
        encoding="utf-8",
    )
    identity = discover_sessions(workspaces)[0]
    assert identity.exact is True
    assert identity.user_id == "user-a"
    assert identity.session_id == "chat:123"

    backup_root = tmp_path / "backups"
    result = migrate_session_file(
        identity,
        backup_root=backup_root,
        workspaces_root=workspaces,
        min_keep=1,
    )

    assert result.status == "migrated"
    assert result.compacted == 2
    assert result.ledger_appended == 3
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert len(migrated["agent"]["memory"]["content"]) == 1
    assert "decision 2" in migrated["agent"]["memory"]["_compressed_summary"]
    assert (backup_root / "default" / "sessions" / path.name).exists()

    store = get_workspace_memory_store(workspace, "user-a")
    archived = store.load_session(ledger_session_key("chat:123"))
    assert archived is not None
    assert len(archived["messages"]) == 3

    second = migrate_session_file(
        identity,
        backup_root=backup_root,
        workspaces_root=workspaces,
        min_keep=1,
    )
    assert second.compacted == 0
    assert (
        len(
            json.loads(path.read_text(encoding="utf-8"))["agent"]["memory"][
                "content"
            ],
        )
        == 1
    )
