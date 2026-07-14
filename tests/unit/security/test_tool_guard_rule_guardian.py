"""Regression tests for shell-command tool guard precision."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from hubos.security.tool_guard.guardians.rule_guardian import (
    RuleBasedToolGuardian,
)


@pytest.fixture
def guardian() -> RuleBasedToolGuardian:
    return RuleBasedToolGuardian()


def _rule_ids(guardian: RuleBasedToolGuardian, command: str) -> set[str]:
    findings = guardian.guard(
        "execute_shell_command",
        {"command": command},
    )
    return {finding.rule_id for finding in findings}


def test_safe_tmp_file_cleanup_is_not_flagged(
    guardian: RuleBasedToolGuardian,
) -> None:
    command = 'rm -f /tmp/v140_commit_msg.txt; echo "cleaned"'
    assert "TOOL_CMD_DANGEROUS_RM" not in _rule_ids(guardian, command)


def test_runtime_temp_file_cleanup_is_not_flagged(
    guardian: RuleBasedToolGuardian,
) -> None:
    target = Path(tempfile.gettempdir()) / "hubos-cleanup-test.txt"
    command = f"rm -f {target}"
    assert "TOOL_CMD_DANGEROUS_RM" not in _rule_ids(guardian, command)


def test_tmp_symlink_escape_stays_guarded(
    guardian: RuleBasedToolGuardian,
    tmp_path: Path,
) -> None:
    link = tmp_path / "outside"
    link.symlink_to(Path.home(), target_is_directory=True)

    command = f"rm -f {link}/important.txt"

    assert "TOOL_CMD_DANGEROUS_RM" in _rule_ids(guardian, command)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /tmp/hubos-cache",
        "rm -f /tmp/one /tmp/two",
        "rm -f /tmp/one; rm -rf /",
        "rm -f /Users/allen/important.txt",
        "rm -f /tmp/*.txt",
        "rm -f $TMPDIR/hubos.txt",
    ],
)
def test_destructive_or_ambiguous_rm_stays_guarded(
    guardian: RuleBasedToolGuardian,
    command: str,
) -> None:
    assert "TOOL_CMD_DANGEROUS_RM" in _rule_ids(guardian, command)
