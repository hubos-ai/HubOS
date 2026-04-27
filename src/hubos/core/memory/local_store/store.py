# -*- coding: utf-8 -*-
"""Local file-backed memory store.

Filesystem layout (under ``get_memory_root()``)::

    archives/YYYY-MM/{session_id}.json.gz   gzipped finished sessions
    daily/YYYY-MM-DD.md                     human-readable daily digests
    sessions/{session_id}/
        metadata.json                       session-level metadata (schema v1.0)
        messages.jsonl                      append-only message stream
        tools/{message_id}.json             per-tool-call payloads
        attachments/{attachment_id}.{ext}   binary blobs
    index/
        sessions_index.jsonl                quick listing / search index
        daily_summaries.jsonl               links daily/*.md to dates

Concurrency note: writes are append-only / file-replace; no locking. Two
writers racing on the same session_id can interleave messages.jsonl lines
(harmless — each line is a complete JSON record) but a metadata.json update
race can lose data. The intended access pattern is one writer per session,
which matches the GM-agent-per-session model.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


_DEFAULT_ROOT = Path.home() / ".hubos" / "memory"

ARCHIVE_THRESHOLD_DAYS = 30
ACTIVE_THRESHOLD_DAYS = 7

_SUBDIR_NAMES = ("sessions", "archives", "daily", "index", "schemas")


def get_memory_root() -> Path:
    """Resolve the memory root from env or default. Read on every call so a
    test can monkey-patch via env without re-importing the module."""
    override = os.environ.get("HUBOS_MEMORY_ROOT")
    if override:
        return Path(override).expanduser()
    return _DEFAULT_ROOT


class LocalMemoryStore:
    """File-backed long-term memory store. L4 default in hubos.core.

    All paths are derived from :func:`get_memory_root`, evaluated once at
    construction time. Pass ``root`` explicitly to override (useful in tests).
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root: Path = (
            Path(root).expanduser() if root else get_memory_root()
        )
        self.sessions_dir = self.root / "sessions"
        self.archives_dir = self.root / "archives"
        self.daily_dir = self.root / "daily"
        self.index_dir = self.root / "index"
        self.schemas_dir = self.root / "schemas"
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for d in (
            self.sessions_dir,
            self.archives_dir,
            self.daily_dir,
            self.index_dir,
            self.schemas_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)

    # ─── Session lifecycle ─────────────────────────────────────────────

    def create_session(self, session_id: str, metadata: Dict[str, Any]) -> str:
        session_dir = self.sessions_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        (session_dir / "messages.jsonl").write_text("", encoding="utf-8")
        (session_dir / "tools").mkdir(exist_ok=True)
        (session_dir / "attachments").mkdir(exist_ok=True)
        self._update_sessions_index(session_id, metadata)
        return session_id

    def append_message(self, session_id: str, message: Dict[str, Any]) -> None:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        msg_line = json.dumps(message, ensure_ascii=False) + "\n"
        with (session_dir / "messages.jsonl").open("a", encoding="utf-8") as f:
            f.write(msg_line)
        self._refresh_message_count(session_id)

    def save_tool_call(
        self,
        session_id: str,
        message_id: str,
        tool_call: Dict[str, Any],
    ) -> None:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        (session_dir / "tools" / f"{message_id}.json").write_text(
            json.dumps(tool_call, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def update_metadata(
        self,
        session_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        (session_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def end_session(
        self,
        session_id: str,
        ended_at: str,
        end_reason: str,
    ) -> None:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        metadata = json.loads(
            (session_dir / "metadata.json").read_text(encoding="utf-8"),
        )
        metadata["ended_at"] = ended_at
        metadata["end_reason"] = end_reason
        (session_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ─── Daily summary ────────────────────────────────────────────────

    def save_daily_summary(self, date: str, summary: str) -> None:
        (self.daily_dir / f"{date}.md").write_text(summary, encoding="utf-8")

    def append_daily_summary_index(self, record: Dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with (self.index_dir / "daily_summaries.jsonl").open(
            "a",
            encoding="utf-8",
        ) as f:
            f.write(line)

    # ─── Archive ──────────────────────────────────────────────────────

    def archive_session(self, session_id: str) -> None:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        metadata = json.loads(
            (session_dir / "metadata.json").read_text(encoding="utf-8"),
        )
        started = datetime.fromisoformat(metadata["started_at"])
        archive_subdir = self.archives_dir / started.strftime("%Y-%m")
        archive_subdir.mkdir(exist_ok=True)
        archive_file = archive_subdir / f"{session_id}.json.gz"
        with gzip.open(archive_file, "wt", encoding="utf-8") as f:
            session_data = {
                "metadata": metadata,
                "messages": [
                    json.loads(line)
                    for line in (session_dir / "messages.jsonl").open(
                        encoding="utf-8",
                    )
                    if line.strip()
                ],
            }
            json.dump(session_data, f, ensure_ascii=False)
        shutil.rmtree(session_dir)

    def auto_archive(self) -> List[str]:
        archived: List[str] = []
        cutoff = datetime.now() - timedelta(days=ARCHIVE_THRESHOLD_DAYS)
        for session_dir in self.sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            try:
                metadata = json.loads(
                    (session_dir / "metadata.json").read_text(
                        encoding="utf-8",
                    ),
                )
                started = datetime.fromisoformat(metadata["started_at"])
                if started < cutoff:
                    self.archive_session(session_dir.name)
                    archived.append(session_dir.name)
            except (
                FileNotFoundError,
                KeyError,
                ValueError,
                json.JSONDecodeError,
            ):
                continue
        return archived

    # ─── Index helpers ────────────────────────────────────────────────

    def _update_sessions_index(
        self,
        session_id: str,
        metadata: Dict[str, Any],
    ) -> None:
        try:
            # 'started' / 'started_at' and 'agent' / 'agent_id' / 'user' /
            # 'user_id' are both in use across callers. Accept either and
            # materialise the canonical name in the index so downstream
            # consumers don't need to know which alias was written.
            started = metadata.get("started_at") or metadata.get("started", "")
            agent_id = metadata.get("agent_id") or metadata.get("agent", "")
            user_id = metadata.get("user_id") or metadata.get("user", "")
            index_record = {
                "session_id": session_id,
                "channel": metadata.get("channel", ""),
                # Keep legacy 'agent' key for back-compat, add 'agent_id'.
                "agent": agent_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "title": metadata.get("title", ""),
                "started": started,
                "tags": metadata.get("tags", []),
                "msg_count": metadata.get("message_count", 0),
                "topics": [],
            }
            line = json.dumps(index_record, ensure_ascii=False) + "\n"
            with (self.index_dir / "sessions_index.jsonl").open(
                "a",
                encoding="utf-8",
            ) as f:
                f.write(line)
        except (TypeError, ValueError):
            pass

    def _refresh_message_count(self, session_id: str) -> None:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            return
        try:
            with (session_dir / "messages.jsonl").open(encoding="utf-8") as f:
                msg_count = sum(1 for line in f if line.strip())
            metadata = json.loads(
                (session_dir / "metadata.json").read_text(encoding="utf-8"),
            )
            metadata["message_count"] = msg_count
            (session_dir / "metadata.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    # ─── Read / search ────────────────────────────────────────────────

    def load_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        session_dir = self.sessions_dir / session_id
        if not session_dir.exists():
            return self._load_archived_session(session_id)
        try:
            metadata = json.loads(
                (session_dir / "metadata.json").read_text(encoding="utf-8"),
            )
            with (session_dir / "messages.jsonl").open(encoding="utf-8") as f:
                messages = [json.loads(line) for line in f if line.strip()]
            return {"metadata": metadata, "messages": messages}
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def _load_archived_session(
        self,
        session_id: str,
    ) -> Optional[Dict[str, Any]]:
        for archive_month in self.archives_dir.iterdir():
            if not archive_month.is_dir():
                continue
            archive_file = archive_month / f"{session_id}.json.gz"
            if archive_file.exists():
                try:
                    with gzip.open(archive_file, "rt", encoding="utf-8") as f:
                        return json.load(f)
                except (OSError, json.JSONDecodeError):
                    return None
        return None

    def get_daily_summary(self, date: str) -> Optional[str]:
        path = self.daily_dir / f"{date}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_sessions(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        sessions: List[Dict[str, Any]] = []
        index_path = self.index_dir / "sessions_index.jsonl"
        if not index_path.exists():
            return sessions
        with index_path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if start_date and record.get("started", "") < start_date:
                        continue
                    if end_date and record.get("started", "") > end_date:
                        continue
                    sessions.append(record)
                except json.JSONDecodeError:
                    continue
        return sessions

    def search_sessions(
        self,
        query: str,
        fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        fields = fields or ["title", "tags", "topics"]
        results: List[Dict[str, Any]] = []
        index_path = self.index_dir / "sessions_index.jsonl"
        if not index_path.exists():
            return results
        q_lower = query.lower()
        with index_path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for field in fields:
                    if q_lower in str(record.get(field, "")).lower():
                        results.append(record)
                        break
        return results

    def search_messages(
        self,
        query: str,
        session_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if session_id:
            session_dirs = [self.sessions_dir / session_id]
        else:
            session_dirs = [
                d for d in self.sessions_dir.iterdir() if d.is_dir()
            ]
        q_lower = query.lower()
        for session_dir in session_dirs:
            if not session_dir.exists():
                continue
            try:
                with (session_dir / "messages.jsonl").open(
                    encoding="utf-8",
                ) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        msg = json.loads(line)
                        if q_lower in str(msg.get("content", "")).lower():
                            msg["_session_id"] = session_dir.name
                            results.append(msg)
            except (FileNotFoundError, json.JSONDecodeError):
                continue
        return results


if __name__ == "__main__":
    store = LocalMemoryStore()
    print("LocalMemoryStore initialized")
    print(f"Memory root: {store.root}")
