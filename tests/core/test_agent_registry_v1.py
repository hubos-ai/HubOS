# -*- coding: utf-8 -*-
"""Tests for Agent Registry V1."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from hubos.core.infra.agent_registry import (
    Agent,
    AgentRegistry,
    AgentStatus,
    DEFAULT_TEMPLATES,
    ModelProvider,
    RiskLevel,
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


class TestAgentRegistryInit:
    """Test agent registry initialization."""

    def test_init_creates_database(self, temp_db):
        """Test that init creates database schema."""
        registry = AgentRegistry(db_path=temp_db)
        import sqlite3

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'",
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_init_creates_indexes(self, temp_db):
        """Test that init creates indexes."""
        registry = AgentRegistry(db_path=temp_db)
        import sqlite3

        conn = sqlite3.connect(temp_db)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_agents_role'",
        )
        assert cursor.fetchone() is not None
        conn.close()


class TestAgentCRUD:
    """Test agent CRUD operations."""

    def test_create_agent(self, registry):
        """Test creating an agent."""
        agent = registry.create_agent(
            name="Test Agent",
            role="tester",
            goal="Run tests",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["test_run", "test_read"],
            routing_tags=["testing", "qa"],
        )

        assert agent.agent_id is not None
        assert agent.name == "Test Agent"
        assert agent.role == "tester"
        assert agent.goal == "Run tests"
        assert agent.model_provider == ModelProvider.OPENAI
        assert agent.model_name == "gpt-4"
        assert agent.allowed_tools == ["test_run", "test_read"]
        assert agent.routing_tags == ["testing", "qa"]
        assert agent.status == AgentStatus.ENABLED
        assert agent.version == "1.0"

    def test_get_agent(self, registry):
        """Test getting an agent by ID."""
        created = registry.create_agent(
            name="Test Agent",
            role="tester",
            goal="Run tests",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )

        retrieved = registry.get_agent(created.agent_id)

        assert retrieved is not None
        assert retrieved.agent_id == created.agent_id
        assert retrieved.name == created.name

    def test_get_nonexistent_agent(self, registry):
        """Test getting nonexistent agent returns None."""
        result = registry.get_agent("nonexistent-id")
        assert result is None

    def test_list_agents(self, registry):
        """Test listing all agents."""
        registry.create_agent(
            name="Agent 1",
            role="dev",
            goal="Goal 1",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )
        registry.create_agent(
            name="Agent 2",
            role="dev",
            goal="Goal 2",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-3.5",
        )

        agents = registry.list_agents()

        assert len(agents) == 2

    def test_list_agents_filter_by_role(self, registry):
        """Test listing agents filtered by role."""
        registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Dev",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )
        registry.create_agent(
            name="Info Agent",
            role="info",
            goal="Info",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )

        dev_agents = registry.list_agents(role="dev")
        info_agents = registry.list_agents(role="info")

        assert len(dev_agents) == 1
        assert dev_agents[0].name == "Dev Agent"
        assert len(info_agents) == 1
        assert info_agents[0].name == "Info Agent"

    def test_list_agents_filter_by_status(self, registry):
        """Test listing agents filtered by status."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )
        registry.disable_agent(agent.agent_id)

        enabled = registry.list_agents(status=AgentStatus.ENABLED)
        disabled = registry.list_agents(status=AgentStatus.DISABLED)

        assert len(enabled) == 0
        assert len(disabled) == 1

    def test_update_agent(self, registry):
        """Test updating an agent."""
        agent = registry.create_agent(
            name="Original Name",
            role="dev",
            goal="Original Goal",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )

        updated = registry.update_agent(
            agent.agent_id,
            name="Updated Name",
            goal="Updated Goal",
            timeout_seconds=600,
        )

        assert updated is not None
        assert updated.name == "Updated Name"
        assert updated.goal == "Updated Goal"
        assert updated.timeout_seconds == 600
        # Unchanged fields
        assert updated.role == "dev"

    def test_delete_agent(self, registry):
        """Test deleting an agent."""
        agent = registry.create_agent(
            name="To Delete",
            role="dev",
            goal="Delete me",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )

        result = registry.delete_agent(agent.agent_id)

        assert result is True
        assert registry.get_agent(agent.agent_id) is None

    def test_delete_nonexistent_agent(self, registry):
        """Test deleting nonexistent agent returns False."""
        result = registry.delete_agent("nonexistent-id")
        assert result is False


class TestAgentEnableDisable:
    """Test agent enable/disable operations."""

    def test_enable_agent(self, registry):
        """Test enabling an agent."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )
        registry.disable_agent(agent.agent_id)

        enabled = registry.enable_agent(agent.agent_id)

        assert enabled is not None
        assert enabled.status == AgentStatus.ENABLED

    def test_disable_agent(self, registry):
        """Test disabling an agent."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )

        disabled = registry.disable_agent(agent.agent_id)

        assert disabled is not None
        assert disabled.status == AgentStatus.DISABLED

    def test_disable_affects_routing(self, registry):
        """Test that disabled agents don't appear in routing."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["test"],
        )

        # Should be found when enabled
        found = registry.get_agent_by_tags(["test"])
        assert found is not None
        assert found.agent_id == agent.agent_id

        # Should not be found when disabled
        registry.disable_agent(agent.agent_id)
        found = registry.get_agent_by_tags(["test"])
        assert found is None


class TestAgentRouting:
    """Test agent routing functionality."""

    def test_get_agent_by_tags(self, registry):
        """Test getting agent by tags."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["development", "coding"],
        )

        found = registry.get_agent_by_tags(["coding"])

        assert found is not None
        assert found.agent_id == agent.agent_id

    def test_get_agent_by_tags_no_match(self, registry):
        """Test getting agent by tags with no match."""
        registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["development"],
        )

        found = registry.get_agent_by_tags(["nonexistent"])

        assert found is None

    def test_get_agent_by_tags_multiple_agents(self, registry):
        """Test getting agent by tags when multiple agents match."""
        registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Dev",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["development", "coding"],
        )
        registry.create_agent(
            name="Info Agent",
            role="info",
            goal="Info",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["information", "research"],
        )

        found = registry.get_agent_by_tags(["development", "information"])

        assert found is not None
        # Should return first enabled match

    def test_get_agent_by_role(self, registry):
        """Test getting agent by role."""
        agent = registry.create_agent(
            name="Test Agent",
            role="ceo",
            goal="Coordinate",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )

        found = registry.get_agent_by_role("ceo")

        assert found is not None
        assert found.agent_id == agent.agent_id

    def test_get_agent_by_role_not_found(self, registry):
        """Test getting agent by role when not found."""
        found = registry.get_agent_by_role("nonexistent")
        assert found is None


class TestAgentExecutionConfig:
    """Test agent execution configuration."""

    def test_get_agent_execution_config(self, registry):
        """Test getting execution config for agent."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            timeout_seconds=600,
            retry_count=5,
            max_subagents=3,
            allowed_tools=["tool1", "tool2"],
        )

        config = registry.get_agent_execution_config(agent.agent_id)

        assert config is not None
        assert config["timeout_seconds"] == 600
        assert config["retry_count"] == 5
        assert config["max_subagents"] == 3
        assert config["allowed_tools"] == ["tool1", "tool2"]

    def test_get_agent_execution_config_not_found(self, registry):
        """Test getting execution config for nonexistent agent."""
        config = registry.get_agent_execution_config("nonexistent-id")
        assert config is None


class TestAgentToolPermission:
    """Test tool permission validation."""

    def test_check_tool_permission_allowed(self, registry):
        """Test allowed tool permission."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1", "tool2"],
        )

        allowed, error = registry.check_tool_permission(
            agent.agent_id,
            "tool1",
        )

        assert allowed is True
        assert error is None

    def test_check_tool_permission_denied(self, registry):
        """Test denied tool permission."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )

        allowed, error = registry.check_tool_permission(
            agent.agent_id,
            "tool2",
        )

        assert allowed is False
        assert error == "TOOL_NOT_ALLOWED"

    def test_check_tool_permission_agent_not_found(self, registry):
        """Test tool permission with nonexistent agent."""
        allowed, error = registry.check_tool_permission("nonexistent", "tool1")

        assert allowed is False
        assert error == "AGENT_NOT_FOUND"

    def test_check_tool_permission_agent_disabled(self, registry):
        """Test tool permission with disabled agent."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
        )
        registry.disable_agent(agent.agent_id)

        allowed, error = registry.check_tool_permission(
            agent.agent_id,
            "tool1",
        )

        assert allowed is False
        assert error == "AGENT_DISABLED"

    def test_check_tool_permission_approval_required(self, registry):
        """Test tool permission when approval is required."""
        agent = registry.create_agent(
            name="Test Agent",
            role="dev",
            goal="Test",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            allowed_tools=["tool1"],
            approval_required_actions=["dangerous_action"],
        )

        allowed, error = registry.check_tool_permission(
            agent.agent_id,
            "tool1",
            action="dangerous_action",
        )

        assert allowed is False
        assert error == "APPROVAL_REQUIRED"


class TestAgentTemplates:
    """Test default agent templates."""

    def test_default_templates_exist(self):
        """Test that all default templates are defined."""
        assert "ceo" in DEFAULT_TEMPLATES
        assert "info" in DEFAULT_TEMPLATES
        assert "dev" in DEFAULT_TEMPLATES
        assert "review" in DEFAULT_TEMPLATES

    def test_ceo_template_fields(self):
        """Test CEO template has correct fields."""
        template = DEFAULT_TEMPLATES["ceo"]
        assert template.role == "ceo"
        assert template.risk_level == RiskLevel.HIGH
        assert "agent_dispatch" in template.allowed_tools
        assert "planning" in template.routing_tags
        assert "coordination" in template.routing_tags

    def test_info_template_fields(self):
        """Test Info template has correct fields."""
        template = DEFAULT_TEMPLATES["info"]
        assert template.role == "info"
        assert template.risk_level == RiskLevel.LOW
        assert "web_search" in template.allowed_tools
        assert "research" in template.routing_tags

    def test_dev_template_fields(self):
        """Test Dev template has correct fields."""
        template = DEFAULT_TEMPLATES["dev"]
        assert template.role == "dev"
        assert template.risk_level == RiskLevel.MEDIUM
        assert "code_generate" in template.allowed_tools
        assert "development" in template.routing_tags

    def test_review_template_fields(self):
        """Test Review template has correct fields."""
        template = DEFAULT_TEMPLATES["review"]
        assert template.role == "review"
        assert template.risk_level == RiskLevel.LOW
        assert "code_review" in template.allowed_tools
        assert "review" in template.routing_tags
