"""Tests for agent tool permission guard."""

import tempfile
from pathlib import Path

import pytest

from hubos.core.infra.agent_registry import AgentRegistry, ModelProvider, AgentStatus, RiskLevel
from hubos.core.infra.agent_tool_guard import (
    AgentToolGuard,
    ToolErrorCode,
    ToolPermissionError,
    ToolPermissionResult,
    check_agent_tool_permission,
    enforce_tool_permission,
)


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    Path(db_path).unlink(missing_ok=True)


@pytest.fixture
def registry(temp_db):
    """Create agent registry with temp database."""
    return AgentRegistry(db_path=temp_db)


@pytest.fixture
def guard(registry):
    """Create tool guard with registry."""
    return AgentToolGuard(agent_registry=registry)


class TestToolPermissionCheck:
    """Test tool permission checking."""

    def test_allowed_tool(self, guard, registry):
        """Test allowed tool returns ALLOWED."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1", "tool2"],
        )

        result = guard.check_permission(agent.agent_id, "tool1")

        assert result.allowed is True
        assert result.error_code == ToolErrorCode.ALLOWED

    def test_disallowed_tool(self, guard, registry):
        """Test disallowed tool returns TOOL_NOT_ALLOWED."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )

        result = guard.check_permission(agent.agent_id, "tool2")

        assert result.allowed is False
        assert result.error_code == ToolErrorCode.TOOL_NOT_ALLOWED

    def test_nonexistent_agent(self, guard):
        """Test nonexistent agent returns AGENT_NOT_FOUND."""
        result = guard.check_permission("nonexistent-id", "tool1")

        assert result.allowed is False
        assert result.error_code == ToolErrorCode.AGENT_NOT_FOUND

    def test_disabled_agent(self, guard, registry):
        """Test disabled agent returns AGENT_DISABLED."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )
        registry.disable_agent(agent.agent_id)

        result = guard.check_permission(agent.agent_id, "tool1")

        assert result.allowed is False
        assert result.error_code == ToolErrorCode.AGENT_DISABLED

    def test_approval_required_action(self, guard, registry):
        """Test approval required action."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
            approval_required_actions=["dangerous_action"],
        )

        result = guard.check_permission(
            agent.agent_id, "tool1", context={"action": "dangerous_action"}
        )

        assert result.allowed is False
        assert result.error_code == ToolErrorCode.TOOL_APPROVAL_REQUIRED
        assert result.requires_approval is True
        assert result.approval_action == "dangerous_action"

    def test_action_not_in_approval_list(self, guard, registry):
        """Test action not in approval list is allowed."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
            approval_required_actions=["dangerous_action"],
        )

        result = guard.check_permission(
            agent.agent_id, "tool1", context={"action": "safe_action"}
        )

        assert result.allowed is True

    def test_high_risk_tool_exceeds_agent_level(self, guard, registry):
        """Test high risk tool is blocked for low risk agent."""
        agent = registry.create_agent(
            name="Test Agent",
            role="info",
            goal="Info only",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["database_delete"],  # critical risk
            risk_level=RiskLevel.LOW,  # but agent is low risk
        )

        result = guard.check_permission(agent.agent_id, "database_delete")

        assert result.allowed is False
        assert result.error_code == ToolErrorCode.TOOL_RISK_LEVEL_HIGH

    def test_high_risk_tool_allowed_for_high_risk_agent(self, guard, registry):
        """Test high risk tool is allowed for high risk agent."""
        agent = registry.create_agent(
            name="Test Agent",
            role="ceo",
            goal="Full access",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["database_delete"],
            risk_level=RiskLevel.CRITICAL,
        )

        result = guard.check_permission(agent.agent_id, "database_delete")

        assert result.allowed is True


@pytest.fixture
def _hubos_logger_propagating():
    """Temporarily enable propagation on the ``hubos`` namespace logger so
    pytest's ``caplog`` (which attaches its handler to the root logger) can
    capture records emitted by ``hubos.*`` modules.

    ``hubos.utils.logging.setup_logger`` sets ``propagate = False`` on
    ``logging.getLogger("hubos")`` so end-user CLI output is formatted
    exclusively by the package's own handler — that's correct behaviour in
    production but incompatible with ``caplog`` out of the box.  This
    fixture flips propagation for the scope of the test and restores the
    original value when the test exits.
    """
    import logging as _logging

    logger = _logging.getLogger("hubos")
    previous = logger.propagate
    logger.propagate = True
    try:
        yield
    finally:
        logger.propagate = previous


class TestToolPermissionAuditLog:
    """Test that tool permission checks are logged."""

    def test_denied_logged_with_warning(
        self, guard, registry, caplog, _hubos_logger_propagating
    ):
        """Test that denied permissions are logged as warnings."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )

        with caplog.at_level("WARNING", logger="hubos"):
            guard.check_permission(agent.agent_id, "tool2")

        assert "Tool permission denied" in caplog.text

    def test_allowed_logged_with_info(
        self, guard, registry, caplog, _hubos_logger_propagating
    ):
        """Test that allowed permissions are logged as info."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )

        with caplog.at_level("INFO", logger="hubos"):
            guard.check_permission(agent.agent_id, "tool1")

        assert "Tool permission granted" in caplog.text


class TestEnforceToolPermission:
    """Test enforce_tool_permission function."""

    def test_enforce_allows_valid(self, guard, registry):
        """Test enforce allows valid permission."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )

        # Should not raise
        enforce_tool_permission(agent.agent_id, "tool1", agent_registry=registry)

    def test_enforce_raises_on_denial(self, guard, registry):
        """Test enforce raises exception on denial."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )

        with pytest.raises(ToolPermissionError) as exc_info:
            enforce_tool_permission(agent.agent_id, "tool2", agent_registry=registry)

        assert exc_info.value.error_code == ToolErrorCode.TOOL_NOT_ALLOWED


class TestConvenienceFunction:
    """Test convenience function."""

    def test_check_permission_function(self, registry):
        """Test convenience function."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )

        result = check_agent_tool_permission(agent.agent_id, "tool1", agent_registry=registry)

        assert result.allowed is True


class TestGetExecutionLimits:
    """Test getting execution limits."""

    def test_get_execution_limits(self, guard, registry):
        """Test getting execution limits."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            timeout_seconds=600,
            retry_count=5,
            max_subagents=3,
            allowed_tools=["tool1"],
        )

        limits = guard.get_execution_limits(agent.agent_id)

        assert limits is not None
        assert limits["timeout_seconds"] == 600
        assert limits["retry_count"] == 5
        assert limits["max_subagents"] == 3

    def test_get_execution_limits_nonexistent(self, guard):
        """Test getting limits for nonexistent agent."""
        limits = guard.get_execution_limits("nonexistent")
        assert limits is None


class TestHighRiskToolsMapping:
    """Test high-risk tools mapping."""

    def test_critical_risk_tools(self, guard):
        """Test critical risk tools are properly mapped."""
        critical_tools = ["database_delete", "config_global_write", "tenant_delete"]
        for tool in critical_tools:
            risk = guard._get_tool_risk_level(tool)
            assert risk == "critical"

    def test_high_risk_tools(self, guard):
        """Test high risk tools are properly mapped."""
        high_risk = ["database_write", "file_delete", "policy_full_rollback", "plugin_install", "plugin_uninstall"]
        for tool in high_risk:
            risk = guard._get_tool_risk_level(tool)
            assert risk == "high"

    def test_medium_risk_tools(self, guard):
        """Test medium risk tools are properly mapped."""
        medium_risk = ["dlq_bulk_discard"]
        for tool in medium_risk:
            risk = guard._get_tool_risk_level(tool)
            assert risk == "medium"

    def test_unknown_tool_low_risk(self, guard):
        """Test unknown tools default to low risk."""
        risk = guard._get_tool_risk_level("unknown_tool")
        assert risk == "low"


class TestDynamicToolPolicy:
    """Test dynamic tool policy from settings."""

    def test_globally_disabled_tool_is_blocked(self, registry):
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )
        guard = AgentToolGuard(
            agent_registry=registry,
            tool_policies=[{"id": "tool1", "enabled": False, "risk": "low", "approval_required": False}],
        )
        result = guard.check_permission(agent.agent_id, "tool1")
        assert result.allowed is False
        assert result.error_code == ToolErrorCode.TOOL_DISABLED

    def test_policy_approval_required_blocks(self, registry):
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )
        guard = AgentToolGuard(
            agent_registry=registry,
            tool_policies=[{"id": "tool1", "enabled": True, "risk": "low", "approval_required": True}],
        )
        result = guard.check_permission(agent.agent_id, "tool1")
        assert result.allowed is False
        assert result.error_code == ToolErrorCode.TOOL_APPROVAL_REQUIRED
