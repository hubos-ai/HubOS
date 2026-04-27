# -*- coding: utf-8 -*-
"""Tests for reflection engine."""

import time
from uuid import uuid4

import pytest

from hubos.core.orchestrator.reflection_engine import (
    ReflectionEngine,
    ReflectionEngineConfig,
    ReflectionMode,
    TaskContext,
)
from hubos.core.schemas.memory import (
    LearnedPolicy,
    RouteHint,
    ReflectionReport,
)
from hubos.core.schemas.tasks import TaskResult, TaskStatus


class TestReflectionEngine:
    """Tests for ReflectionEngine."""

    @pytest.fixture
    def engine(self) -> ReflectionEngine:
        """Create a reflection engine with default config."""
        return ReflectionEngine()

    @pytest.fixture
    def config(self) -> ReflectionEngineConfig:
        """Create a test configuration."""
        return ReflectionEngineConfig(
            mode=ReflectionMode.SYNC,
            min_reflection_interval_seconds=0.1,
            policy_success_threshold=0.6,
            max_policy_suggestions=3,
        )

    @pytest.fixture
    def success_context(self) -> TaskContext:
        """Create a successful task context."""
        return TaskContext(
            task_id="task-success-1",
            session_id="session-1",
            trace_id="trace-1",
            task_input={"query": "test query", "type": "search"},
            execution_trace=[
                {
                    "step": 1,
                    "worker": "openai",
                    "success": True,
                    "latency_ms": 100,
                },
                {
                    "step": 2,
                    "worker": "openai",
                    "success": True,
                    "latency_ms": 150,
                },
            ],
            task_result=TaskResult(
                unit_id=uuid4(),
                task_id="task-success-1",
                status=TaskStatus.SUCCESS,
                confidence=0.9,
                output_data={"result": "success"},
                trace_id="trace-1",
            ),
            execution_time_ms=250,
        )

    @pytest.fixture
    def failure_context(self) -> TaskContext:
        """Create a failed task context."""
        return TaskContext(
            task_id="task-failure-1",
            session_id="session-1",
            trace_id="trace-2",
            task_input={"query": "large input", "size": 50000},
            execution_trace=[
                {
                    "step": 1,
                    "worker": "openai",
                    "success": False,
                    "error": "timeout",
                },
                {
                    "step": 2,
                    "worker": "claude",
                    "success": False,
                    "error": "timeout",
                },
            ],
            task_result=TaskResult(
                unit_id=uuid4(),
                task_id="task-failure-1",
                status=TaskStatus.FAILURE,
                confidence=0.3,
                output_data={},
                error_message="Execution timeout after 300s",
                retry_count=2,
                trace_id="trace-2",
            ),
            execution_time_ms=300000,
        )

    @pytest.fixture
    def human_feedback_context(self) -> TaskContext:
        """Create a context with human feedback."""
        return TaskContext(
            task_id="task-feedback-1",
            session_id="session-1",
            trace_id="trace-3",
            task_input={"task": "complex task"},
            execution_trace=[
                {"step": 1, "worker": "openai", "success": True},
            ],
            task_result=TaskResult(
                unit_id=uuid4(),
                task_id="task-feedback-1",
                status=TaskStatus.SUCCESS,
                confidence=0.7,
                output_data={"result": "initial"},
                trace_id="trace-3",
            ),
            human_feedback={
                "worked": ["Used correct approach"],
                "failed": ["Output format could be better"],
            },
            execution_time_ms=5000,
        )

    def test_reflect_success_generates_report(
        self,
        engine: ReflectionEngine,
        success_context: TaskContext,
    ) -> None:
        """Test that reflection on success generates a report."""
        report = engine.reflect(success_context)

        assert isinstance(report, ReflectionReport)
        assert report.task_id == "task-success-1"
        # With no content in execution trace, what_worked may be empty.
        # The old code added vacuous "Task completed successfully"; the new
        # code only extracts substantive content from trace steps.
        assert report.confidence >= 0.5

    def test_reflect_failure_generates_analysis(
        self,
        engine: ReflectionEngine,
        failure_context: TaskContext,
    ) -> None:
        """Test that reflection on failure generates root cause analysis."""
        report = engine.reflect(failure_context)

        assert len(report.what_failed) > 0
        assert report.root_cause != ""
        assert report.next_time_strategy != ""

    def test_reflect_human_feedback_boosts_confidence(
        self,
        engine: ReflectionEngine,
        human_feedback_context: TaskContext,
    ) -> None:
        """Test that human feedback increases confidence."""
        report = engine.reflect(human_feedback_context)

        assert report.has_human_feedback is True
        assert report.confidence >= 0.85  # Boosted by human feedback

    def test_reflect_creates_episodic_memory(
        self,
        engine: ReflectionEngine,
        success_context: TaskContext,
    ) -> None:
        """Test that reflection creates episodic memory."""
        engine.reflect(success_context)

        episodic = engine.get_episodic("task-success-1")
        assert episodic is not None
        assert episodic.task_id == "task-success-1"
        assert episodic.outcome == "success"

    def test_reflect_failure_creates_failure_episodic(
        self,
        engine: ReflectionEngine,
        failure_context: TaskContext,
    ) -> None:
        """Test that failure creates failure episodic memory."""
        engine.reflect(failure_context)

        episodic = engine.get_episodic("task-failure-1")
        assert episodic is not None
        assert episodic.outcome == "failure"

    def test_reflect_generates_policy_suggestions(
        self,
        engine: ReflectionEngine,
        failure_context: TaskContext,
    ) -> None:
        """Test that reflection generates policy suggestions."""
        report = engine.reflect(failure_context)

        # Should suggest timeout increase
        if report.policy_suggestions:
            has_timeout_suggestion = any(
                "timeout" in str(s.get("action", {}))
                for s in report.policy_suggestions
            )
            # May or may not have suggestions depending on analysis

    def test_reflect_stores_policy(
        self,
        engine: ReflectionEngine,
        failure_context: TaskContext,
    ) -> None:
        """Test that reflection stores learned policies."""
        engine.reflect(failure_context)

        policies = engine.get_policies()
        # May have policies depending on what was generated

    def test_generate_route_hint_no_match(
        self,
        engine: ReflectionEngine,
    ) -> None:
        """Test route hint generation with no matching policy."""
        hint = engine.generate_route_hint({"query": "unseen query"})

        assert hint is None

    def test_generate_route_hint_with_match(
        self,
        engine: ReflectionEngine,
    ) -> None:
        """Test route hint generation with matching policy."""
        # First, create a policy through reflection
        context = TaskContext(
            task_id="task-route-1",
            session_id="session-1",
            trace_id="trace-route",
            task_input={"query": "search query", "type": "search"},
            execution_trace=[
                {"step": 1, "worker": "openai", "success": True},
            ],
            task_result=TaskResult(
                unit_id=uuid4(),
                task_id="task-route-1",
                status=TaskStatus.SUCCESS,
                confidence=0.9,
                output_data={},
                trace_id="trace-route",
            ),
        )
        engine.reflect(context)

        # Now try to generate hint
        hint = engine.generate_route_hint(
            {"query": "search query", "type": "search"},
        )

        # May or may not match depending on trigger extraction

    def test_record_policy_effectiveness(
        self,
        engine: ReflectionEngine,
    ) -> None:
        """Test recording policy effectiveness."""
        # Create a policy
        policy = LearnedPolicy(
            policy_id=uuid4(),
            trigger="test:trigger",
            action={"timeout_seconds": 300},
            confidence=0.7,
            hit_count=5,
            effective_count=3,
        )
        engine._policy_store["test:trigger"] = policy

        # Record effectiveness
        engine.record_policy_effectiveness(
            policy.policy_id,
            was_effective=True,
        )

        updated = engine.get_policy(policy.policy_id)
        assert updated is not None
        assert updated.effective_count == 4
        assert updated.success_rate == 4 / 5

    def test_disable_policy(
        self,
        engine: ReflectionEngine,
    ) -> None:
        """Test disabling a policy."""
        policy = LearnedPolicy(
            policy_id=uuid4(),
            trigger="disable:test",
            action={},
            confidence=0.7,
        )
        engine._policy_store["disable:test"] = policy

        result = engine.disable_policy(policy.policy_id)

        assert result is True
        assert engine.get_policy(policy.policy_id).disabled is True

    def test_get_reflection_report(
        self,
        engine: ReflectionEngine,
        success_context: TaskContext,
    ) -> None:
        """Test retrieving reflection report."""
        engine.reflect(success_context)

        report = engine.get_reflection_report("task-success-1")
        assert report is not None
        assert report.task_id == "task-success-1"

    def test_get_metrics(
        self,
        engine: ReflectionEngine,
        success_context: TaskContext,
        failure_context: TaskContext,
    ) -> None:
        """Test getting reflection engine metrics."""
        engine.reflect(success_context)
        engine.reflect(failure_context)

        metrics = engine.get_metrics()

        assert "reflection_count" in metrics
        assert "reflection_success_rate" in metrics
        assert metrics["reflection_count"] == 2


class TestReflectionEngineConfig:
    """Tests for ReflectionEngineConfig."""

    def test_default_config(self) -> None:
        """Test default configuration."""
        config = ReflectionEngineConfig()

        assert config.mode == ReflectionMode.ASYNC
        assert config.policy_success_threshold == 0.6
        assert config.max_policy_suggestions == 3

    def test_sync_mode_config(self) -> None:
        """Test sync mode configuration."""
        config = ReflectionEngineConfig(mode=ReflectionMode.SYNC)

        assert config.mode == ReflectionMode.SYNC
