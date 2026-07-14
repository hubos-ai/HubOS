# -*- coding: utf-8 -*-
"""Offline migration helpers for legacy JSON conversation sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from .workspace_ledger import persist_memory_to_ledger

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')
_FEISHU_USER_RE = re.compile(r"^(ou_[0-9a-fA-F]{32})_(.+)$")
_INTERNAL_STATUS_MARK = "hubos_internal_status"
_SUMMARY_MAX_CHARS = 14_000
_EXCERPT_MAX_CHARS = 700


@dataclass(frozen=True)
class SessionIdentity:
    """Resolved identity for one JSON session file."""

    path: Path
    workspace_dir: Path
    user_id: str
    session_id: str
    channel: str
    agent_id: str
    title: str = ""
    exact: bool = False


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of migrating one session file."""

    path: str
    status: str
    user_id: str
    session_id: str
    channel: str
    messages: int = 0
    ledger_appended: int = 0
    compacted: int = 0
    tool_payloads_compacted: int = 0
    kept: int = 0
    summary_chars: int = 0
    reason: str = ""


class _JSONMemory:
    """Small adapter accepted by ``persist_memory_to_ledger``."""

    def __init__(self, state: dict[str, Any]):
        self.content = state.get("content", [])
        self._summary = str(state.get("_compressed_summary") or "")

    def get_compressed_summary(self) -> str:
        return self._summary


def _sanitize_filename(value: str) -> str:
    return _UNSAFE_FILENAME_RE.sub("--", value)


def _message_from_item(item: Any) -> Any:
    if isinstance(item, (list, tuple)) and item:
        return item[0]
    return item


def _marks_from_item(item: Any) -> Any:
    if isinstance(item, (list, tuple)) and len(item) > 1:
        return item[1]
    return []


def _message_value(message: Any, key: str, default: Any = None) -> Any:
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _message_blocks(message: Any) -> list[Any]:
    content = _message_value(message, "content", [])
    return content if isinstance(content, list) else []


def _is_internal(item: Any) -> bool:
    marks = _marks_from_item(item)
    if marks == _INTERNAL_STATUS_MARK:
        return True
    return (
        isinstance(marks, (list, tuple, set))
        and _INTERNAL_STATUS_MARK in marks
    )


def _is_protected_system_message(message: Any) -> bool:
    if _message_value(message, "role") != "system":
        return False
    return not any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for block in _message_blocks(message)
    )


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _message_text(message: Any) -> str:
    snippets: list[str] = []
    for block in _message_blocks(message):
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text") or "").strip()
            if text:
                snippets.append(text)
        elif block_type == "tool_use":
            name = str(block.get("name") or "tool")
            arguments = block.get("input")
            keys = list(arguments)[:8] if isinstance(arguments, dict) else []
            suffix = f" ({', '.join(map(str, keys))})" if keys else ""
            snippets.append(f"[调用工具 {name}{suffix}]")
    if not snippets:
        content = _message_value(message, "content", "")
        if isinstance(content, str):
            snippets.append(content)
    return re.sub(r"\s+", " ", " ".join(snippets)).strip()


def build_extractive_summary(
    messages: Iterable[Any],
    *,
    previous_summary: str = "",
    max_chars: int = _SUMMARY_MAX_CHARS,
) -> str:
    """Build a bounded local summary without making an LLM request.

    The full source messages are persisted to the workspace ledger before this
    summary replaces them, so the digest only needs to preserve continuity and
    useful search anchors.
    """
    material = list(messages)
    timestamps = [
        parsed
        for parsed in (
            _parse_timestamp(
                _message_value(_message_from_item(item), "timestamp"),
            )
            for item in material
        )
        if parsed is not None
    ]
    date_range = "未知"
    if timestamps:
        date_range = f"{min(timestamps):%Y-%m-%d %H:%M} 至 {max(timestamps):%Y-%m-%d %H:%M}"

    header = (
        "# 历史会话压缩摘要\n"
        f"- 已归档消息：{len(material)} 条\n"
        f"- 时间范围：{date_range}\n"
        "- 完整原文已保存到当前工作区的用户隔离长期记忆账本，可按需召回。\n"
    )
    parts = [header]
    previous = previous_summary.strip()
    if previous:
        previous_budget = min(max_chars // 2, 6_000)
        parts.append("\n## 既有摘要\n" + previous[:previous_budget] + "\n")

    entries: list[str] = []
    for item in material:
        if _is_internal(item):
            continue
        message = _message_from_item(item)
        text = _message_text(message)
        if not text:
            continue
        role = str(
            _message_value(message, "role")
            or _message_value(message, "name")
            or "message",
        )
        role_label = {"user": "用户", "assistant": "助手", "system": "系统"}.get(
            role,
            role,
        )
        timestamp = str(_message_value(message, "timestamp", "") or "")[:16]
        prefix = (
            f"- [{timestamp}] {role_label}: "
            if timestamp
            else f"- {role_label}: "
        )
        entries.append(prefix + text[:_EXCERPT_MAX_CHARS])

    excerpt_header = "\n## 最近关键对话摘录\n"
    fixed_chars = len("".join(parts)) + len(excerpt_header)
    available = max(0, max_chars - fixed_chars)
    selected: list[str] = []
    used = 0
    for entry in reversed(entries):
        cost = len(entry) + 1
        if selected and used + cost > available:
            break
        if cost > available:
            entry = entry[: max(0, available - 1)]
            cost = len(entry) + 1
        if entry:
            selected.append(entry)
            used += cost
        if used >= available:
            break
    if selected:
        parts.append(excerpt_header + "\n".join(reversed(selected)))

    return "".join(parts)[:max_chars].strip()


def _tool_pair_keep_indices(content: list[Any], always_keep: set[int]) -> None:
    tool_uses: dict[str, int] = {}
    tool_results: dict[str, int] = {}
    for index, item in enumerate(content):
        for block in _message_blocks(_message_from_item(item)):
            if not isinstance(block, dict):
                continue
            call_id = block.get("id") or block.get("tool_use_id")
            if not call_id:
                continue
            if block.get("type") == "tool_use":
                tool_uses[str(call_id)] = index
            elif block.get("type") == "tool_result":
                tool_results[str(call_id)] = index
    pairs = []
    for call_id in set(tool_uses) | set(tool_results):
        pair = {tool_uses.get(call_id), tool_results.get(call_id)}
        pair.discard(None)
        pairs.append(pair)
    changed = True
    while changed:
        changed = False
        for pair in pairs:
            if pair & always_keep and not pair <= always_keep:
                always_keep.update(pair)
                changed = True


def compact_memory_state_locally(
    memory_state: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_hours: float = 2.0,
    min_keep: int = 10,
) -> tuple[int, int]:
    """Compact stale JSON memory in place without using a model."""
    content = memory_state.get("content")
    if not isinstance(content, list):
        return 0, 0
    candidate_indices = [
        index
        for index, item in enumerate(content)
        if not _is_protected_system_message(_message_from_item(item))
    ]
    if len(candidate_indices) <= min_keep:
        return 0, len(content)

    always_keep = set(candidate_indices[-min_keep:])
    _tool_pair_keep_indices(content, always_keep)
    cutoff = (now or datetime.now()) - timedelta(hours=max_age_hours)
    stale_indices: list[int] = []
    for index, item in enumerate(content):
        if index in always_keep:
            continue
        message = _message_from_item(item)
        if _is_protected_system_message(message):
            continue
        timestamp = _parse_timestamp(_message_value(message, "timestamp"))
        if timestamp is not None and timestamp < cutoff:
            stale_indices.append(index)

    if not stale_indices:
        return 0, len(content)
    stale_set = set(stale_indices)
    stale_items = [content[index] for index in stale_indices]
    memory_state["_compressed_summary"] = build_extractive_summary(
        stale_items,
        previous_summary=str(memory_state.get("_compressed_summary") or ""),
    )
    memory_state["content"] = [
        item for index, item in enumerate(content) if index not in stale_set
    ]
    return len(stale_items), len(memory_state["content"])


def compact_tool_payloads_in_state(
    memory_state: dict[str, Any],
    *,
    workspace_dir: Path,
    session_id: str,
    dry_run: bool = False,
    input_threshold: int = 2_000,
    output_threshold: int = 3_000,
) -> int:
    """Archive retained completed tool payloads and leave bounded pointers."""
    from ..tool_output_archive import (
        archive_tool_output,
        compact_completed_tool_inputs,
    )

    content = memory_state.get("content", [])
    messages = [_message_from_item(item) for item in content]
    if dry_run:
        completed_ids = {
            str(block.get("id") or block.get("tool_use_id"))
            for message in messages
            for block in _message_blocks(message)
            if isinstance(block, dict)
            and block.get("type") == "tool_result"
            and (block.get("id") or block.get("tool_use_id"))
        }
        count = 0
        for message in messages:
            for block in _message_blocks(message):
                if not isinstance(block, dict):
                    continue
                call_id = str(
                    block.get("id") or block.get("tool_use_id") or "",
                )
                if (
                    block.get("type") == "tool_use"
                    and call_id in completed_ids
                ):
                    arguments = block.get("input", {})
                    if not (
                        isinstance(arguments, dict)
                        and arguments.get("_archived_tool_input")
                    ):
                        serialized = json.dumps(
                            arguments,
                            ensure_ascii=False,
                            default=str,
                        )
                        count += int(len(serialized) > input_threshold)
                elif block.get("type") == "tool_result":
                    output = block.get("output")
                    serialized = json.dumps(
                        output,
                        ensure_ascii=False,
                        default=str,
                    )
                    already_archived = "Tool output archived:" in serialized
                    count += int(
                        len(serialized) > output_threshold
                        and not already_archived,
                    )
        return count

    refs_root = workspace_dir / "refs"
    compacted = compact_completed_tool_inputs(
        messages,
        recent_n=0,
        threshold=input_threshold,
        session_id=session_id,
        refs_root=refs_root,
    )
    for message in messages:
        for block in _message_blocks(message):
            if (
                not isinstance(block, dict)
                or block.get("type") != "tool_result"
            ):
                continue
            output = block.get("output")
            serialized = json.dumps(output, ensure_ascii=False, default=str)
            if (
                len(serialized) <= output_threshold
                or "Tool output archived:" in serialized
            ):
                continue
            call_id = str(block.get("id") or block.get("tool_use_id") or "")
            summary = archive_tool_output(
                serialized,
                tool_name=str(block.get("name") or ""),
                session_id=session_id,
                tool_call_id=call_id,
                threshold=output_threshold,
                refs_root=refs_root,
            )
            if summary is None:
                continue
            block["output"] = [{"type": "text", "text": summary}]
            compacted += 1
    return compacted


def _load_chat_identities(workspace_dir: Path) -> dict[str, SessionIdentity]:
    chats_path = workspace_dir / "chats.json"
    try:
        chats = json.loads(chats_path.read_text(encoding="utf-8")).get(
            "chats",
            [],
        )
    except (OSError, ValueError, AttributeError):
        chats = []
    identities: dict[str, SessionIdentity] = {}
    for chat in chats:
        if not isinstance(chat, dict):
            continue
        user_id = str(chat.get("user_id") or "")
        session_id = str(chat.get("session_id") or "")
        if not session_id:
            continue
        filename = f"{_sanitize_filename(user_id)}_{_sanitize_filename(session_id)}.json"
        identities[filename] = SessionIdentity(
            path=workspace_dir / "sessions" / filename,
            workspace_dir=workspace_dir,
            user_id=user_id,
            session_id=session_id,
            channel=str(chat.get("channel") or "unknown"),
            agent_id=(
                "default"
                if workspace_dir.name.startswith("feishu_")
                else workspace_dir.name
            ),
            title=str(chat.get("name") or ""),
            exact=True,
        )
    return identities


def resolve_session_identity(
    path: Path,
    workspace_dir: Path,
) -> SessionIdentity:
    """Resolve session and user IDs, preferring authoritative chat metadata."""
    exact = _load_chat_identities(workspace_dir).get(path.name)
    if exact is not None:
        return exact

    stem = path.stem
    workspace_name = workspace_dir.name
    channel = "unknown"
    user_id = ""
    session_id = stem
    if workspace_name.startswith("feishu_ou_"):
        user_id = workspace_name[len("feishu_") :]
        prefix = _sanitize_filename(user_id) + "_"
        session_id = stem[len(prefix) :] if stem.startswith(prefix) else stem
        channel = "feishu"
    else:
        match = _FEISHU_USER_RE.match(stem)
        if match:
            user_id, session_id = match.groups()
            channel = "feishu"
        elif stem.startswith("default_"):
            user_id, session_id = "default", stem[len("default_") :]
        elif stem.startswith("main_"):
            user_id, session_id = "main", stem[len("main_") :]
        elif "_weixin--" in stem:
            user_id, session_tail = stem.split("_", 1)
            session_id = session_tail.replace("--", ":", 1)
            channel = "weixin"
        else:
            digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:16]
            user_id = f"_legacy_{workspace_name}_{digest}"

    return SessionIdentity(
        path=path,
        workspace_dir=workspace_dir,
        user_id=user_id,
        session_id=session_id,
        channel=channel,
        agent_id=(
            "default"
            if workspace_name.startswith("feishu_")
            else workspace_name
        ),
    )


def discover_sessions(workspaces_root: Path) -> list[SessionIdentity]:
    identities: list[SessionIdentity] = []
    for path in sorted(workspaces_root.glob("*/sessions/*.json")):
        identities.append(resolve_session_identity(path, path.parent.parent))
    return identities


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def migrate_session_file(
    identity: SessionIdentity,
    *,
    backup_root: Path,
    workspaces_root: Path,
    dry_run: bool = False,
    max_age_hours: float = 2.0,
    min_keep: int = 10,
) -> MigrationResult:
    path = identity.path
    try:
        original_bytes = path.read_bytes()
        state = json.loads(
            original_bytes.decode("utf-8", errors="surrogatepass"),
        )
        memory_state = state["agent"]["memory"]
        content = memory_state.get("content", [])
        if not isinstance(content, list):
            raise ValueError("agent.memory.content is not a list")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return MigrationResult(
            path=str(path),
            status="error",
            user_id=identity.user_id,
            session_id=identity.session_id,
            channel=identity.channel,
            reason=str(exc),
        )

    preview_state = json.loads(json.dumps(memory_state, ensure_ascii=False))
    compacted, kept = compact_memory_state_locally(
        preview_state,
        max_age_hours=max_age_hours,
        min_keep=min_keep,
    )
    tool_payloads = compact_tool_payloads_in_state(
        preview_state,
        workspace_dir=identity.workspace_dir,
        session_id=identity.session_id,
        dry_run=True,
    )
    if dry_run:
        return MigrationResult(
            path=str(path),
            status="dry-run",
            user_id=identity.user_id,
            session_id=identity.session_id,
            channel=identity.channel,
            messages=len(content),
            compacted=compacted,
            tool_payloads_compacted=tool_payloads,
            kept=kept,
            summary_chars=len(
                str(preview_state.get("_compressed_summary") or ""),
            ),
        )

    appended = persist_memory_to_ledger(
        memory=_JSONMemory(memory_state),
        workspace_dir=identity.workspace_dir,
        session_id=identity.session_id,
        user_id=identity.user_id,
        channel=identity.channel,
        agent_id=identity.agent_id,
        title=identity.title,
    )
    tool_payloads = compact_tool_payloads_in_state(
        preview_state,
        workspace_dir=identity.workspace_dir,
        session_id=identity.session_id,
    )
    if compacted or tool_payloads:
        # Refuse to overwrite a session that changed after it was read.
        if path.read_bytes() != original_bytes:
            return MigrationResult(
                path=str(path),
                status="skipped",
                user_id=identity.user_id,
                session_id=identity.session_id,
                channel=identity.channel,
                messages=len(content),
                ledger_appended=appended,
                reason="session changed during migration",
            )
        relative = path.relative_to(workspaces_root)
        backup_path = backup_root / relative
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if not backup_path.exists():
            shutil.copy2(path, backup_path)
        state["agent"]["memory"] = preview_state
        _atomic_write_json(path, state)

    return MigrationResult(
        path=str(path),
        status="migrated",
        user_id=identity.user_id,
        session_id=identity.session_id,
        channel=identity.channel,
        messages=len(content),
        ledger_appended=appended,
        compacted=compacted,
        tool_payloads_compacted=tool_payloads,
        kept=kept,
        summary_chars=len(str(preview_state.get("_compressed_summary") or "")),
    )


def migrate_all_sessions(
    *,
    workspaces_root: Path,
    backup_root: Path,
    manifest_path: Path,
    skip_session_ids: set[str] | None = None,
    dry_run: bool = False,
    max_age_hours: float = 2.0,
    min_keep: int = 10,
) -> list[MigrationResult]:
    """Migrate every discovered session and write a resumable manifest."""
    skipped = skip_session_ids or set()
    results: list[MigrationResult] = []
    if not dry_run:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
    for identity in discover_sessions(workspaces_root):
        if identity.session_id in skipped:
            result = MigrationResult(
                path=str(identity.path),
                status="skipped",
                user_id=identity.user_id,
                session_id=identity.session_id,
                channel=identity.channel,
                reason="active session",
            )
        else:
            result = migrate_session_file(
                identity,
                backup_root=backup_root,
                workspaces_root=workspaces_root,
                dry_run=dry_run,
                max_age_hours=max_age_hours,
                min_keep=min_keep,
            )
        results.append(result)
        if not dry_run:
            with manifest_path.open("a", encoding="utf-8") as manifest:
                manifest.write(
                    json.dumps(asdict(result), ensure_ascii=False) + "\n",
                )
    return results


__all__ = [
    "MigrationResult",
    "SessionIdentity",
    "build_extractive_summary",
    "compact_memory_state_locally",
    "compact_tool_payloads_in_state",
    "discover_sessions",
    "migrate_all_sessions",
    "migrate_session_file",
    "resolve_session_identity",
]
