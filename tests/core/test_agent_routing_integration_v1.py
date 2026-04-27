# -*- coding: utf-8 -*-
"""Tests for agent routing integration V1."""

import tempfile
from pathlib import Path

import pytest

from hubos.core.infra.agent_registry import AgentRegistry, ModelProvider


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


class TestAgentRoutingIntegration:
    """Test agent routing integration scenarios."""

    def test_route_task_to_agent_by_tag(self, registry):
        """Test that a task with tags routes to correct agent."""
        # Create agents with different routing tags
        registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Development",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding", "development"],
        )
        registry.create_agent(
            name="Info Agent",
            role="info",
            goal="Information",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["research", "information"],
        )

        # Route by development tag
        agent = registry.get_agent_by_tags(["development"])
        assert agent is not None
        assert agent.name == "Dev Agent"

        # Route by information tag
        agent = registry.get_agent_by_tags(["information"])
        assert agent is not None
        assert agent.name == "Info Agent"

    def test_route_task_with_multiple_tags(self, registry):
        """Test routing when task has multiple tags."""
        registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Development",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding", "development", "testing"],
        )

        # First tag match
        agent = registry.get_agent_by_tags(["coding", "research"])
        assert agent is not None
        assert agent.role == "dev"

    def test_disabled_agent_not_routed(self, registry):
        """Test that disabled agents are not selected for routing."""
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Development",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding"],
        )

        # Should be found when enabled
        found = registry.get_agent_by_tags(["coding"])
        assert found is not None

        # Disable and try again
        registry.disable_agent(agent.agent_id)
        found = registry.get_agent_by_tags(["coding"])
        assert found is None

    def test_agent_execution_config_applied(self, registry):
        """Test that agent execution config is applied correctly."""
        agent = registry.create_agent(
            name="Complex Agent",
            role="dev",
            goal="Complex tasks",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            timeout_seconds=900,
            retry_count=5,
            max_subagents=3,
            allowed_tools=["tool1", "tool2", "tool3"],
        )

        config = registry.get_agent_execution_config(agent.agent_id)

        assert config["timeout_seconds"] == 900
        assert config["retry_count"] == 5
        assert config["max_subagents"] == 3
        assert config["allowed_tools"] == ["tool1", "tool2", "tool3"]

    def test_route_without_matching_tags(self, registry):
        """Test routing when no tags match."""
        registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Development",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding"],
        )

        # No matching tags
        agent = registry.get_agent_by_tags(["nonexistent"])
        assert agent is None

    def test_route_empty_tags(self, registry):
        """Test routing with empty tags list."""
        registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Development",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding"],
        )

        agent = registry.get_agent_by_tags([])
        assert agent is None

    def test_chinese_tags_routing(self, registry):
        """Test routing with Chinese tags."""
        registry.create_agent(
            name="CEO Agent",
            role="ceo",
            goal="Coordinate",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["planning", "coordination", "决策", "规划"],
        )

        # Route by Chinese tag
        agent = registry.get_agent_by_tags(["决策"])
        assert agent is not None
        assert agent.role == "ceo"

        agent = registry.get_agent_by_tags(["规划"])
        assert agent is not None
        assert agent.role == "ceo"

    def test_mixed_language_tags(self, registry):
        """Test routing with mixed language tags."""
        registry.create_agent(
            name="Info Agent",
            role="info",
            goal="Research",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["research", "information", "查询", "信息", "搜索"],
        )

        # English tag
        agent = registry.get_agent_by_tags(["research"])
        assert agent is not None
        assert agent.role == "info"

        # Chinese tag
        agent = registry.get_agent_by_tags(["查询"])
        assert agent is not None
        assert agent.role == "info"

    def test_route_by_role(self, registry):
        """Test routing by role."""
        registry.create_agent(
            name="CEO Agent",
            role="ceo",
            goal="Coordinate",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )
        registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Develop",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )

        ceo = registry.get_agent_by_role("ceo")
        assert ceo is not None
        assert ceo.name == "CEO Agent"

        dev = registry.get_agent_by_role("dev")
        assert dev is not None
        assert dev.name == "Dev Agent"

    def test_role_routing_disabled_returns_none(self, registry):
        """Test that disabled agent is not returned by role routing."""
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Develop",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
        )
        registry.disable_agent(agent.agent_id)

        found = registry.get_agent_by_role("dev")
        assert found is None

    def test_full_workflow_create_route_use(self, registry):
        """Test full workflow: create agent, route to it, use its config."""
        # 1. Create agent via bootstrap or directly
        agent = registry.create_agent(
            name="Test Agent",
            role="tester",
            goal="Run tests",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["testing", "qa"],
            timeout_seconds=300,
            retry_count=2,
            allowed_tools=["test_run", "test_report"],
        )

        # 2. Route task to agent
        routed = registry.get_agent_by_tags(["testing"])
        assert routed is not None
        assert routed.agent_id == agent.agent_id

        # 3. Get execution config
        config = registry.get_agent_execution_config(agent.agent_id)
        assert config["timeout_seconds"] == 300
        assert config["retry_count"] == 2
        assert "test_run" in config["allowed_tools"]

    def test_multiple_agents_same_role(self, registry):
        """Test routing when multiple agents have same role (different tenants)."""
        registry.create_agent(
            name="Dev Agent 1",
            role="dev",
            goal="Dev",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            created_by="tenant-1",
        )
        registry.create_agent(
            name="Dev Agent 2",
            role="dev",
            goal="Dev",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            created_by="tenant-2",
        )

        # Should return first enabled one
        found = registry.get_agent_by_role("dev")
        assert found is not None
        # Order is not deterministic, but should be one of them
        assert found.role == "dev"


class TestAgentRoutingWithDisabled:
    """Test routing edge cases with disabled agents."""

    def test_all_agents_disabled(self, registry):
        """Test routing when all agents are disabled."""
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Dev",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding"],
        )
        registry.disable_agent(agent.agent_id)

        found = registry.get_agent_by_tags(["coding"])
        assert found is None

    def test_disable_then_enable(self, registry):
        """Test disable then enable restores routing."""
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Dev",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding"],
        )

        # Verify enabled
        found = registry.get_agent_by_tags(["coding"])
        assert found is not None

        # Disable
        registry.disable_agent(agent.agent_id)
        found = registry.get_agent_by_tags(["coding"])
        assert found is None

        # Re-enable
        registry.enable_agent(agent.agent_id)
        found = registry.get_agent_by_tags(["coding"])
        assert found is not None
        assert found.agent_id == agent.agent_id

    def test_update_tags_after_disable(self, registry):
        """Test updating tags on disabled agent."""
        agent = registry.create_agent(
            name="Dev Agent",
            role="dev",
            goal="Dev",
            model_provider=ModelProvider.OPENAI,
            model_name="gpt-4",
            routing_tags=["coding"],
        )
        registry.disable_agent(agent.agent_id)

        # Add new tag
        registry.update_agent(
            agent.agent_id,
            routing_tags=["coding", "new-tag"],
        )

        # Should still not be found with either tag (disabled)
        found = registry.get_agent_by_tags(["coding"])
        assert found is None
        found = registry.get_agent_by_tags(["new-tag"])
        assert found is None
