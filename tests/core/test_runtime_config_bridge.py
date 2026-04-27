# -*- coding: utf-8 -*-
"""Tests for runtime config bridge and integration helpers."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from hubos.core.infra.agent_registry import (
    AgentRegistry,
    ModelProvider,
    RiskLevel,
)
from hubos.core.infra.runtime_config import (
    apply_settings_to_registry,
    build_effective_runtime_config,
    build_permissions_matrix,
    check_channel_connection,
    check_model_connection,
)


def _base_settings() -> dict:
    return {
        "models": [
            {
                "id": "openai_default",
                "provider": "openai",
                "model": "gpt-4o",
                "enabled": True,
                "api_key_env": "OPENAI_API_KEY",
                "timeout_seconds": 1,
            },
        ],
        "channels": [
            {
                "id": "webhook",
                "type": "webhook",
                "enabled": True,
                "endpoint": "/webhook",
                "auth": "api_key",
            },
        ],
        "tools": [
            {
                "id": "web_search",
                "enabled": True,
                "risk": "low",
                "approval_required": False,
            },
            {
                "id": "code_execute",
                "enabled": False,
                "risk": "high",
                "approval_required": True,
            },
        ],
        "workflows": [
            {
                "id": "one_person_default",
                "enabled": True,
                "default": True,
                "max_parallel_subagents": 1,
            },
        ],
    }


def test_apply_settings_updates_agents() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        registry = AgentRegistry(db_path=db_path)
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="ship",
            model_provider=ModelProvider.ANTHROPIC,
            model_name="claude-3",
            allowed_tools=["web_search", "code_execute"],
            max_subagents=3,
            risk_level=RiskLevel.MEDIUM,
        )

        summary = apply_settings_to_registry(registry, _base_settings())
        updated = registry.get_agent(agent.agent_id)

        assert updated is not None
        assert updated.model_provider.value == "openai"
        assert updated.model_name == "gpt-4o"
        assert updated.allowed_tools == ["web_search"]
        assert updated.max_subagents == 1
        assert summary.updated_agents == 1
        assert summary.model_overrides == 1
        assert summary.subagent_caps == 1
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_effective_runtime_config_snapshot() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        registry = AgentRegistry(db_path=db_path)
        registry.bootstrap_defaults()
        cfg = build_effective_runtime_config(registry, _base_settings())
        assert cfg["models"]["enabled_count"] == 1
        assert cfg["tools"]["enabled_tools"] == ["web_search"]
        assert cfg["workflows"]["default_parallel_limit"] == 1
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_permissions_matrix_uses_tool_policy() -> None:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        registry = AgentRegistry(db_path=db_path)
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="ship",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4o",
            allowed_tools=["web_search", "code_execute"],
            risk_level=RiskLevel.MEDIUM,
        )

        matrix = build_permissions_matrix(registry, _base_settings())
        row = next(
            r for r in matrix["rows"] if r["agent_id"] == agent.agent_id
        )
        assert row["permissions"]["web_search"]["allowed"] is True
        assert row["permissions"]["code_execute"]["allowed"] is False
        assert (
            row["permissions"]["code_execute"]["error_code"] == "TOOL_DISABLED"
        )
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_model_connection_fails_without_key() -> None:
    cfg = {
        "id": "openai_default",
        "provider": "openai",
        "enabled": True,
        "api_key_env": "MISSING_API_KEY",
        "timeout_seconds": 1,
    }
    result = check_model_connection(cfg)
    assert result["ok"] is False
    assert "Missing env key" in result["message"]


def test_model_connection_http_success() -> None:
    cfg = {
        "id": "openai_default",
        "provider": "openai",
        "enabled": True,
        "api_key_env": "OPENAI_API_KEY",
        "timeout_seconds": 1,
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return None

        def getcode(self):
            return 200

    with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}):
        with patch("urllib.request.urlopen", return_value=FakeResponse()):
            result = check_model_connection(cfg)
    assert result["ok"] is True


def test_channel_connection_local_endpoint() -> None:
    result = check_channel_connection(
        {
            "id": "webhook",
            "type": "webhook",
            "enabled": True,
            "endpoint": "/webhook",
        },
    )
    assert result["ok"] is True


def test_channel_connection_invalid_endpoint() -> None:
    result = check_channel_connection(
        {
            "id": "wechat",
            "type": "wechat",
            "enabled": True,
            "endpoint": "not-a-url",
        },
    )
    assert result["ok"] is False
    assert "Invalid endpoint format" in result["message"]
