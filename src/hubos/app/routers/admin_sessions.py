# -*- coding: utf-8 -*-
"""Admin session-view API — read-only cross-user inspection.

Endpoints
---------

    GET  /admin/sessions              list / filter / search sessions
    GET  /admin/sessions/{id}         full session (metadata + messages)
    GET  /admin/sessions/{id}/messages   paginated message tail

All endpoints require the caller to hold the ``admin`` role, as derived
from the current :class:`~hubos.core.infra.tenant_context.TenantContext`
bound by :class:`~hubos.app.tenant_middleware.TenantContextMiddleware`.
Non-admin callers get HTTP 403 with a structured payload; unknown
session ids get HTTP 404. Because the admin view is deliberately
cross-user, handlers do **not** apply the per-user filter that regular
memory-recall tools do — that is the whole point of this surface.

Data source
-----------

The authoritative source for live conversations is the host app's
per-agent workspace layout::

    WORKING_DIR/workspaces/<agent_id>/chats.json         → ChatSpec list
    WORKING_DIR/workspaces/<agent_id>/sessions/*.json    → session state
                                                          (agentscope memory)

The admin surface scans all agent workspaces, merges chats, and loads
the matching session state file for the detail endpoint. Internal
workflow chats produced by ``coordinate_workflow`` (channel
``hubos_core_workflow``) are hidden from the default list to avoid
drowning the admin in sub-agent bookkeeping.

The :class:`~hubos.core.memory.local_store.LocalMemoryStore` (L4) is
still consulted as a fallback for sessions that may have been archived
or synthesised outside the chat pipeline (e.g., headless runs), keeping
the L4 surface useful without depending on it as primary truth.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from hubos.core.infra.rbac import ForbiddenError, ensure_roles
from hubos.core.infra.tenant_context import current_user_id
from hubos.core.memory.local_store import LocalMemoryStore

from ...constant import WORKING_DIR

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/admin", tags=["admin"])


# --------------------------------------------------------------------------
# L4 store access — retained for archived / synthesised sessions. The chat
# pipeline does NOT write here today; the primary source is the workspace
# scanner below.
# --------------------------------------------------------------------------
_store: LocalMemoryStore | None = None


def _get_store() -> LocalMemoryStore:
    global _store
    if _store is None:
        _store = LocalMemoryStore()
    return _store


def reset_store_singleton_for_tests() -> None:
    """Drop the memoised store so a test can flip HUBOS_MEMORY_ROOT
    between runs without a module reload."""
    global _store
    _store = None


# --------------------------------------------------------------------------
# Workspace scanner — walks `WORKING_DIR/workspaces/*/chats.json` and the
# associated `sessions/*.json` state files. Cheap enough (O(agents * chats))
# for admin-only use; if it ever needs to scale we can index into L4 on the
# write path and drop this.
# --------------------------------------------------------------------------

# Internal channel used by `coordinate_workflow` to emit per-step sub-sessions.
# These are plumbing, not user-facing conversations — hide them from the
# default admin list but still allow direct lookup by session_id.
_INTERNAL_WORKFLOW_CHANNEL = "hubos_core_workflow"


def _workspaces_root() -> Path:
    return Path(WORKING_DIR).expanduser() / "workspaces"


def _load_chats_file(path: Path) -> list[dict[str, Any]]:
    """Return the raw `chats` list from a workspace's chats.json, or []."""
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as exc:  # pragma: no cover - corrupt file
        logger.warning("admin_sessions: failed to parse %s: %s", path, exc)
        return []
    chats = data.get("chats") if isinstance(data, dict) else None
    return chats if isinstance(chats, list) else []


def _iso(dt: Any) -> str:
    """Best-effort ISO-8601 string for datetime-ish input."""
    if isinstance(dt, str):
        return dt
    if isinstance(dt, datetime):
        return dt.astimezone(timezone.utc).isoformat()
    return ""


def _session_state_path(agent_id: str, user_id: str, session_id: str) -> Path:
    """Resolve the per-session JSON state file path.

    Mirrors :func:`hubos.app.runner.session.SafeJSONSession._get_save_path`
    so the admin surface reads exactly what the runner wrote.
    """
    import re

    unsafe = re.compile(r'[\\/:*?"<>|]')
    safe_sid = unsafe.sub("--", session_id or "")
    safe_uid = unsafe.sub("--", user_id or "")
    fname = f"{safe_uid}_{safe_sid}.json" if safe_uid else f"{safe_sid}.json"
    return _workspaces_root() / agent_id / "sessions" / fname


def _load_session_messages(
    agent_id: str,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Load and normalise the message list for a (agent, user, session).

    The on-disk shape is ``{"agent": {"memory": {"content": [[msg, [...]], ...]}}}``
    where each entry is ``[Msg_dict, tool_responses_list]``. We flatten to
    the flat list of Msg dicts the admin UI consumes, preserving the tool
    responses inline as additional synthetic messages when present.
    """
    path = _session_state_path(agent_id, user_id, session_id)
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8") as f:
            state = json.load(f)
    except Exception as exc:  # pragma: no cover - corrupt file
        logger.warning(
            "admin_sessions: failed to parse session state %s: %s",
            path,
            exc,
        )
        return []

    memory = ((state or {}).get("agent") or {}).get("memory") or {}
    content = memory.get("content") if isinstance(memory, dict) else None
    if not isinstance(content, list):
        return []

    out: list[dict[str, Any]] = []
    for entry in content:
        # Each entry is typically [Msg_dict, tool_responses_list] or just
        # Msg_dict for simpler memory backends.
        if isinstance(entry, list) and entry:
            msg = entry[0]
            tool_resps = entry[1] if len(entry) > 1 else []
        else:
            msg = entry
            tool_resps = []

        if isinstance(msg, dict):
            out.append(msg)
        if isinstance(tool_resps, list):
            for tr in tool_resps:
                if isinstance(tr, dict):
                    out.append(tr)
    return out


def _infer_started(
    chat: dict[str, Any],
    messages: list[dict[str, Any]],
) -> str:
    """Pick the best 'started' timestamp for the summary row."""
    created = chat.get("created_at")
    if created:
        return _iso(created)
    for m in messages:
        ts = m.get("timestamp")
        if ts:
            return str(ts)
    return ""


def _summarise_chat(
    agent_id: str,
    chat: dict[str, Any],
    *,
    load_messages: bool,
) -> dict[str, Any]:
    user_id = str(chat.get("user_id") or "")
    session_id = str(chat.get("session_id") or "")
    # Stable public id: prefer the ChatSpec UUID so the UI can pass it back
    # verbatim; fall back to session_id when missing (e.g. legacy rows).
    public_id = str(chat.get("id") or session_id)

    messages: list[dict[str, Any]] = []
    if load_messages:
        messages = _load_session_messages(agent_id, user_id, session_id)

    started = _infer_started(chat, messages)
    updated = _iso(chat.get("updated_at")) or started

    return {
        "session_id": public_id,
        # Raw fields required by the admin list UI.
        "title": chat.get("name") or "Untitled",
        "started": started,
        "updated": updated,
        "agent_id": agent_id,
        "agent": agent_id,
        "channel": chat.get("channel") or "",
        "user_id": user_id or None,
        "status": chat.get("status") or "idle",
        "msg_count": len(messages) if load_messages else None,
        # Internal fields the detail endpoint uses to locate state on disk.
        "_raw_session_id": session_id,
        "_agent_id": agent_id,
        "_user_id": user_id,
    }


def _scan_workspaces(*, load_message_counts: bool) -> list[dict[str, Any]]:
    """Scan all agent workspaces and return flattened session summaries."""
    root = _workspaces_root()
    if not root.is_dir():
        return []

    rows: list[dict[str, Any]] = []
    for agent_dir in sorted(root.iterdir()):
        if not agent_dir.is_dir():
            continue
        chats_file = agent_dir / "chats.json"
        for chat in _load_chats_file(chats_file):
            if not isinstance(chat, dict):
                continue
            rows.append(
                _summarise_chat(
                    agent_dir.name,
                    chat,
                    load_messages=load_message_counts,
                ),
            )
    return rows


def _find_summary_by_id(
    session_id: str,
) -> Optional[tuple[str, str, str, dict[str, Any]]]:
    """Locate (agent_id, user_id, raw_session_id, chat_dict) by public id.

    The caller may pass either the ChatSpec UUID (preferred) or the raw
    ``session_id`` for legacy consumers; both are matched.
    """
    root = _workspaces_root()
    if not root.is_dir():
        return None
    for agent_dir in sorted(root.iterdir()):
        if not agent_dir.is_dir():
            continue
        for chat in _load_chats_file(agent_dir / "chats.json"):
            if not isinstance(chat, dict):
                continue
            if (
                str(chat.get("id") or "") == session_id
                or str(chat.get("session_id") or "") == session_id
            ):
                return (
                    agent_dir.name,
                    str(chat.get("user_id") or ""),
                    str(chat.get("session_id") or ""),
                    chat,
                )
    return None


# --------------------------------------------------------------------------
# RBAC guard — translate hubos.core's ForbiddenError into an HTTP 403 with a
# stable, parseable body. Kept local to this router so the admin surface
# owns its own HTTP contract.
# --------------------------------------------------------------------------


def _require_admin() -> None:
    try:
        ensure_roles("admin")
    except ForbiddenError as e:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "required_roles": list(e.required),
                "mode": e.mode,
                "held_roles": list(e.held),
                "user_id": e.user_id,
            },
        ) from e


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


def _apply_filters(
    rows: list[dict[str, Any]],
    *,
    query: Optional[str],
    user_id_filter: Optional[str],
    agent_id_filter: Optional[str],
    channel_filter: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
    include_internal: bool,
) -> list[dict[str, Any]]:
    def _match(r: dict[str, Any]) -> bool:
        if not include_internal and (
            r.get("channel") == _INTERNAL_WORKFLOW_CHANNEL
        ):
            return False
        if user_id_filter and r.get("user_id") != user_id_filter:
            return False
        if agent_id_filter and r.get("agent_id") != agent_id_filter:
            return False
        if channel_filter and r.get("channel") != channel_filter:
            return False
        started = str(r.get("started", ""))
        if start_date and started and started < start_date:
            return False
        if end_date and started and started > end_date:
            return False
        if query:
            ql = query.lower()
            title = str(r.get("title", "")).lower()
            tags = " ".join(str(t) for t in r.get("tags", []) or []).lower()
            topics = " ".join(
                str(t) for t in r.get("topics", []) or []
            ).lower()
            if ql not in title and ql not in tags and ql not in topics:
                return False
        return True

    return [r for r in rows if _match(r)]


def _strip_internal_keys(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if not k.startswith("_")}


@router.get("/sessions", summary="List all sessions (admin only)")
async def list_sessions(
    q: Optional[str] = Query(
        None,
        description="Substring match against title/tags/topics.",
        max_length=256,
    ),
    start_date: Optional[str] = Query(
        None,
        description="ISO-8601 lower bound on 'started' (inclusive).",
    ),
    end_date: Optional[str] = Query(
        None,
        description="ISO-8601 upper bound on 'started' (inclusive).",
    ),
    user_id: Optional[str] = Query(
        None,
        description="Filter by owning principal; admin-only field.",
    ),
    agent_id: Optional[str] = Query(None, description="Filter by agent."),
    channel: Optional[str] = Query(None, description="Filter by channel."),
    include_internal: bool = Query(
        False,
        description=(
            "Include hubos.core internal workflow sub-sessions "
            "(channel=hubos_core_workflow). Off by default."
        ),
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Admin cross-user session listing.

    Response shape::

        {
          "total": <int>,      # total matching rows (pre-pagination)
          "limit": <int>,
          "offset": <int>,
          "sessions": [ {session_id, title, started, user_id, ...}, ... ]
        }
    """
    _require_admin()

    rows = _scan_workspaces(load_message_counts=True)

    # Merge in any L4-only entries (archived / synthetic). Dedup by
    # session_id so the workspace copy wins when both sources have the row.
    try:
        l4_rows = _get_store().list_sessions(
            start_date=start_date,
            end_date=end_date,
        )
    except Exception as exc:  # pragma: no cover - L4 is best-effort here
        logger.debug("admin_sessions: L4 list_sessions failed: %s", exc)
        l4_rows = []

    seen_ids = {r["session_id"] for r in rows}
    for r in l4_rows:
        sid = str(r.get("session_id", "")) or str(r.get("id", ""))
        if not sid or sid in seen_ids:
            continue
        normalised = dict(r)
        normalised.setdefault("session_id", sid)
        normalised.setdefault("title", r.get("title") or sid)
        normalised.setdefault("started", r.get("started") or "")
        normalised.setdefault("agent_id", r.get("agent") or r.get("agent_id"))
        normalised.setdefault("agent", r.get("agent") or r.get("agent_id"))
        normalised.setdefault("channel", r.get("channel") or "")
        normalised.setdefault("user_id", r.get("user_id"))
        normalised.setdefault("msg_count", r.get("msg_count"))
        rows.append(normalised)
        seen_ids.add(sid)

    filtered = _apply_filters(
        rows,
        query=q,
        user_id_filter=user_id,
        agent_id_filter=agent_id,
        channel_filter=channel,
        start_date=start_date,
        end_date=end_date,
        include_internal=include_internal,
    )
    # Most recent first by 'started' (falling back to 'updated').
    filtered.sort(
        key=lambda r: (str(r.get("started") or r.get("updated") or "")),
        reverse=True,
    )

    page = [_strip_internal_keys(r) for r in filtered[offset : offset + limit]]

    logger.info(
        "admin list_sessions caller=%r total=%d page=%d..%d q=%r "
        "user_filter=%r agent_filter=%r",
        current_user_id(),
        len(filtered),
        offset,
        offset + len(page),
        q,
        user_id,
        agent_id,
    )

    return {
        "total": len(filtered),
        "limit": limit,
        "offset": offset,
        "sessions": page,
    }


def _build_detail_from_workspace(
    session_id: str,
    last_n: int | None = None,
) -> Optional[dict[str, Any]]:
    hit = _find_summary_by_id(session_id)
    if hit is None:
        return None
    agent_id, user_id, raw_session_id, chat = hit
    messages = _load_session_messages(agent_id, user_id, raw_session_id)
    total = len(messages)
    if last_n is not None and total > last_n:
        tail = messages[-last_n:]
        truncated = True
    else:
        tail = messages
        truncated = False

    metadata = {
        "title": chat.get("name") or "Untitled",
        "agent_id": agent_id,
        "agent": agent_id,
        "channel": chat.get("channel") or "",
        "user_id": user_id or None,
        "status": chat.get("status") or "idle",
        "started_at": _iso(chat.get("created_at")),
        "updated_at": _iso(chat.get("updated_at")),
        "meta": chat.get("meta") or {},
        "raw_session_id": raw_session_id,
    }
    return {
        "session_id": session_id,
        "metadata": metadata,
        "messages": tail,
        "truncated": truncated,
        "total_messages": total,
    }


@router.get(
    "/sessions/{session_id}",
    summary="Fetch one session's metadata + messages (admin only)",
)
async def get_session(
    session_id: str,
    last_n: int = Query(
        200,
        ge=1,
        le=2000,
        description="Return the most recent N messages. Full transcripts "
        "beyond this window should use /messages with offset pagination.",
    ),
) -> dict[str, Any]:
    _require_admin()

    sid = session_id.strip()
    ws_detail = _build_detail_from_workspace(sid, last_n=last_n)
    if ws_detail is not None:
        logger.info(
            "admin get_session (workspace) caller=%r session=%r owner=%r "
            "messages=%d truncated=%s",
            current_user_id(),
            sid,
            ws_detail["metadata"].get("user_id"),
            ws_detail["total_messages"],
            ws_detail["truncated"],
        )
        return ws_detail

    loaded = _get_store().load_session(sid)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "session_id": session_id},
        )

    metadata = loaded.get("metadata") or {}
    messages = loaded.get("messages") or []
    total = len(messages)
    truncated = total > last_n
    tail = messages[-last_n:] if truncated else messages

    logger.info(
        "admin get_session (L4) caller=%r session=%r owner=%r messages=%d "
        "truncated=%s",
        current_user_id(),
        sid,
        metadata.get("user_id"),
        total,
        truncated,
    )

    return {
        "session_id": sid,
        "metadata": metadata,
        "messages": tail,
        "truncated": truncated,
        "total_messages": total,
    }


@router.get(
    "/sessions/{session_id}/messages",
    summary="Paginated message window (admin only)",
)
async def get_session_messages(
    session_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    _require_admin()

    sid = session_id.strip()
    ws_detail = _build_detail_from_workspace(sid, last_n=None)
    if ws_detail is not None:
        all_msgs = ws_detail["messages"]
        total = len(all_msgs)
        window = all_msgs[offset : offset + limit]
        return {
            "session_id": sid,
            "offset": offset,
            "limit": limit,
            "total": total,
            "messages": window,
        }

    loaded = _get_store().load_session(sid)
    if loaded is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "session_id": session_id},
        )

    all_msgs = loaded.get("messages") or []
    total = len(all_msgs)
    window = all_msgs[offset : offset + limit]

    return {
        "session_id": sid,
        "offset": offset,
        "limit": limit,
        "total": total,
        "messages": window,
    }
