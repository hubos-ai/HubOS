# -*- coding: utf-8 -*-
"""Append-only long-term ledger scoped to one workspace user."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .local_store import LocalMemoryStore

_INTERNAL_STATUS_MARK = "hubos_internal_status"
_LARGE_TOOL_INPUT_CHARS = 1_000
_UNSAFE_TOOL_FILE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_STORE_CACHE: OrderedDict[str, LocalMemoryStore] = OrderedDict()
_STORE_CACHE_LOCK = threading.Lock()
_STORE_CACHE_MAX = 256


def workspace_memory_root(
    workspace_dir: str | Path,
    user_id: str = "",
) -> Path:
    """Return a hard-isolated memory root without exposing raw user IDs."""
    tenant = user_id.strip() or "_anonymous"
    tenant_hash = hashlib.sha256(tenant.encode("utf-8")).hexdigest()[:20]
    return Path(workspace_dir).expanduser() / ".memory" / "users" / tenant_hash


def ledger_session_key(session_id: str) -> str:
    """Map arbitrary channel session IDs to a portable directory name."""
    return (
        "session-"
        + hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    )


def get_workspace_memory_store(
    workspace_dir: str | Path,
    user_id: str = "",
) -> LocalMemoryStore:
    root = workspace_memory_root(workspace_dir, user_id).resolve()
    key = str(root)
    with _STORE_CACHE_LOCK:
        store = _STORE_CACHE.get(key)
        if store is None:
            store = LocalMemoryStore(root=root)
            _STORE_CACHE[key] = store
            if len(_STORE_CACHE) > _STORE_CACHE_MAX:
                _STORE_CACHE.popitem(last=False)
        else:
            _STORE_CACHE.move_to_end(key)
        return store


def _is_internal_marks(marks: Any) -> bool:
    if marks == _INTERNAL_STATUS_MARK:
        return True
    return isinstance(marks, (list, tuple, set)) and (
        _INTERNAL_STATUS_MARK in marks
    )


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _message_dict(msg: Any) -> dict[str, Any]:
    if isinstance(msg, dict):
        return _json_safe(dict(msg))
    to_dict = getattr(msg, "to_dict", None)
    if callable(to_dict):
        return _json_safe(to_dict())
    return {
        "id": str(getattr(msg, "id", "") or ""),
        "role": str(getattr(msg, "role", "") or ""),
        "name": str(getattr(msg, "name", "") or ""),
        "timestamp": str(getattr(msg, "timestamp", "") or ""),
        "content": _json_safe(getattr(msg, "content", "")),
    }


def _record_id(record: dict[str, Any]) -> str:
    existing = record.get("id") or record.get("message_id")
    if existing:
        return str(existing)
    canonical = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return "msg-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _tool_file_key(message_id: str, call_id: str) -> str:
    raw = f"{message_id}--{call_id}"
    cleaned = _UNSAFE_TOOL_FILE_RE.sub("_", raw).strip("._")
    if len(cleaned) <= 120:
        return cleaned
    return cleaned[:80] + "-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _compact_tool_block_for_ledger(
    block: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    clean = dict(block)
    clean.pop("raw_input", None)
    if clean.get("type") != "tool_use":
        return clean, None

    arguments = clean.get("input", {})
    serialized = json.dumps(arguments, ensure_ascii=False, default=str)
    tool_record = {
        "call_id": str(clean.get("id") or clean.get("tool_use_id") or ""),
        "tool_name": str(clean.get("name") or ""),
        "arguments": _json_safe(arguments),
    }
    if len(serialized) > _LARGE_TOOL_INPUT_CHARS:
        keys = list(arguments)[:20] if isinstance(arguments, dict) else []
        clean["input"] = {
            "_stored_separately": True,
            "chars": len(serialized),
            "keys": keys,
            "preview": serialized[:300],
        }
    return clean, tool_record


def persist_memory_to_ledger(
    *,
    memory: Any,
    workspace_dir: str | Path,
    session_id: str,
    user_id: str = "",
    channel: str = "",
    agent_id: str = "default",
    title: str = "",
) -> int:
    """Persist currently visible memory before it is compacted or replaced."""
    content: Iterable[Any] = getattr(memory, "content", []) or []
    records: list[dict[str, Any]] = []
    tool_records: list[tuple[str, dict[str, Any]]] = []
    summary_getter = getattr(memory, "get_compressed_summary", None)
    compressed_summary = summary_getter() if callable(summary_getter) else ""
    if compressed_summary:
        summary_text = str(compressed_summary)
        summary_id = (
            "summary-"
            + hashlib.sha256(
                summary_text.encode("utf-8"),
            ).hexdigest()[:24]
        )
        records.append(
            {
                "_ledger_id": summary_id,
                "_session_id": session_id,
                "id": summary_id,
                "role": "system",
                "name": "compressed_summary",
                "content": summary_text,
                "kind": "compressed_summary",
            },
        )

    for item in content:
        if isinstance(item, (list, tuple)) and item:
            msg = item[0]
            marks = item[1] if len(item) > 1 else None
        else:
            msg = item
            marks = None
        if _is_internal_marks(marks):
            continue

        record = _message_dict(msg)
        message_id = _record_id(record)
        record["_ledger_id"] = message_id
        record["_session_id"] = session_id
        blocks = record.get("content")
        if isinstance(blocks, list):
            compacted: list[Any] = []
            for block in blocks:
                if not isinstance(block, dict):
                    compacted.append(block)
                    continue
                clean, tool_record = _compact_tool_block_for_ledger(block)
                compacted.append(clean)
                if tool_record is not None:
                    call_id = tool_record.get("call_id") or "tool"
                    tool_record.update(
                        {
                            "message_id": message_id,
                            "timestamp": record.get("timestamp", ""),
                        },
                    )
                    tool_records.append(
                        (
                            _tool_file_key(message_id, str(call_id)),
                            tool_record,
                        ),
                    )
            record["content"] = compacted
        records.append(record)

    store = get_workspace_memory_store(workspace_dir, user_id)
    key = ledger_session_key(session_id)
    now = datetime.now(timezone.utc).isoformat()
    store.ensure_session(
        key,
        {
            "session_id": session_id,
            "user_id": user_id,
            "channel": channel,
            "agent_id": agent_id,
            "title": title[:200],
            "started_at": now,
            "updated_at": now,
        },
    )
    appended = store.append_messages_unique(key, records)
    for file_key, tool_record in tool_records:
        store.save_tool_call(key, file_key, tool_record)
    return appended


__all__ = [
    "get_workspace_memory_store",
    "ledger_session_key",
    "persist_memory_to_ledger",
    "workspace_memory_root",
]
