# -*- coding: utf-8 -*-
"""Long-term / cross-session memory recall tools for the General Manager agent.

Two tools:

* ``recall_long_term(query, top_k)``   — fuzzy search across ALL past sessions
  (titles, tags, message bodies). Used when the user references something
  vague like "remember when we talked about X last week".

* ``recall_session(session_id, last_n)`` — load a specific past session's
  messages (most recent ``last_n``). Used when the GM already knows the
  session_id (e.g. surfaced by recall_long_term) and wants the full text.

Backed by ``hubos.core.memory.LocalMemoryStore`` (the file-based L4 layer
defined in ``docs/architecture-memory-layers.md``). Multi-user isolation is
best-effort via the ``user_id`` filter from the current request context;
hard tenant isolation is Stage C.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from hubos.core.memory import LocalMemoryStore

from .runtime_delegate import _current_runtime_ctx

logger = logging.getLogger(__name__)


_DEFAULT_TOP_K = 10
_MAX_TOP_K = 50
_DEFAULT_LAST_N = 20
_MAX_LAST_N = 200


def _err(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


def _ok(text: str) -> ToolResponse:
    return ToolResponse(content=[TextBlock(type="text", text=text)])


_store_singleton: LocalMemoryStore | None = None


def _get_store() -> LocalMemoryStore:
    """Lazy singleton; respects ``HUBOS_MEMORY_ROOT`` env var via the store
    constructor."""
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = LocalMemoryStore()
        logger.info(
            "memory_recall: LocalMemoryStore initialized at %s",
            _store_singleton.root,
        )
    return _store_singleton


def _filter_by_user(
    records: list[dict[str, Any]],
    user_id: str,
) -> list[dict[str, Any]]:
    """Best-effort user_id filter. Records without user_id are kept (legacy
    sessions); records with a different user_id are dropped."""
    if not user_id:
        return records
    out = []
    for r in records:
        rec_user = r.get("user_id") or r.get("user") or ""
        if not rec_user or rec_user == user_id:
            out.append(r)
    return out


async def recall_long_term(
    query: str,
    top_k: int = _DEFAULT_TOP_K,
    include_messages: bool = True,
) -> ToolResponse:
    """Cross-session fuzzy search over the long-term memory store.

    Use when the user refers to a past topic / decision / fact whose
    session_id you do NOT already have. Searches both session titles/tags
    AND (if ``include_messages``) message bodies.

    DO NOT use for the CURRENT chat history — that's already in your
    short-term context, just read it. DO NOT use for trivia the model
    already knows — call this only for things that came from THIS user's
    own history.

    Args:
        query: free-form search string. Required.
        top_k: cap on returned hits. Default 10, max 50.
        include_messages: if True (default), also search message bodies.
            Set False to only match session titles/tags (faster).

    Returns:
        ``ToolResponse`` with JSON payload::

            {
              "query": str,
              "scope_user_id": str,
              "session_hits": [{"session_id": ..., "title": ..., "started": ...,
                                "agent": ..., "tags": [...]}, ...],
              "message_hits": [{"session_id": ..., "role": ..., "timestamp": ...,
                                "snippet": ...}, ...],
              "total": <int>
            }
    """
    if not query or not query.strip():
        return _err("recall_long_term: query cannot be empty")

    top_k = max(1, min(int(top_k), _MAX_TOP_K))
    ctx = _current_runtime_ctx()
    user_id = ctx.get("user_id") or ""

    try:
        store = _get_store()
    except Exception as e:  # noqa: BLE001
        return _err(
            f"recall_long_term: memory store unavailable: {type(e).__name__}: {e}",
        )

    try:
        session_hits_raw = store.search_sessions(query.strip())
        session_hits_raw = _filter_by_user(session_hits_raw, user_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("recall_long_term: search_sessions failed")
        return _err(
            f"recall_long_term: search_sessions failed: {type(e).__name__}: {e}",
        )

    message_hits_raw: list[dict[str, Any]] = []
    if include_messages:
        try:
            message_hits_raw = store.search_messages(query.strip())
        except Exception as e:  # noqa: BLE001
            logger.exception("recall_long_term: search_messages failed")
            return _err(
                f"recall_long_term: search_messages failed: {type(e).__name__}: {e}",
            )

    session_hits = [
        {
            "session_id": r.get("session_id"),
            "title": r.get("title"),
            "started": r.get("started"),
            "agent": r.get("agent"),
            "tags": r.get("tags") or [],
            "msg_count": r.get("msg_count"),
        }
        for r in session_hits_raw[:top_k]
    ]

    message_hits: list[dict[str, Any]] = []
    for m in message_hits_raw:
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict)
            )
        snippet = (
            (str(content)[:200] + "…")
            if len(str(content)) > 200
            else str(content)
        )
        message_hits.append(
            {
                "session_id": m.get("_session_id"),
                "role": m.get("role"),
                "timestamp": m.get("timestamp"),
                "snippet": snippet,
            },
        )
    message_hits = message_hits[:top_k]

    payload = {
        "query": query.strip(),
        "scope_user_id": user_id or None,
        "session_hits": session_hits,
        "message_hits": message_hits,
        "total": len(session_hits) + len(message_hits),
    }

    if payload["total"] == 0:
        # Friendlier text-only response so the LLM can tell the user
        return _ok(
            f"recall_long_term: no matches for {query!r} "
            f"(user_id={user_id or 'global'}). Memory store is empty or "
            f"this topic was never discussed before.",
        )

    return _ok(json.dumps(payload, ensure_ascii=False, indent=2))


async def recall_session(
    session_id: str,
    last_n: int = _DEFAULT_LAST_N,
) -> ToolResponse:
    """Load the messages of a specific past session.

    Use AFTER ``recall_long_term`` (or when the user references a session
    by ID) to get the actual conversation back. Returns the most recent
    ``last_n`` messages + the session's metadata.

    Args:
        session_id: the session to load.
        last_n: number of most-recent messages to return. Default 20, max 200.

    Returns:
        ``ToolResponse`` with JSON payload::

            {
              "session_id": str,
              "found": bool,
              "metadata": {...} | null,
              "messages": [...] | null,
              "truncated": bool,
              "total_messages": <int>
            }
    """
    if not session_id or not session_id.strip():
        return _err("recall_session: session_id cannot be empty")

    last_n = max(1, min(int(last_n), _MAX_LAST_N))

    try:
        store = _get_store()
    except Exception as e:  # noqa: BLE001
        return _err(
            f"recall_session: memory store unavailable: {type(e).__name__}: {e}",
        )

    try:
        loaded = store.load_session(session_id.strip())
    except Exception as e:  # noqa: BLE001
        logger.exception("recall_session: load_session failed")
        return _err(
            f"recall_session: load_session failed: {type(e).__name__}: {e}",
        )

    if loaded is None:
        return _ok(
            json.dumps(
                {
                    "session_id": session_id,
                    "found": False,
                    "metadata": None,
                    "messages": None,
                    "truncated": False,
                    "total_messages": 0,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )

    metadata = loaded.get("metadata") or {}
    messages = loaded.get("messages") or []

    ctx = _current_runtime_ctx()
    user_id = ctx.get("user_id") or ""
    md_user = metadata.get("user_id") or metadata.get("user") or ""
    if user_id and md_user and md_user != user_id:
        return _err(
            f"recall_session: session {session_id!r} belongs to a different user "
            f"({md_user!r}); access denied.",
        )

    total = len(messages)
    truncated = total > last_n
    tail = messages[-last_n:] if truncated else messages

    return _ok(
        json.dumps(
            {
                "session_id": session_id,
                "found": True,
                "metadata": metadata,
                "messages": tail,
                "truncated": truncated,
                "total_messages": total,
            },
            ensure_ascii=False,
            indent=2,
        ),
    )


__all__ = ["recall_long_term", "recall_session"]
