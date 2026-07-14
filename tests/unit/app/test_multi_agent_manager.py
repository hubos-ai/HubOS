# -*- coding: utf-8 -*-
"""Tests for Feishu workspace knowledge sharing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hubos.app import multi_agent_manager as mam_mod


class _DummyWorkspace:
    """Minimal workspace stub for manager tests."""

    def __init__(self, agent_id: str, workspace_dir: str):
        self.agent_id = agent_id
        self.workspace_dir = workspace_dir
        self.manager = None

    async def start(self) -> None:
        return None

    def set_manager(self, manager) -> None:
        self.manager = manager


def _write_default_workspace(root: Path) -> None:
    default_dir = root / ".hubos" / "workspaces" / "default"
    (default_dir / "memory" / "knowledge").mkdir(parents=True, exist_ok=True)
    (default_dir / "skills").mkdir(parents=True, exist_ok=True)
    (default_dir / "agent.json").write_text(
        json.dumps(
            {
                "id": "default",
                "name": "default",
                "workspace_dir": str(default_dir),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (default_dir / "memory" / "knowledge" / "tools.md").write_text(
        "tools knowledge",
        encoding="utf-8",
    )
    (default_dir / "memory" / "knowledge" / "system.md").write_text(
        "system knowledge",
        encoding="utf-8",
    )
    (default_dir / "memory" / "knowledge" / "business.md").write_text(
        "sensitive business knowledge",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_feishu_workspace_shares_only_whitelisted_knowledge(
    monkeypatch,
    tmp_path,
):
    """New Feishu workspaces should inherit only safe shared knowledge."""
    _write_default_workspace(tmp_path)
    monkeypatch.setattr(mam_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mam_mod, "Workspace", _DummyWorkspace)

    manager = mam_mod.MultiAgentManager()
    workspace = await manager.get_or_create_feishu_workspace("ou_test_123")

    assert workspace is not None
    feishu_dir = tmp_path / ".hubos" / "workspaces" / "feishu_ou_test_123"
    knowledge_dir = feishu_dir / "memory" / "knowledge"

    assert (knowledge_dir / "tools.md").exists()
    assert (knowledge_dir / "system.md").exists()
    assert not (knowledge_dir / "business.md").exists()
    assert "共享规则知识请优先参考" in (
        feishu_dir / "MEMORY.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_existing_feishu_workspace_backfills_shared_knowledge(
    monkeypatch,
    tmp_path,
):
    """Existing Feishu workspaces should be healed on next access."""
    _write_default_workspace(tmp_path)
    feishu_dir = tmp_path / ".hubos" / "workspaces" / "feishu_ou_existing"
    feishu_dir.mkdir(parents=True, exist_ok=True)
    (feishu_dir / "agent.json").write_text(
        json.dumps(
            {
                "id": "feishu_ou_existing",
                "name": "Feishu Existing",
                "workspace_dir": str(feishu_dir),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (feishu_dir / "memory").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(mam_mod.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(mam_mod, "Workspace", _DummyWorkspace)

    manager = mam_mod.MultiAgentManager()
    workspace = await manager.get_or_create_feishu_workspace("ou_existing")

    assert workspace is not None
    knowledge_dir = feishu_dir / "memory" / "knowledge"
    assert (knowledge_dir / "tools.md").exists()
    assert (knowledge_dir / "system.md").exists()
    assert not (knowledge_dir / "business.md").exists()


def test_feishu_workspace_id_sanitizes_untrusted_sender_id() -> None:
    assert (
        mam_mod.feishu_workspace_id_for_open_id("ou_safe-123")
        == "feishu_ou_safe-123"
    )

    workspace_id = mam_mod.feishu_workspace_id_for_open_id("../Alice#abc")

    assert workspace_id.startswith("feishu_")
    assert "/" not in workspace_id
    assert "." not in workspace_id
    assert "#" not in workspace_id
