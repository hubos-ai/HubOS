"""Tests for agent template bootstrap."""

import tempfile
from pathlib import Path

import pytest

from hubos.core.infra.agent_registry import AgentRegistry, DEFAULT_TEMPLATES


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


class TestBootstrapDefaults:
    """Test bootstrapping default agent templates."""

    def test_bootstrap_creates_all_templates(self, registry):
        """Test that bootstrap creates all 4 default templates."""
        agents = registry.bootstrap_defaults(created_by="test-tenant")

        assert len(agents) == 4

        roles = {a.role for a in agents}
        assert "ceo" in roles
        assert "info" in roles
        assert "dev" in roles
        assert "review" in roles

    def test_bootstrap_is_idempotent(self, registry):
        """Test that bootstrap is idempotent."""
        # First bootstrap
        agents1 = registry.bootstrap_defaults(created_by="test-tenant")
        count1 = len(agents1)

        # Second bootstrap should return same agents
        agents2 = registry.bootstrap_defaults(created_by="test-tenant")
        count2 = len(agents2)

        assert count1 == count2
        assert count1 == 4

        # Same agents should be returned
        agent_ids1 = {a.agent_id for a in agents1}
        agent_ids2 = {a.agent_id for a in agents2}
        assert agent_ids1 == agent_ids2

    def test_bootstrap_sets_correct_properties(self, registry):
        """Test that bootstrapped agents have correct properties."""
        agents = registry.bootstrap_defaults(created_by="test-tenant")

        ceo = next(a for a in agents if a.role == "ceo")
        assert ceo.name == "CEO Agent"
        assert ceo.template_id == "ceo"
        assert ceo.created_by == "test-tenant"

        info = next(a for a in agents if a.role == "info")
        assert info.name == "Information Agent"
        assert info.template_id == "info"

        dev = next(a for a in agents if a.role == "dev")
        assert dev.name == "Developer Agent"
        assert dev.template_id == "dev"

        review = next(a for a in agents if a.role == "review")
        assert review.name == "Review Agent"
        assert review.template_id == "review"

    def test_bootstrap_sets_routing_tags(self, registry):
        """Test that bootstrapped agents have routing tags."""
        agents = registry.bootstrap_defaults(created_by="test-tenant")

        ceo = next(a for a in agents if a.role == "ceo")
        assert "planning" in ceo.routing_tags
        assert "coordination" in ceo.routing_tags

        info = next(a for a in agents if a.role == "info")
        assert "research" in info.routing_tags
        assert "information" in info.routing_tags

        dev = next(a for a in agents if a.role == "dev")
        assert "development" in dev.routing_tags
        assert "coding" in dev.routing_tags

        review = next(a for a in agents if a.role == "review")
        assert "review" in review.routing_tags
        assert "quality" in review.routing_tags

    def test_bootstrap_sets_tool_permissions(self, registry):
        """Test that bootstrapped agents have correct tool permissions."""
        agents = registry.bootstrap_defaults(created_by="test-tenant")

        ceo = next(a for a in agents if a.role == "ceo")
        assert "task_create" in ceo.allowed_tools
        assert "agent_dispatch" in ceo.allowed_tools

        info = next(a for a in agents if a.role == "info")
        assert "web_search" in info.allowed_tools
        assert "memory_read" in info.allowed_tools

        dev = next(a for a in agents if a.role == "dev")
        assert "code_generate" in dev.allowed_tools
        assert "file_write" in dev.allowed_tools

        review = next(a for a in agents if a.role == "review")
        assert "code_review" in review.allowed_tools

    def test_bootstrap_preserves_template_settings(self, registry):
        """Test that bootstrapped agents preserve template settings."""
        agents = registry.bootstrap_defaults(created_by="test-tenant")

        ceo = next(a for a in agents if a.role == "ceo")
        assert ceo.max_subagents == 4
        assert ceo.timeout_seconds == 600
        assert ceo.retry_count == 3

        info = next(a for a in agents if a.role == "info")
        assert info.max_subagents == 0
        assert info.timeout_seconds == 120
        assert info.retry_count == 2

    def test_bootstrap_approval_requirements(self, registry):
        """Test that bootstrapped agents have correct approval requirements."""
        agents = registry.bootstrap_defaults(created_by="test-tenant")

        ceo = next(a for a in agents if a.role == "ceo")
        assert "policy:full_rollback" in ceo.approval_required_actions
        assert "tenant:delete" in ceo.approval_required_actions

        dev = next(a for a in agents if a.role == "dev")
        assert "dlq:bulk_discard" in dev.approval_required_actions
        assert "plugin:install:global" in dev.approval_required_actions

        info = next(a for a in agents if a.role == "info")
        assert len(info.approval_required_actions) == 0

        review = next(a for a in agents if a.role == "review")
        assert len(review.approval_required_actions) == 0


class TestCloneFromTemplate:
    """Test cloning agents from templates."""

    def test_clone_from_template(self, registry):
        """Test cloning a single agent from template."""
        agent = registry.clone_from_template("ceo", created_by="test-tenant")

        assert agent is not None
        assert agent.role == "ceo"
        assert agent.template_id == "ceo"
        assert agent.created_by == "test-tenant"

    def test_clone_from_template_with_custom_name(self, registry):
        """Test cloning with custom name."""
        agent = registry.clone_from_template(
            "ceo",
            name="My CEO Agent",
            created_by="test-tenant"
        )

        assert agent is not None
        assert agent.name == "My CEO Agent"
        assert agent.role == "ceo"  # Role preserved from template

    def test_clone_from_template_idempotent(self, registry):
        """Test that cloning is idempotent per template/created_by."""
        agent1 = registry.clone_from_template("ceo", created_by="test-tenant")
        agent2 = registry.clone_from_template("ceo", created_by="test-tenant")

        assert agent1.agent_id == agent2.agent_id

    def test_clone_from_template_different_created_by(self, registry):
        """Test that different created_by can clone same template."""
        agent1 = registry.clone_from_template("ceo", created_by="tenant-1")
        agent2 = registry.clone_from_template("ceo", created_by="tenant-2")

        assert agent1.agent_id != agent2.agent_id

    def test_clone_from_nonexistent_template(self, registry):
        """Test cloning from nonexistent template returns None."""
        agent = registry.clone_from_template("nonexistent", created_by="test")
        assert agent is None

    def test_clone_preserves_all_fields(self, registry):
        """Test that clone preserves all template fields."""
        agent = registry.clone_from_template("dev", created_by="test")

        template = DEFAULT_TEMPLATES["dev"]

        assert agent.name == template.name
        assert agent.role == template.role
        assert agent.goal == template.goal
        assert agent.model_provider == template.model_provider
        assert agent.model_name == template.model_name
        assert agent.allowed_tools == template.allowed_tools
        assert agent.max_subagents == template.max_subagents
        assert agent.timeout_seconds == template.timeout_seconds
        assert agent.retry_count == template.retry_count
        assert agent.risk_level == template.risk_level
        assert agent.approval_required_actions == template.approval_required_actions
        assert agent.routing_tags == template.routing_tags


class TestBootstrapIntegration:
    """Integration tests for bootstrap functionality."""

    def test_bootstrap_then_create_custom_agent(self, registry):
        """Test bootstrapping then creating custom agent."""
        # Bootstrap defaults
        defaults = registry.bootstrap_defaults(created_by="tenant")

        # Create custom agent
        custom = registry.create_agent(
            name="Custom Agent",
            role="custom",
            goal="Custom goal",
            model_provider=registry.list_agents()[0].model_provider,
            model_name="gpt-4",
            created_by="tenant",
        )

        # Should have all 4 defaults + 1 custom = 5
        all_agents = registry.list_agents()
        assert len(all_agents) == 5

        # Custom agent should be present
        custom_found = registry.get_agent(custom.agent_id)
        assert custom_found is not None
        assert custom_found.name == "Custom Agent"

    def test_bootstrap_multiple_tenants(self, registry):
        """Test bootstrapping for multiple tenants."""
        registry.bootstrap_defaults(created_by="tenant-1")
        registry.bootstrap_defaults(created_by="tenant-2")

        # After bootstrapping 2 tenants, we should have 2 CEOs
        all_ceos = registry.list_agents(role="ceo")

        # Should have 2 CEO agents total (one per tenant)
        assert len(all_ceos) == 2

        # They should have different agent IDs
        assert all_ceos[0].agent_id != all_ceos[1].agent_id

        # They should have different created_by values
        created_bys = {a.created_by for a in all_ceos}
        assert "tenant-1" in created_bys
        assert "tenant-2" in created_bys
