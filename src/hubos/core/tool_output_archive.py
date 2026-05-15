# -*- coding: utf-8 -*-
"""Tool output archival — write large tool outputs to refs files.

Phase-1 lightweight version:
- When tool output exceeds a threshold, write the full output to a refs file
- Return a structured summary that is useful for subsequent model turns
- Short outputs pass through unchanged
- Periodic cleanup of expired refs (retention + capacity eviction)
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Threshold: outputs longer than this (in chars) will be archived.
# Set conservatively; model_factory already truncates at 3k chars for old turns.
DEFAULT_ARCHIVE_THRESHOLD_CHARS = 3_000

# Maximum length of the summary to keep in context
_MAX_SUMMARY_DETAIL_CHARS = 500


def get_refs_root() -> Path:
    """Get the refs root directory under the current workspace.

    Uses HUBOS_WORKING_DIR if set, otherwise ~/.hubos.
    """
    workspace = os.environ.get("HUBOS_WORKING_DIR")
    if workspace:
        root = Path(workspace)
    else:
        root = Path.home() / ".hubos"
    return root / "refs"


def archive_tool_output(
    text: str,
    *,
    tool_name: str = "",
    session_id: str = "",
    tool_call_id: str = "",
    threshold: int = DEFAULT_ARCHIVE_THRESHOLD_CHARS,
) -> Optional[str]:
    """Archive large tool output to a refs file and return a structured summary.

    If the text is under the threshold, returns None (caller should use original text).

    If archived, returns a structured summary string suitable for model context.

    Args:
        text: The tool output text.
        tool_name: Name of the tool that produced the output.
        session_id: Current session ID for organizing refs.
        tool_call_id: Unique ID of the tool call (optional, auto-generated if empty).
        threshold: Character threshold for archival.

    Returns:
        None if text is short enough; otherwise a structured summary string.
    """
    if not text or len(text) <= threshold:
        return None

    # Resolve session_id: explicit param > context var > fallback
    if not session_id:
        from ..config.context import get_current_session_id

        session_id = get_current_session_id() or ""

    if not session_id:
        session_id = "unknown"
    if not tool_call_id:
        tool_call_id = uuid.uuid4().hex[:12]

    # Write full output to refs file
    refs_root = get_refs_root()
    session_dir = refs_root / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    ref_file = session_dir / f"{tool_call_id}.md"
    try:
        ref_file.write_text(text, encoding="utf-8")
    except Exception:
        logger.warning(
            "Failed to write tool output archive: %s",
            ref_file,
            exc_info=True,
        )
        return None

    # Build structured summary
    summary = _build_summary(
        text=text,
        tool_name=tool_name,
        ref_path=str(ref_file),
    )
    return summary


def _build_summary(
    *,
    text: str,
    tool_name: str,
    ref_path: str,
) -> str:
    """Build a structured summary from the archived tool output."""
    lines = text.split("\n")
    line_count = len(lines)

    # Extract key information from the output
    key_error = _extract_key_error(text)
    key_files = _extract_key_files(text)

    # Build the summary
    parts = [f"Tool output archived: {tool_name or 'unknown'}"]

    if key_error:
        parts.append(f"Result: error — {key_error}")
    elif line_count > 1:
        # Show first meaningful line as a hint
        first_meaningful = _first_meaningful_line(text)
        if first_meaningful:
            detail = first_meaningful[:200]
            parts.append(f"Result: {line_count} lines. {detail}")
        else:
            parts.append(f"Result: {line_count} lines")
    else:
        char_count = len(text)
        parts.append(f"Result: {char_count} chars")

    if key_files:
        file_list = ", ".join(key_files[:5])
        parts.append(f"Key files: {file_list}")

    parts.append(f"Ref: {ref_path}")

    return "\n".join(parts)


def _extract_key_error(text: str) -> str:
    """Extract the most relevant error message from tool output."""
    error_patterns = [
        r"(Error[:\s].+?)(?:\n|$)",
        r"(Exception[:\s].+?)(?:\n|$)",
        r"(Traceback[\s\S]*?)(?:\n\n|\Z)",
        r"(\w+Error[:\s].+?)(?:\n|$)",
    ]
    for pattern in error_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            error = match.group(1).strip()[:200]
            return error
    return ""


def _extract_key_files(text: str) -> list[str]:
    """Extract file paths mentioned in tool output."""
    # Match common file path patterns
    file_pattern = (
        r"(?:^|[\s:])(/[\w/.-]+\.\w+|[~][\w/.-]+\.\w+|\w+://[\w/.-]+\.\w+)"
    )
    matches = re.findall(file_pattern, text)
    # Deduplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result[:5]


def _first_meaningful_line(text: str) -> str:
    """Get the first non-empty, non-separator line from text."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and stripped not in ("---", "```", "==="):
            return stripped
    return ""


# ---------------------------------------------------------------------------
# Refs cleanup
# ---------------------------------------------------------------------------

# Sessions whose refs were written to within this window are protected
# from cleanup, regardless of retention_days.  This is the conservative
# fallback when no explicit "is session running?" registry is available.
DEFAULT_PROTECT_HOURS: float = 4.0

DEFAULT_RETENTION_DAYS: int = 7
DEFAULT_MAX_TOTAL_MB: float = 500.0


@dataclass
class CleanupResult:
    """Detailed stats returned by :func:`cleanup_refs`."""

    protected_sessions: list[str] = field(default_factory=list)
    expired_deleted_sessions: list[str] = field(default_factory=list)
    capacity_deleted_sessions: list[str] = field(default_factory=list)
    freed_bytes: int = 0
    remaining_bytes: int = 0
    skipped_sessions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # convenience
    @property
    def total_deleted(self) -> int:
        return len(self.expired_deleted_sessions) + len(
            self.capacity_deleted_sessions,
        )

    def summary(self) -> str:
        """Human-readable one-line summary."""
        parts = [
            f"protected={len(self.protected_sessions)}",
            f"expired={len(self.expired_deleted_sessions)}",
            f"capacity={len(self.capacity_deleted_sessions)}",
            f"freed={self.freed_bytes / 1024 / 1024:.1f}MB",
            f"remaining={self.remaining_bytes / 1024 / 1024:.1f}MB",
        ]
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return "refs cleanup: " + ", ".join(parts)


def _session_dir_mtime(session_dir: Path) -> float:
    """Return the newest mtime among all files in *session_dir*.

    Returns 0.0 if the directory is empty or doesn't exist.
    """
    newest = 0.0
    try:
        for f in session_dir.iterdir():
            if f.is_file():
                mtime = f.stat().st_mtime
                if mtime > newest:
                    newest = mtime
    except OSError:
        return 0.0
    return newest


def _session_dir_size(session_dir: Path) -> int:
    """Total bytes of all files in *session_dir*."""
    total = 0
    try:
        for f in session_dir.iterdir():
            if f.is_file():
                total += f.stat().st_size
    except OSError:
        pass
    return total


def cleanup_refs(
    *,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    max_total_mb: float = DEFAULT_MAX_TOTAL_MB,
    protect_hours: float = DEFAULT_PROTECT_HOURS,
    dry_run: bool = False,
) -> CleanupResult:
    """Clean up expired and oversized refs directories.

    Three rules applied in priority order:

    1. **Active protection** — session dirs whose newest file mtime is
       within *protect_hours* of now are never touched.
    2. **Retention** — non-protected session dirs whose newest mtime is
       older than *retention_days* are deleted entirely.
    3. **Capacity eviction** — if total size still exceeds *max_total_mb*
       after retention cleanup, the oldest non-protected session dirs are
       deleted until the budget is met.

    All timestamps are sourced from the refs filesystem itself — no
    dependency on external session registries.

    Args:
        retention_days: Delete session dirs older than this (days).
        max_total_mb: Maximum total refs size in megabytes.
        protect_hours: Protect session dirs written to within this many
            hours.  Acts as a conservative "probably still active" guard.
        dry_run: If True, report what *would* be deleted without actually
            deleting anything.

    Returns:
        A :class:`CleanupResult` with detailed per-session stats.
    """
    result = CleanupResult()
    refs_root = get_refs_root()

    if not refs_root.exists():
        return result

    now = time.time()
    protect_cutoff = now - protect_hours * 3600
    retention_cutoff = now - retention_days * 86400
    max_total_bytes = int(max_total_mb * 1024 * 1024)

    # Collect session dirs with metadata
    sessions: list[
        tuple[str, Path, float, int]
    ] = []  # (id, path, mtime, size)
    for entry in sorted(refs_root.iterdir()):
        if not entry.is_dir():
            continue
        mtime = _session_dir_mtime(entry)
        size = _session_dir_size(entry)
        sessions.append((entry.name, entry, mtime, size))

    if not sessions:
        return result

    # ── Rule 1: classify protected vs candidates ──────────────────────
    protected: list[tuple[str, Path, float, int]] = []
    candidates: list[tuple[str, Path, float, int]] = []
    for item in sessions:
        sid, spath, smtime, ssize = item
        if smtime >= protect_cutoff:
            protected.append(item)
            result.protected_sessions.append(sid)
        else:
            candidates.append(item)

    # ── Rule 2: retention cleanup ─────────────────────────────────────
    remaining: list[tuple[str, Path, float, int]] = []
    for item in candidates:
        sid, spath, smtime, ssize = item
        if smtime < retention_cutoff:
            if dry_run:
                result.freed_bytes += ssize
                result.expired_deleted_sessions.append(sid)
            else:
                try:
                    shutil.rmtree(spath)
                    result.freed_bytes += ssize
                    result.expired_deleted_sessions.append(sid)
                except Exception as exc:
                    result.errors.append(f"{sid}: {exc}")
                    logger.warning(
                        "refs cleanup: failed to remove %s: %s", spath, exc
                    )
                    # Deletion failed — keep in remaining so it's tracked
                    remaining.append(item)
        else:
            remaining.append(item)

    # ── Rule 3: capacity eviction ─────────────────────────────────────
    # Re-measure remaining total (retention may have freed enough)
    current_total = sum(s for _, _, _, s in protected) + sum(
        s for _, _, _, s in remaining
    )
    if current_total > max_total_bytes:
        # Sort remaining by mtime ascending (oldest first)
        remaining.sort(key=lambda x: x[2])
        for item in remaining:
            if current_total <= max_total_bytes:
                break
            sid, spath, smtime, ssize = item
            if dry_run:
                current_total -= ssize
                result.freed_bytes += ssize
                result.capacity_deleted_sessions.append(sid)
            else:
                try:
                    shutil.rmtree(spath)
                    current_total -= ssize
                    result.freed_bytes += ssize
                    result.capacity_deleted_sessions.append(sid)
                except Exception as exc:
                    result.errors.append(f"{sid}: {exc}")
                    logger.warning(
                        "refs cleanup: failed to remove %s: %s",
                        spath,
                        exc,
                    )

    # Sessions that survived all rules
    surviving = [
        item[0]
        for item in remaining
        if item[0] not in result.capacity_deleted_sessions
    ]
    result.skipped_sessions = surviving + result.protected_sessions
    result.remaining_bytes = current_total

    level = logging.INFO if not result.errors else logging.WARNING
    logger.log(
        level,
        "refs cleanup (%s): %s",
        "dry-run" if dry_run else "live",
        result.summary(),
    )
    return result
