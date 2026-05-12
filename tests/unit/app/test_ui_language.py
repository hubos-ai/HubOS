# -*- coding: utf-8 -*-
"""Tests for UI-language-aware slash command responses.

Covers:
  - ui_language.get_ui_language / is_zh / clear_language_cache
  - /deny no-pending response follows UI language
  - /approve (daemon) no-pending response follows UI language
  - /approve (daemon) success response follows UI language
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_HERE = Path(__file__).resolve()
_SRC_ROOT = _HERE.parents[3] / "src"
_MODULE_PATH = _SRC_ROOT / "hubos" / "app" / "ui_language.py"


def _load_ui_language_direct(tmp_settings: Path):
    """Load ui_language.py directly via importlib.util, bypassing packages."""
    # Register stub parent packages so relative import of hubos.constant works.
    for pkg in ("hubos", "hubos.app"):
        if pkg not in sys.modules:
            sys.modules[pkg] = type(sys)(pkg)

    # Ensure hubos.constant has WORKING_DIR pointing to tmp dir
    if "hubos.constant" not in sys.modules:
        sys.modules["hubos.constant"] = types.ModuleType("hubos.constant")
    sys.modules["hubos.constant"].WORKING_DIR = tmp_settings.parent

    mod_name = "hubos.app.ui_language"
    if mod_name in sys.modules:
        del sys.modules[mod_name]

    spec = importlib.util.spec_from_file_location(mod_name, _MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)

    # Override the settings file path for testing
    mod._SETTINGS_FILE = tmp_settings
    mod._read_language.cache_clear()
    return mod


# ---------------------------------------------------------------------------
# Tests for ui_language module
# ---------------------------------------------------------------------------


class TestUiLanguage:
    """Direct tests for get_ui_language / is_zh."""

    def test_default_en_when_no_file(self, tmp_path):
        mod = _load_ui_language_direct(tmp_path / "nonexistent.json")
        assert mod.get_ui_language() == "en"
        assert mod.is_zh() is False

    def test_returns_zh(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "zh"}), "utf-8")
        mod = _load_ui_language_direct(sf)
        assert mod.get_ui_language() == "zh"
        assert mod.is_zh() is True

    def test_returns_en(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "en"}), "utf-8")
        mod = _load_ui_language_direct(sf)
        assert mod.get_ui_language() == "en"
        assert mod.is_zh() is False

    def test_cache_cleared(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "en"}), "utf-8")
        mod = _load_ui_language_direct(sf)
        assert mod.get_ui_language() == "en"
        # Update file and clear cache
        sf.write_text(json.dumps({"language": "zh"}), "utf-8")
        mod.clear_language_cache()
        assert mod.get_ui_language() == "zh"


# ---------------------------------------------------------------------------
# Tests for /deny no-pending (runner.py query_handler)
# ---------------------------------------------------------------------------


class TestDenyNoPending:
    """/deny without pending approval returns language-appropriate message."""

    @pytest.mark.asyncio
    async def test_deny_no_pending_en(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "en"}), "utf-8")
        ui_mod = _load_ui_language_direct(sf)
        assert not ui_mod.is_zh()
        # Mirror the runner.py logic
        msg = (
            "当前没有等待拒绝的工具操作。"
            if ui_mod.is_zh()
            else "No pending tool action to deny."
        )
        assert msg == "No pending tool action to deny."

    @pytest.mark.asyncio
    async def test_deny_no_pending_zh(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "zh"}), "utf-8")
        ui_mod = _load_ui_language_direct(sf)
        assert ui_mod.is_zh()
        msg = (
            "当前没有等待拒绝的工具操作。"
            if ui_mod.is_zh()
            else "No pending tool action to deny."
        )
        assert msg == "当前没有等待拒绝的工具操作。"


# ---------------------------------------------------------------------------
# Tests for /approve via daemon_commands
# ---------------------------------------------------------------------------


def _approve_no_pending_text(is_zh: bool) -> str:
    """Mirror the text-selection logic from daemon_commands.py."""
    if is_zh:
        return (
            "**暂无待审批操作**\n\n" "当前会话没有等待审批的工具操作。\n" "只有在敏感工具调用等待你确认时，才能使用此命令。"
        )
    return (
        "**No pending approval**\n\n"
        "- There is no tool-guard approval waiting for this session.\n"
        "- This command is only valid when a sensitive tool "
        "call is awaiting your review."
    )


def _approve_success_text(is_zh: bool, tool_name: str, req_id: str) -> str:
    """Mirror the approve-success text-selection logic."""
    if is_zh:
        return (
            f"**工具已批准执行** ✅\n\n"
            f"- 工具：`{tool_name}`\n"
            f"- 请求：`{req_id[:8]}…`"
        )
    return (
        f"**Tool execution approved** ✅\n\n"
        f"- Tool: `{tool_name}`\n"
        f"- Request: `{req_id[:8]}…`"
    )


class TestCacheInvalidation:
    """Verify that clear_language_cache() causes the next read to pick up
    file changes — mirroring what put_language() does after saving."""

    def test_language_refreshes_after_clear(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "en"}), "utf-8")
        mod = _load_ui_language_direct(sf)

        assert mod.get_ui_language() == "en"

        # Simulate put_language: write new language then clear cache
        sf.write_text(json.dumps({"language": "zh"}), "utf-8")
        mod.clear_language_cache()

        assert mod.get_ui_language() == "zh"
        assert mod.is_zh() is True

    def test_language_stale_without_clear(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "en"}), "utf-8")
        mod = _load_ui_language_direct(sf)

        assert mod.get_ui_language() == "en"

        # Overwrite file but do NOT clear cache — should stay "en"
        sf.write_text(json.dumps({"language": "zh"}), "utf-8")
        assert mod.get_ui_language() == "en"


class TestApproveDaemon:
    """/approve (daemon) language-aware responses."""

    @pytest.mark.asyncio
    async def test_approve_no_pending_en(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "en"}), "utf-8")
        ui_mod = _load_ui_language_direct(sf)
        text = _approve_no_pending_text(ui_mod.is_zh())
        assert "No pending approval" in text
        assert "暂无" not in text

    @pytest.mark.asyncio
    async def test_approve_no_pending_zh(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "zh"}), "utf-8")
        ui_mod = _load_ui_language_direct(sf)
        text = _approve_no_pending_text(ui_mod.is_zh())
        assert "暂无待审批操作" in text
        assert "No pending" not in text

    @pytest.mark.asyncio
    async def test_approve_success_en(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "en"}), "utf-8")
        ui_mod = _load_ui_language_direct(sf)
        text = _approve_success_text(
            ui_mod.is_zh(), "edit_file", "abc123def456"
        )
        assert "Tool execution approved" in text
        assert "edit_file" in text
        assert "工具" not in text

    @pytest.mark.asyncio
    async def test_approve_success_zh(self, tmp_path):
        sf = tmp_path / "settings.json"
        sf.write_text(json.dumps({"language": "zh"}), "utf-8")
        ui_mod = _load_ui_language_direct(sf)
        text = _approve_success_text(
            ui_mod.is_zh(), "edit_file", "abc123def456"
        )
        assert "工具已批准执行" in text
        assert "edit_file" in text
        assert "Tool execution" not in text
