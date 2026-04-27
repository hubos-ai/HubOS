# -*- coding: utf-8 -*-
"""Tests for policy routing integration."""

from uuid import uuid4
from unittest.mock import MagicMock

import pytest

from hubos.core.orchestrator.policy_router import PolicyRouter
from hubos.core.schemas.memory import RouteHint, LearnedPolicy


class TestPolicyRouter:
    """Tests for PolicyRouter."""

    @pytest.fixture
    def mock_reflection_engine(self):
        """Create a mock reflection engine."""
        engine = MagicMock()
        engine.generate_route_hint.return_value = None
        return engine

    @pytest.fixture
    def router(self, mock_reflection_engine) -> PolicyRouter:
        """Create a policy router with mock engine."""
        return PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

    def test_disabled_router_returns_unchanged(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that disabled router doesn't apply hints."""
        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=False,
        )

        plan_params = {"worker_priority": ["openai"]}
        result = router.apply_route_hint({"query": "test"}, plan_params)

        assert result["worker_priority"] == ["openai"]
        mock_reflection_engine.generate_route_hint.assert_not_called()

    def test_route_hint_applies_worker_priority(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that route hint applies worker priority."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            worker_priority=["claude", "openai"],
            timeout_seconds=600,
            confidence=0.8,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {}  # No current priority
        result = router.apply_route_hint({"query": "test"}, plan_params)

        assert result["worker_priority"] == ["claude", "openai"]

    def test_route_hint_applies_skip_providers(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that route hint applies skip providers."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            skip_providers=["anthropic"],
            confidence=0.7,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {"skip_providers": []}
        result = router.apply_route_hint({"query": "test"}, plan_params)

        assert "anthropic" in result["skip_providers"]

    def test_route_hint_applies_timeout(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that route hint applies timeout."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            timeout_seconds=600,  # Non-default
            confidence=0.8,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {"timeout_seconds": 300}  # Default
        result = router.apply_route_hint({"query": "test"}, plan_params)

        assert result["timeout_seconds"] == 600

    def test_route_hint_applies_retry_count(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that route hint applies retry count."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            retry_count=5,
            confidence=0.7,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {}
        result = router.apply_route_hint({"query": "test"}, plan_params)

        assert result["retry_count"] == 5

    def test_route_hint_applies_parallel_mode(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that route hint applies parallel mode."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            parallel=True,
            confidence=0.8,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {"parallel": False}
        result = router.apply_route_hint({"query": "test"}, plan_params)

        assert result["parallel"] is True

    def test_existing_worker_priority_not_overwritten(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that existing worker priority is not overwritten."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            worker_priority=["claude", "openai"],
            confidence=0.8,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {"worker_priority": ["openai"]}  # Already set
        result = router.apply_route_hint({"query": "test"}, plan_params)

        # Should NOT overwrite existing
        assert result["worker_priority"] == ["openai"]

    def test_route_hint_stored_in_params(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that route hint is stored for effectiveness tracking."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            confidence=0.8,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {}
        result = router.apply_route_hint({"query": "test"}, plan_params)

        assert "_route_hint" in result
        assert result["_route_hint"] is hint

    def test_record_execution_outcome(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test recording execution outcome for effectiveness."""
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            confidence=0.8,
        )

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {"_route_hint": hint}
        router.record_execution_outcome(
            plan_params,
            was_successful=True,
            confidence=0.9,
        )

        mock_reflection_engine.record_policy_effectiveness.assert_called_once()

    def test_record_outcome_ignores_missing_hint(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test that missing route hint doesn't cause error."""
        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        plan_params = {}  # No _route_hint
        router.record_execution_outcome(
            plan_params,
            was_successful=True,
            confidence=0.9,
        )

        # Should not call record_policy_effectiveness
        mock_reflection_engine.record_policy_effectiveness.assert_not_called()

    def test_get_metrics(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test getting policy router metrics."""
        # Set up some hits
        hint = RouteHint(
            trigger_task_id="trigger-1",
            policy_id=uuid4(),
            confidence=0.8,
        )
        mock_reflection_engine.generate_route_hint.return_value = hint

        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        # Apply some hints
        for _ in range(3):
            router.apply_route_hint({"query": "test"}, {})

        # Record some outcomes
        router.record_execution_outcome(
            {"_route_hint": hint},
            was_successful=True,
            confidence=0.9,
        )

        metrics = router.get_metrics()

        assert "policy_hit_count" in metrics
        assert "policy_hit_rate" in metrics
        assert "policy_effective_rate" in metrics

    def test_set_enabled(
        self,
        mock_reflection_engine,
    ) -> None:
        """Test enabling/disabling router."""
        router = PolicyRouter(
            reflection_engine=mock_reflection_engine,
            enabled=True,
        )

        router.set_enabled(False)
        assert router.is_enabled is False

        router.set_enabled(True)
        assert router.is_enabled is True


class TestPolicyRouterIntegration:
    """Integration tests for policy router with real reflection engine."""

    def test_full_policy_lifecycle(self) -> None:
        """Test complete policy hit lifecycle."""
        # Create real reflection engine
        from hubos.core.orchestrator.reflection_engine import (
            ReflectionEngine,
            TaskContext,
        )
        from hubos.core.schemas.tasks import TaskResult, TaskStatus

        engine = ReflectionEngine()
        router = PolicyRouter(reflection_engine=engine, enabled=True)

        # First, reflect on a task to create a policy
        context = TaskContext(
            task_id="task-policy-lifecycle",
            session_id="session-1",
            trace_id="trace-lifecycle",
            task_input={"query": "policy test", "type": "search"},
            execution_trace=[
                {"step": 1, "worker": "openai", "success": True},
            ],
            task_result=TaskResult(
                unit_id=uuid4(),
                task_id="task-policy-lifecycle",
                status=TaskStatus.SUCCESS,
                confidence=0.9,
                output_data={},
                trace_id="trace-lifecycle",
            ),
        )
        engine.reflect(context)

        # Now apply route hint
        plan_params = {}
        result = router.apply_route_hint(
            {"query": "policy test", "type": "search"},
            plan_params,
        )

        # Should have applied something (may or may not depending on trigger matching)
        # This is just a basic integration test

        # Record outcome
        if "_route_hint" in result:
            router.record_execution_outcome(
                result,
                was_successful=True,
                confidence=0.9,
            )

        # Get metrics
        metrics = router.get_metrics()
        assert "enabled" in metrics
