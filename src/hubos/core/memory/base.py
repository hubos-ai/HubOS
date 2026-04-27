# -*- coding: utf-8 -*-
"""MemoryStore contract (L4 long-term memory backends).

Three structural protocols, smallest-surface-first:

    MemoryStore               required CRUD + search every backend MUST do
    ArchivableMemoryStore     optional: cold storage of finished sessions
    SummarizableMemoryStore   optional: daily/period digest persistence

We use ``typing.Protocol(runtime_checkable=True)`` instead of an ABC so that:

    1. Existing implementations (e.g. :class:`LocalMemoryStore`) need no
       inheritance edit — they satisfy the contract structurally.
    2. ``isinstance(store, MemoryStore)`` works at runtime for capability
       discovery (e.g. the GM agent can probe whether long-term archive is
       supported before exposing the corresponding tool).
    3. Future remote backends (HTTP, vector DB, hosted memory service) can
       implement just the parts that map cleanly to their model.

Contracts intentionally use plain ``dict[str, Any]`` payloads (not Pydantic
models) so backends written in pure standard-library — like
:class:`LocalMemoryStore` — stay zero-dependency.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class MemoryStore(Protocol):
    """Minimal capability every long-term memory backend must satisfy.

    Lifecycle order is::

        create_session(sid, meta)
        ├─ append_message(sid, msg)              # 0..N times
        ├─ save_tool_call(sid, mid, payload)     # 0..N times
        └─ end_session(sid, ended_at, reason)    # exactly 0 or 1 time

    All write methods are expected to be idempotent on logical content
    (re-appending the same message line is allowed; backends MAY dedupe by
    ``message_id`` if present).
    """

    # ─── Lifecycle ─────────────────────────────────────────────────────

    def create_session(self, session_id: str, metadata: Dict[str, Any]) -> str:
        """Create a new session. Returns the session_id (echo)."""
        ...

    def end_session(
        self,
        session_id: str,
        ended_at: str,
        end_reason: str,
    ) -> None:
        """Mark a session ended. ``ended_at`` is ISO-8601."""
        ...

    def update_metadata(
        self,
        session_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        """Replace the session's metadata blob entirely."""
        ...

    # ─── Writes ────────────────────────────────────────────────────────

    def append_message(self, session_id: str, message: Dict[str, Any]) -> None:
        """Append one message record (role/content/etc.)."""
        ...

    def save_tool_call(
        self,
        session_id: str,
        message_id: str,
        tool_call: Dict[str, Any],
    ) -> None:
        """Persist one tool-call payload, keyed by ``message_id``."""
        ...

    # ─── Reads ─────────────────────────────────────────────────────────

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Return ``{"metadata": ..., "messages": [...]}`` or None if unknown.
        Backends with archival MAY transparently fall through to cold storage.
        """
        ...

    def list_sessions(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List session index records, optionally filtered by ISO date string."""
        ...

    def search_sessions(
        self,
        query: str,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Substring search across session-level fields (title/tags/topics …)."""
        ...

    def search_messages(
        self,
        query: str,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Substring search across message contents. Scoped to one session if
        ``session_id`` is given, otherwise all live sessions."""
        ...


@runtime_checkable
class ArchivableMemoryStore(MemoryStore, Protocol):
    """Optional capability: backend can move finished sessions to cold storage."""

    def archive_session(self, session_id: str) -> None:
        """Move one session to cold storage. Subsequent ``load_session`` MUST
        still find it (via fallback)."""
        ...

    def auto_archive(self) -> List[str]:
        """Sweep all stale sessions to cold storage. Returns ids archived."""
        ...


@runtime_checkable
class SummarizableMemoryStore(MemoryStore, Protocol):
    """Optional capability: backend persists daily/weekly digests."""

    def save_daily_summary(self, date: str, summary: str) -> None:
        """Persist a daily digest. ``date`` is ``YYYY-MM-DD``."""
        ...

    def get_daily_summary(self, date: str) -> Optional[str]:
        """Fetch a previously persisted daily digest."""
        ...

    def append_daily_summary_index(self, record: Dict[str, Any]) -> None:
        """Append one entry to the cross-day summary index."""
        ...


__all__ = [
    "MemoryStore",
    "ArchivableMemoryStore",
    "SummarizableMemoryStore",
]
