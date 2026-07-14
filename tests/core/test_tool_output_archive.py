# -*- coding: utf-8 -*-
"""Tests for tool_output_archive — archival + cleanup."""

import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def refs_tmp(tmp_path: Path):
    """Create a temporary refs root and patch get_refs_root."""
    refs_root = tmp_path / "refs"
    refs_root.mkdir()

    # We'll write test dirs under refs_root
    def _write_session(
        session_id: str,
        files: dict[str, str],
        mtime_offset: float = 0,
    ):
        """Create a session dir with files. mtime_offset < 0 sets mtime to the past."""
        sdir = refs_root / session_id
        sdir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            f = sdir / name
            f.write_text(content, encoding="utf-8")
            if mtime_offset:
                os.utime(f, (f.stat().st_mtime + mtime_offset,) * 2)
        return sdir

    with patch(
        "hubos.core.tool_output_archive.get_refs_root",
        return_value=refs_root,
    ):
        yield refs_root, _write_session


class TestArchiveToolOutput:
    """Tests for archive_tool_output (write path)."""

    def test_short_output_returns_none(self, refs_tmp):
        refs_root, _ = refs_tmp
        from hubos.core.tool_output_archive import archive_tool_output

        result = archive_tool_output("short text", tool_name="test")
        assert result is None

    def test_long_output_archived(self, refs_tmp):
        refs_root, _ = refs_tmp
        from hubos.core.tool_output_archive import archive_tool_output

        long_text = "x" * 5000
        result = archive_tool_output(
            long_text,
            tool_name="shell",
            session_id="sess-1",
        )
        assert result is not None
        assert "archived" in result.lower()
        assert "shell" in result
        # File was created
        session_dir = refs_root / "sess-1"
        assert session_dir.exists()
        files = list(session_dir.iterdir())
        assert len(files) == 1
        assert files[0].read_text() == long_text

    def test_completed_old_tool_input_is_archived(self, refs_tmp):
        from agentscope.message import Msg
        from hubos.core.tool_output_archive import (
            compact_completed_tool_inputs,
        )

        tool_use = Msg(
            name="assistant",
            role="assistant",
            content=[
                {
                    "type": "tool_use",
                    "id": "call-big",
                    "name": "write_file",
                    "input": {"content": "x" * 500},
                    "raw_input": "x" * 500,
                },
            ],
        )
        tool_result = Msg(
            name="system",
            role="system",
            content=[
                {
                    "type": "tool_result",
                    "id": "call-big",
                    "name": "write_file",
                    "output": [{"type": "text", "text": "ok"}],
                },
            ],
        )
        recent = Msg(name="user", role="user", content="continue")

        count = compact_completed_tool_inputs(
            [tool_use, tool_result, recent],
            recent_n=1,
            threshold=100,
        )

        assert count == 1
        pointer = tool_use.content[0]["input"]
        assert pointer["_archived_tool_input"] is True
        assert Path(pointer["ref"]).read_text(encoding="utf-8")


class TestCleanupRefs:
    """Tests for cleanup_refs."""

    def test_empty_refs_dir(self, refs_tmp):
        refs_root, _ = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        result = cleanup_refs()
        assert result.total_deleted == 0
        assert result.freed_bytes == 0

    def test_protected_session_not_deleted(self, refs_tmp):
        """Session with recent mtime is protected regardless of retention."""
        refs_root, write_session = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        # Recent session (mtime = now)
        write_session("recent-sess", {"a.md": "content" * 100})

        result = cleanup_refs(retention_days=0, protect_hours=4)
        assert "recent-sess" in result.protected_sessions
        assert result.total_deleted == 0
        assert (refs_root / "recent-sess").exists()

    def test_expired_session_deleted(self, refs_tmp):
        """Session older than retention_days is deleted."""
        refs_root, write_session = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        # Old session (mtime = 10 days ago)
        write_session(
            "old-sess",
            {"a.md": "content" * 100},
            mtime_offset=-10 * 86400,
        )

        result = cleanup_refs(retention_days=7, protect_hours=0, dry_run=False)
        assert "old-sess" in result.expired_deleted_sessions
        assert result.freed_bytes > 0
        assert not (refs_root / "old-sess").exists()

    def test_expired_dry_run_does_not_delete(self, refs_tmp):
        """Dry run reports but doesn't actually delete."""
        refs_root, write_session = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        write_session(
            "old-sess",
            {"a.md": "data"},
            mtime_offset=-10 * 86400,
        )

        result = cleanup_refs(retention_days=7, protect_hours=0, dry_run=True)
        assert "old-sess" in result.expired_deleted_sessions
        # File still exists
        assert (refs_root / "old-sess").exists()

    def test_capacity_eviction(self, refs_tmp):
        """When total exceeds max, oldest non-protected dirs are evicted."""
        refs_root, write_session = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        # Create 3 sessions, each ~1KB, all older than protect_hours
        for i in range(3):
            write_session(
                f"sess-{i}",
                {f"file{j}.md": "x" * 300 for j in range(3)},
                mtime_offset=-(i + 1) * 86400,  # 1, 2, 3 days ago
            )

        # Max 1 KB → should evict oldest first
        result = cleanup_refs(
            retention_days=30,
            max_total_mb=0.001,  # ~1KB
            protect_hours=0,
        )
        assert result.capacity_deleted_sessions
        # At least one deleted
        assert result.total_deleted >= 1

    def test_not_expired_not_over_budget_kept(self, refs_tmp):
        """Session within retention and under budget is kept."""
        refs_root, write_session = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        write_session(
            "fresh-sess",
            {"a.md": "small"},
            mtime_offset=-2 * 86400,  # 2 days ago
        )

        result = cleanup_refs(
            retention_days=7,
            max_total_mb=500,
            protect_hours=0,
        )
        assert result.total_deleted == 0
        assert "fresh-sess" in result.skipped_sessions
        assert (refs_root / "fresh-sess").exists()

    def test_mixed_scenario(self, refs_tmp):
        """Realistic mix: protected + expired + fresh."""
        refs_root, write_session = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        # Protected: written just now
        write_session("active", {"a.md": "x" * 200})

        # Expired: 10 days old
        write_session(
            "stale",
            {"b.md": "y" * 200},
            mtime_offset=-10 * 86400,
        )

        # Fresh: 2 days old
        write_session(
            "recent",
            {"c.md": "z" * 200},
            mtime_offset=-2 * 86400,
        )

        result = cleanup_refs(
            retention_days=7,
            max_total_mb=500,
            protect_hours=4,
        )
        assert "active" in result.protected_sessions
        assert "stale" in result.expired_deleted_sessions
        assert "recent" in result.skipped_sessions
        assert (refs_root / "active").exists()
        assert not (refs_root / "stale").exists()
        assert (refs_root / "recent").exists()

    def test_summary_format(self, refs_tmp):
        refs_root, write_session = refs_tmp
        from hubos.core.tool_output_archive import cleanup_refs

        write_session("s1", {"a.md": "x" * 200})
        result = cleanup_refs()
        summary = result.summary()
        assert "protected=" in summary
        assert "remaining=" in summary
