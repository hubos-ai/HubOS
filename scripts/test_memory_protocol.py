#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sa5-2 验证：hubos.core.memory.base 协议契约。

  T1  LocalMemoryStore 满足 MemoryStore（核心契约）
  T2  LocalMemoryStore 满足 ArchivableMemoryStore（可选-归档）
  T3  LocalMemoryStore 满足 SummarizableMemoryStore（可选-摘要）
  T4  Mock 最小核心实现满足 MemoryStore，但不满足两个可选 Protocol
  T5  缺失方法的 broken 实现被 isinstance 正确拒掉
  T6  base.py 自身命名洁净度
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

SRC = Path("/Users/allen/HubOS/src")
sys.path.insert(0, str(SRC))


def step(t: str) -> None:
    print(f"\n=== {t} ===")


def main() -> int:
    tmp_root = Path(tempfile.mkdtemp(prefix="hubos-memory-proto-"))
    os.environ["HUBOS_MEMORY_ROOT"] = str(tmp_root)
    try:
        return _run()
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _run() -> int:
    from hubos.core.memory import (
        ArchivableMemoryStore,
        LocalMemoryStore,
        MemoryStore,
        SummarizableMemoryStore,
    )

    step("T1. LocalMemoryStore satisfies MemoryStore (core)")
    store = LocalMemoryStore()
    assert isinstance(
        store,
        MemoryStore,
    ), "LocalMemoryStore is not a MemoryStore"
    print("  isinstance(store, MemoryStore) = True")
    print("  T1 PASS")

    step("T2. LocalMemoryStore satisfies ArchivableMemoryStore (optional)")
    assert isinstance(
        store,
        ArchivableMemoryStore,
    ), "LocalMemoryStore lacks archive_session/auto_archive"
    print("  isinstance(store, ArchivableMemoryStore) = True")
    print("  T2 PASS")

    step("T3. LocalMemoryStore satisfies SummarizableMemoryStore (optional)")
    assert isinstance(
        store,
        SummarizableMemoryStore,
    ), "LocalMemoryStore lacks daily summary methods"
    print("  isinstance(store, SummarizableMemoryStore) = True")
    print("  T3 PASS")

    step("T4. Minimal mock implements ONLY MemoryStore — capabilities differ")

    class MinimalMockStore:
        """In-memory mock that implements only the core MemoryStore methods."""

        def __init__(self) -> None:
            self._sessions: Dict[str, Dict[str, Any]] = {}
            self._messages: Dict[str, List[Dict[str, Any]]] = {}
            self._tools: Dict[str, Dict[str, Dict[str, Any]]] = {}

        def create_session(
            self,
            session_id: str,
            metadata: Dict[str, Any],
        ) -> str:
            self._sessions[session_id] = dict(metadata)
            self._messages[session_id] = []
            self._tools[session_id] = {}
            return session_id

        def end_session(
            self,
            session_id: str,
            ended_at: str,
            end_reason: str,
        ) -> None:
            self._sessions[session_id]["ended_at"] = ended_at
            self._sessions[session_id]["end_reason"] = end_reason

        def update_metadata(
            self,
            session_id: str,
            metadata: Dict[str, Any],
        ) -> None:
            self._sessions[session_id] = dict(metadata)

        def append_message(
            self,
            session_id: str,
            message: Dict[str, Any],
        ) -> None:
            self._messages[session_id].append(dict(message))

        def save_tool_call(
            self,
            session_id: str,
            message_id: str,
            tool_call: Dict[str, Any],
        ) -> None:
            self._tools[session_id][message_id] = dict(tool_call)

        def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
            if session_id not in self._sessions:
                return None
            return {
                "metadata": self._sessions[session_id],
                "messages": self._messages[session_id],
            }

        def list_sessions(
            self,
            start_date: Optional[str] = None,
            end_date: Optional[str] = None,
        ) -> List[Dict[str, Any]]:
            return [
                {"session_id": sid, **meta}
                for sid, meta in self._sessions.items()
            ]

        def search_sessions(
            self,
            query: str,
            fields: Optional[List[str]] = None,
        ) -> List[Dict[str, Any]]:
            return [m for m in self._sessions.values() if query in str(m)]

        def search_messages(
            self,
            query: str,
            session_id: Optional[str] = None,
        ) -> List[Dict[str, Any]]:
            results: List[Dict[str, Any]] = []
            for sid, msgs in self._messages.items():
                if session_id and sid != session_id:
                    continue
                for m in msgs:
                    if query in str(m.get("content", "")):
                        results.append(m)
            return results

    mock = MinimalMockStore()
    assert isinstance(
        mock,
        MemoryStore,
    ), "MinimalMockStore failed core contract"
    assert not isinstance(
        mock,
        ArchivableMemoryStore,
    ), "MinimalMockStore should NOT satisfy archive protocol"
    assert not isinstance(
        mock,
        SummarizableMemoryStore,
    ), "MinimalMockStore should NOT satisfy summary protocol"
    # quick smoke: it does work as a MemoryStore
    mock.create_session(
        "sess_mock",
        {"started_at": "2026-04-20T00:00:00", "title": "mock"},
    )
    mock.append_message("sess_mock", {"role": "user", "content": "hi"})
    loaded = mock.load_session("sess_mock")
    assert loaded and loaded["messages"][0]["content"] == "hi"
    print("  isinstance(mock, MemoryStore) = True")
    print("  isinstance(mock, ArchivableMemoryStore) = False")
    print("  isinstance(mock, SummarizableMemoryStore) = False")
    print("  T4 PASS")

    step("T5. Broken implementation (missing required method) is rejected")

    class BrokenStore:
        def create_session(
            self,
            session_id: str,
            metadata: Dict[str, Any],
        ) -> str:
            return session_id

        # intentionally missing append_message / load_session / etc.

    assert not isinstance(
        BrokenStore(),
        MemoryStore,
    ), "BrokenStore wrongly accepted as MemoryStore"
    print("  BrokenStore correctly rejected by isinstance check")
    print("  T5 PASS")

    step("T6. base.py naming hygiene")
    base_text = (SRC / "hubos" / "core" / "memory" / "base.py").read_text(
        encoding="utf-8",
    )
    bad = [
        w
        for w in ("openclaw", "OpenClaw", "copaw", "CoPaw", "solo_hub")
        if w in base_text
    ]
    assert not bad, f"naming violations in base.py: {bad}"
    print("  base.py: no other-project names")
    print("  T6 PASS")

    print("\n========== ALL sa5-2 TESTS PASSED (6/6) ==========")
    return 0


if __name__ == "__main__":
    sys.exit(main())
