#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sa5-1 验证：hubos.core.memory.local_store 的端到端正确性。

使用 ``HUBOS_MEMORY_ROOT=<tmpdir>`` 把所有 IO 隔离到临时目录，避免污染
真实的 ``~/.hubos/memory/``。

测试矩阵：
  T1  create_session + append_message + load_session 往返
  T2  save_tool_call + 文件落盘
  T3  end_session + update_metadata
  T4  daily_summary 生成 + save 写文件
  T5  list_sessions / search_sessions / search_messages
  T6  归档：archive_session + load 从归档反向加载
  T7  HUBOS_MEMORY_ROOT 环境变量真实生效（隔离性）
  T8  无遗留的外部项目名称字符串（openclaw / copaw / solo_hub 等）
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))


def step(t: str) -> None:
    print(f"\n=== {t} ===")


def main() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="hubos-memory-test-"))
    print(f"Sandbox memory root: {tmp_root}")
    os.environ["HUBOS_MEMORY_ROOT"] = str(tmp_root)

    try:
        return _run(tmp_root)
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        print(f"\n(cleaned up {tmp_root})")


def _run(tmp_root: Path) -> int:
    from hubos.core.memory import (
        LocalMemoryStore,
        DailySummaryGenerator,
        get_memory_root,
    )

    step("T1. CRUD round-trip: create + append + load")
    assert (
        get_memory_root() == tmp_root
    ), f"env override not honored: {get_memory_root()}"
    store = LocalMemoryStore()
    assert store.root == tmp_root

    sid = "sess_e2e_01"
    today_iso = datetime.now().isoformat()
    store.create_session(
        sid,
        {
            "version": "1.0",
            "session_id": sid,
            "channel": "test",
            "agent_id": "gm",
            "user_id": "u-001",
            "started_at": today_iso,
            "title": "测试会话 - 项目集成",
            "tags": ["测试", "project-hubos"],
        },
    )
    store.append_message(sid, {"role": "user", "content": "你好，确认接入完成"})
    store.append_message(sid, {"role": "assistant", "content": "确认，集成已经完成"})

    loaded = store.load_session(sid)
    assert loaded is not None, "load_session returned None"
    assert (
        len(loaded["messages"]) == 2
    ), f"expected 2 messages, got {len(loaded['messages'])}"
    assert loaded["metadata"]["message_count"] == 2
    print(
        f"  loaded {len(loaded['messages'])} messages, metadata.message_count={loaded['metadata']['message_count']}",
    )
    print("  T1 PASS")

    step("T2. save_tool_call")
    store.save_tool_call(
        sid,
        "msg_001",
        {
            "call_id": "call_xyz",
            "message_id": "msg_001",
            "tool_name": "fake_search",
            "arguments": {"q": "hubos"},
            "result": {
                "status": "ok",
                "output": "found 0 results",
                "error": None,
            },
            "timestamp": today_iso,
            "duration_ms": 12,
        },
    )
    tool_file = tmp_root / "sessions" / sid / "tools" / "msg_001.json"
    assert tool_file.exists(), f"tool call file not written: {tool_file}"
    print(f"  tool call landed at {tool_file.relative_to(tmp_root)}")
    print("  T2 PASS")

    step("T3. update_metadata + end_session")
    store.update_metadata(
        sid,
        {**loaded["metadata"], "title": "测试会话(更新后) - 项目集成"},
    )
    store.end_session(
        sid,
        ended_at=datetime.now().isoformat(),
        end_reason="test_complete",
    )
    reread = json.loads(
        (tmp_root / "sessions" / sid / "metadata.json").read_text(
            encoding="utf-8",
        ),
    )
    assert "(更新后)" in reread["title"], reread["title"]
    assert reread["end_reason"] == "test_complete"
    assert "ended_at" in reread
    print(f"  metadata updated; end_reason={reread['end_reason']}")
    print("  T3 PASS")

    step("T4. DailySummaryGenerator generate + save")
    gen = DailySummaryGenerator(store)
    today_date = datetime.now().date().isoformat()
    summary = gen.generate(today_date)
    assert today_date in summary, "date heading missing"
    assert "## 会话统计" in summary
    assert "## 重要决策" in summary
    saved = gen.save(today_date)
    assert saved == summary
    assert (tmp_root / "daily" / f"{today_date}.md").exists()
    print(f"  daily/{today_date}.md written ({len(summary)} chars)")
    print("  T4 PASS")

    step("T5. list / search")
    listed = store.list_sessions()
    assert any(
        r["session_id"] == sid for r in listed
    ), f"session not in index: {listed}"
    found_by_title = store.search_sessions("测试会话")
    found_by_tag = store.search_sessions("project-hubos")
    print(
        f"  list_sessions={len(listed)}  search('测试会话')={len(found_by_title)}  search('project-hubos')={len(found_by_tag)}",
    )
    assert found_by_title and found_by_tag, "search returned empty"

    found_msgs = store.search_messages("集成已经完成")
    assert any(m["content"] == "确认，集成已经完成" for m in found_msgs), found_msgs
    print(f"  search_messages('集成已经完成')={len(found_msgs)}")
    print("  T5 PASS")

    step("T6. archive round-trip")
    # forge a stale started_at so auto_archive can pick it up
    sid_old = "sess_e2e_old"
    store.create_session(
        sid_old,
        {
            "version": "1.0",
            "session_id": sid_old,
            "channel": "test",
            "agent_id": "gm",
            "started_at": (datetime.now() - timedelta(days=60)).isoformat(),
            "title": "陈旧会话",
        },
    )
    store.append_message(sid_old, {"role": "user", "content": "归档目标"})
    archived = store.auto_archive()
    assert (
        sid_old in archived
    ), f"expected {sid_old} in archived, got {archived}"
    # session dir gone
    assert not (tmp_root / "sessions" / sid_old).exists()
    # archive file present
    archives = list((tmp_root / "archives").rglob(f"{sid_old}.json.gz"))
    assert archives, "no .json.gz produced"
    # load_session falls back to archive
    loaded_old = store.load_session(sid_old)
    assert (
        loaded_old is not None
        and loaded_old["metadata"]["session_id"] == sid_old
    )
    assert any(m.get("content") == "归档目标" for m in loaded_old["messages"])
    print(
        f"  archived to {archives[0].relative_to(tmp_root)}; reloaded {len(loaded_old['messages'])} msgs",
    )
    print("  T6 PASS")

    step("T7. env-driven sandbox (no writes leaked outside HUBOS_MEMORY_ROOT)")
    leaked_default = Path.home() / ".hubos" / "memory" / "sessions" / sid
    if leaked_default.exists():
        return _fail(f"data leaked to default root: {leaked_default}")
    print(f"  default root untouched: {Path.home() / '.hubos' / 'memory'}")
    print("  T7 PASS")

    step("T8. naming hygiene — no other-project names in source")
    pkg_dir = SRC / "hubos" / "core" / "memory"
    bad_words = [
        "openclaw",
        "OpenClaw",
        "openClaw",
        "copaw",
        "CoPaw",
        "solo_hub",
    ]
    hits = []
    for f in pkg_dir.rglob("*"):
        if f.is_file() and f.suffix in {".py", ".json", ".md"}:
            text = f.read_text(encoding="utf-8")
            for w in bad_words:
                if w in text:
                    hits.append(f"{f.relative_to(SRC)} contains {w!r}")
    if hits:
        return _fail("naming-hygiene violations:\n  - " + "\n  - ".join(hits))
    print(
        f"  scanned {sum(1 for _ in pkg_dir.rglob('*.py'))} .py + {sum(1 for _ in pkg_dir.rglob('*.json'))} .json files: clean",
    )
    print("  T8 PASS")

    print("\n========== ALL sa5-1 TESTS PASSED (8/8) ==========")
    return 0


def _fail(msg: str) -> int:
    print(f"\nFAIL: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
