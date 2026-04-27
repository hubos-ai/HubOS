#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for DAG Executor Selector - Parallel Core V1.5 Step 6."""

import pytest
import time
from hubos.core.dag.executor_selector import (
    ExecutorSelector,
    ExecutorMetrics,
    SelectionResult,
)


class TestExecutorSelector:
    """Test smart executor selector."""

    def test_initialization(self):
        """Test selector initializes correctly."""
        selector = ExecutorSelector(
            default_executor="native",
            enable_auto_switch=True,
        )

        assert selector._default == "native"
        assert selector._enable_auto_switch is True
        assert len(selector._executor_metrics) == 0

    def test_set_node_hint(self):
        """Test setting node hints."""
        selector = ExecutorSelector()

        selector.set_node_hint("node-1", "camel")
        assert selector._node_hint["node-1"] == "camel"

    def test_set_policy_recommendation(self):
        """Test setting policy recommendations."""
        selector = ExecutorSelector()

        selector.set_policy_recommendation("dev", "native")
        assert selector._policy_recommendation["dev"] == "native"

    def test_record_execution_creates_metrics(self):
        """Test recording execution creates metrics."""
        selector = ExecutorSelector()

        selector.record_execution(
            executor="native",
            success=True,
            latency_ms=100.0,
        )

        assert "native" in selector._executor_metrics
        metrics = selector._executor_metrics["native"]
        assert metrics.total_runs == 1
        assert metrics.successful_runs == 1
        assert metrics.total_latency_ms == 100.0

    def test_record_execution_failure(self):
        """Test recording failed execution."""
        selector = ExecutorSelector()

        selector.record_execution(
            executor="native",
            success=False,
            latency_ms=50.0,
        )

        metrics = selector._executor_metrics["native"]
        assert metrics.failed_runs == 1
        assert metrics.successful_runs == 0

    def test_record_execution_timeout(self):
        """Test recording timeout."""
        selector = ExecutorSelector()

        selector.record_execution(
            executor="native",
            success=False,
            latency_ms=5000.0,
            timed_out=True,
        )

        metrics = selector._executor_metrics["native"]
        assert metrics.timed_out_runs == 1

    def test_select_uses_default_when_no_history(self):
        """Test select returns default when no history."""
        selector = ExecutorSelector(default_executor="native")

        result = selector.select("node-1", "dev")

        assert result.selected_executor == "native"
        assert "default" in result.selection_reason

    def test_select_prefers_node_hint(self):
        """Test node hint has highest priority."""
        selector = ExecutorSelector(default_executor="native")

        selector.set_node_hint("node-1", "camel")

        result = selector.select("node-1", "dev", executor_hint="native")

        # executor_hint should win over node_id hint
        assert result.selected_executor == "native"

    def test_select_prefers_node_id_hint_over_policy(self):
        """Test node_id hint wins over policy recommendation."""
        selector = ExecutorSelector(default_executor="native")

        selector.set_node_hint("node-1", "camel")
        selector.set_policy_recommendation("dev", "native")

        result = selector.select("node-1", "dev")

        assert result.selected_executor == "camel"

    def test_select_prefers_policy_over_history(self):
        """Test policy recommendation wins over historical best."""
        selector = ExecutorSelector(default_executor="native")

        # Record historical best for native
        for _ in range(10):
            selector.record_execution("native", True, 100.0)

        # Policy recommends camel
        selector.set_policy_recommendation("dev", "camel")

        result = selector.select("node-1", "dev")

        assert result.selected_executor == "camel"
        assert "policy_recommendation" in result.selection_reason

    def test_select_prefers_history_over_default(self):
        """Test historical best wins over default."""
        selector = ExecutorSelector(default_executor="native")

        # Record camel as historical best
        for _ in range(10):
            selector.record_execution("camel", True, 100.0)

        result = selector.select("node-1", "dev")

        assert result.selected_executor == "camel"

    def test_fallback_executor_when_auto_switch_enabled(self):
        """Test fallback is set when auto-switch is enabled and history exists."""
        selector = ExecutorSelector(
            default_executor="native",
            enable_auto_switch=True,
        )

        # Record some history so alternatives exist
        for _ in range(3):
            selector.record_execution("camel", True, 100.0)

        result = selector.select("node-1", "dev")

        # With history, should have fallback to camel (second choice)
        assert result.fallback_executor is not None

    def test_no_fallback_when_auto_switch_disabled(self):
        """Test no fallback when auto-switch is disabled."""
        selector = ExecutorSelector(
            default_executor="native",
            enable_auto_switch=False,
        )

        result = selector.select("node-1", "dev")

        assert result.fallback_executor is None

    def test_selection_history_recorded(self):
        """Test selection history is recorded."""
        selector = ExecutorSelector()

        selector.select("node-1", "dev")

        assert len(selector._selection_history) == 1
        assert selector._selection_history[0]["node_id"] == "node-1"

    def test_get_selection_history(self):
        """Test getting selection history."""
        selector = ExecutorSelector()

        for i in range(5):
            selector.select(f"node-{i}", "dev")

        history = selector.get_selection_history(limit=3)

        assert len(history) == 3

    def test_executor_metrics(self):
        """Test getting executor metrics."""
        selector = ExecutorSelector()

        for _ in range(5):
            selector.record_execution("native", True, 100.0)
        for _ in range(3):
            selector.record_execution("camel", False, 200.0)

        metrics = selector.get_executor_metrics()

        assert "native" in metrics
        assert metrics["native"]["total_runs"] == 5
        assert metrics["native"]["success_rate"] == 1.0

        assert "camel" in metrics
        assert metrics["camel"]["success_rate"] == 0.0

    def test_should_switch_low_success_rate(self):
        """Test should_switch returns True for low success rate."""
        selector = ExecutorSelector(enable_auto_switch=True)

        # Record low success rate
        for _ in range(5):
            selector.record_execution("native", True, 100.0)
        for _ in range(3):
            selector.record_execution("native", False, 100.0)  # 5/8 = 62.5%

        result = selector.should_switch("native")

        assert result is True

    def test_should_switch_not_enough_samples(self):
        """Test should_switch needs minimum samples."""
        selector = ExecutorSelector(enable_auto_switch=True)

        # Only 3 runs (below minimum of 5)
        for _ in range(3):
            selector.record_execution("native", False, 100.0)

        result = selector.should_switch("native")

        assert result is False

    def test_should_switch_high_success_rate(self):
        """Test should_switch returns False for high success rate."""
        selector = ExecutorSelector(enable_auto_switch=True)

        # High success rate (8/10 = 80%)
        for _ in range(8):
            selector.record_execution("native", True, 100.0)
        for _ in range(2):
            selector.record_execution("native", False, 100.0)

        result = selector.should_switch("native")

        assert result is False

    def test_get_alternative_executor(self):
        """Test getting alternative executor."""
        selector = ExecutorSelector()

        selector.record_execution("native", True, 100.0)
        selector.record_execution("camel", True, 100.0)

        alt = selector.get_alternative_executor("native")

        assert alt == "camel"

    def test_get_alternative_fallback(self):
        """Test alternative fallback when no history."""
        selector = ExecutorSelector()

        alt = selector.get_alternative_executor("native")

        # Should return camel as fallback
        assert alt == "camel"

    def test_confidence_scores(self):
        """Test confidence scores are assigned correctly."""
        selector = ExecutorSelector()

        # Node hint should have highest confidence
        selector.set_node_hint("node-1", "camel")
        result = selector.select("node-1", "dev", executor_hint="native")
        assert result.confidence == 0.9  # executor_hint confidence

        # Reset and test node_id hint
        selector2 = ExecutorSelector()
        selector2.set_node_hint("node-1", "camel")
        result2 = selector2.select("node-1", "dev")
        assert result2.confidence == 0.85  # node_id_hint confidence

    def test_success_rate_property(self):
        """Test ExecutorMetrics success_rate property."""
        metrics = ExecutorMetrics(
            executor="test",
            total_runs=10,
            successful_runs=7,
        )

        assert metrics.success_rate == 0.7

    def test_success_rate_zero_runs(self):
        """Test success_rate with no runs."""
        metrics = ExecutorMetrics(executor="test")

        assert metrics.success_rate == 0.0

    def test_avg_latency_ms_property(self):
        """Test ExecutorMetrics avg_latency_ms property."""
        metrics = ExecutorMetrics(
            executor="test",
            total_runs=5,
            total_latency_ms=500.0,
        )

        assert metrics.avg_latency_ms == 100.0

    def test_avg_latency_ms_zero_runs(self):
        """Test avg_latency_ms with no runs."""
        metrics = ExecutorMetrics(executor="test")

        assert metrics.avg_latency_ms == 0.0

    def test_selection_result_alternatives(self):
        """Test SelectionResult alternatives includes fallback and default."""
        selector = ExecutorSelector(default_executor="native")

        # Record some history to populate alternatives
        for _ in range(3):
            selector.record_execution("camel", True, 100.0)

        result = selector.select("node-1", "dev")

        # With history, alternatives should be populated
        assert len(result.alternatives) >= 1
