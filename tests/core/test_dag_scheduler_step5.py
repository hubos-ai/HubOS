#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for DAG scheduler - Parallel Core V1.5 Step 5."""

import pytest
import time
from hubos.core.dag.models import (
    DagPlan,
    DagNode,
    DagEdge,
    NodeStatus,
    RetryPolicy,
)
from hubos.core.dag.scheduler import DagScheduler, SchedulerConfig
from hubos.core.dag.runtime_state import DagRuntimeState


class TestDagScheduler:
    """Test DAG scheduler."""

    def test_scheduler_initialization(self):
        """Test scheduler initializes correctly."""
        nodes = [
            DagNode(node_id="ceo", role="ceo", required=True),
            DagNode(node_id="dev", role="dev", required=True),
        ]
        edges = [
            DagEdge(from_node="ceo", to_node="dev"),
        ]
        plan = DagPlan(
            plan_id="test",
            name="Test DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["ceo"],
            exit_nodes=["dev"],
        )

        config = SchedulerConfig(max_parallelism=10)
        scheduler = DagScheduler(plan, "test-task", config)

        status = scheduler.get_status_summary()
        assert status["status"] == "initialized"
        assert "ceo" in scheduler._runtime.get_ready_nodes()

    def test_ready_queue_after_first_dispatch(self):
        """Test ready queue updates after first node dispatch."""
        nodes = [
            DagNode(node_id="ceo", role="ceo", required=True),
            DagNode(node_id="dev", role="dev", required=True),
        ]
        edges = [
            DagEdge(from_node="ceo", to_node="dev"),
        ]
        plan = DagPlan(
            plan_id="linear",
            name="Linear DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["ceo"],
            exit_nodes=["dev"],
        )

        config = SchedulerConfig(max_parallelism=10)
        scheduler = DagScheduler(plan, "test-task", config)
        scheduler.start()

        # ceo should be ready initially
        ready = scheduler._runtime.get_ready_nodes()
        assert "ceo" in ready

        # Dispatch ceo
        success = scheduler._runtime.dispatch_node(
            "ceo",
            "native",
            "test-dispatcher",
        )
        assert success

        # ceo should no longer be ready (dispatched)
        ready = scheduler._runtime.get_ready_nodes()
        assert "ceo" not in ready

    def test_max_parallelism_enforced(self):
        """Test max parallelism is enforced."""
        nodes = [
            DagNode(node_id="a", role="dev", required=True),
            DagNode(node_id="b", role="dev", required=True),
            DagNode(node_id="c", role="dev", required=True),
            DagNode(node_id="d", role="dev", required=True),
        ]
        edges = [
            DagEdge(from_node="a", to_node="b"),
            DagEdge(from_node="a", to_node="c"),
            DagEdge(from_node="a", to_node="d"),
        ]
        plan = DagPlan(
            plan_id="parallel",
            name="Parallel DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["b", "c", "d"],
        )

        config = SchedulerConfig(max_parallelism=2)
        scheduler = DagScheduler(plan, "test-task", config)
        scheduler.start()

        # Dispatch a first
        scheduler._runtime.dispatch_node("a", "native", "test")

        # Dispatch b and c (max parallelism = 2)
        scheduler._runtime.dispatch_node("b", "native", "test")
        scheduler._runtime.dispatch_node("c", "native", "test")

        # d should NOT be dispatched (would exceed max)
        assert scheduler._runtime.get_running_count() <= 2

    def test_node_failure_and_retry(self):
        """Test node failure triggers retry logic."""
        nodes = [
            DagNode(
                node_id="fail-node",
                role="dev",
                required=True,
                retry_policy=RetryPolicy.LIMITED,
                max_attempts=3,
            ),
        ]
        edges = []
        plan = DagPlan(
            plan_id="retry-test",
            name="Retry Test DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["fail-node"],
            exit_nodes=["fail-node"],
        )

        config = SchedulerConfig()
        scheduler = DagScheduler(plan, "test-task", config)
        scheduler.start()

        # Dispatch node
        scheduler._runtime.dispatch_node("fail-node", "native", "test")

        # Fail the node
        success = scheduler._runtime.fail_node("fail-node", "Test error")
        assert success

        # Should be in retrying state
        state = scheduler._runtime.run_state.get_node_state("fail-node")
        assert state.status == NodeStatus.RETRYING
        assert state.retry_count == 1

    def test_human_gate_on_retry_exhaustion(self):
        """Test node goes to human gate when retries exhausted."""
        nodes = [
            DagNode(
                node_id="hg-node",
                role="dev",
                required=True,
                retry_policy=RetryPolicy.HUMAN_GATE,
                max_attempts=2,
                retry_delay_ms=10,  # Short delay for testing
            ),
        ]
        edges = []
        plan = DagPlan(
            plan_id="human-gate-test",
            name="Human Gate Test",
            nodes=nodes,
            edges=edges,
            entry_nodes=["hg-node"],
            exit_nodes=["hg-node"],
        )

        config = SchedulerConfig()
        scheduler = DagScheduler(plan, "test-task", config)
        scheduler.start()

        # Dispatch and fail first time
        scheduler._runtime.dispatch_node("hg-node", "native", "test")
        result1 = scheduler._runtime.fail_node("hg-node", "Error 1")
        assert result1
        assert (
            scheduler._runtime.run_state.nodes["hg-node"].status
            == NodeStatus.RETRYING
        )

        # Wait for retry delay to pass
        import time

        time.sleep(0.1)

        # Retry should be available after retry_node call
        success = scheduler.retry_node("hg-node")
        assert success
        assert (
            scheduler._runtime.run_state.nodes["hg-node"].status
            == NodeStatus.READY
        )

        # Dispatch second time
        scheduler._runtime.dispatch_node("hg-node", "native", "test")
        result2 = scheduler._runtime.fail_node("hg-node", "Error 2")

        # Should be in human gate after exhausting retries
        state = scheduler._runtime.run_state.get_node_state("hg-node")
        assert state.status == NodeStatus.HUMAN_GATE

    def test_human_gate_resolution_approve(self):
        """Test human gate resolution by approval."""
        nodes = [
            DagNode(node_id="hg", role="dev", required=True),
        ]
        edges = []
        plan = DagPlan(
            plan_id="hg-resolve",
            name="Human Gate Resolve",
            nodes=nodes,
            edges=edges,
            entry_nodes=["hg"],
            exit_nodes=["hg"],
        )

        config = SchedulerConfig()
        scheduler = DagScheduler(plan, "test-task", config)

        # Set to human gate
        scheduler._runtime.run_state.nodes["hg"].status = NodeStatus.HUMAN_GATE

        # Resolve with approval
        success = scheduler.resolve_human_gate(
            "hg",
            approved=True,
            result={"approved": True},
        )
        assert success

        state = scheduler._runtime.run_state.get_node_state("hg")
        assert state.status == NodeStatus.DONE

    def test_human_gate_resolution_reject(self):
        """Test human gate resolution by rejection."""
        nodes = [
            DagNode(node_id="hg", role="dev", required=True),
        ]
        edges = []
        plan = DagPlan(
            plan_id="hg-reject",
            name="Human Gate Reject",
            nodes=nodes,
            edges=edges,
            entry_nodes=["hg"],
            exit_nodes=["hg"],
        )

        config = SchedulerConfig()
        scheduler = DagScheduler(plan, "test-task", config)

        scheduler._runtime.run_state.nodes["hg"].status = NodeStatus.HUMAN_GATE

        success = scheduler.resolve_human_gate("hg", approved=False)
        assert success

        state = scheduler._runtime.run_state.get_node_state("hg")
        assert state.status == NodeStatus.FAILED

    def test_executor_selection_by_hint(self):
        """Test executor is selected by node hint."""
        nodes = [
            DagNode(
                node_id="camel-node",
                role="dev",
                executor_hint="camel",
                required=True,
            ),
            DagNode(
                node_id="native-node",
                role="dev",
                executor_hint="native",
                required=True,
            ),
            DagNode(node_id="default-node", role="dev", required=True),
        ]
        edges = []
        plan = DagPlan(
            plan_id="executor-test",
            name="Executor Selection",
            nodes=nodes,
            edges=edges,
            entry_nodes=["camel-node", "native-node", "default-node"],
            exit_nodes=["camel-node", "native-node", "default-node"],
        )

        config = SchedulerConfig(default_executor="native")
        scheduler = DagScheduler(plan, "test-task", config)

        assert (
            scheduler._runtime.get_executor_for_node("camel-node") == "camel"
        )
        assert (
            scheduler._runtime.get_executor_for_node("native-node") == "native"
        )
        assert (
            scheduler._runtime.get_executor_for_node("default-node")
            == "native"
        )

    def test_runtime_state_serialization(self):
        """Test runtime state can be serialized and restored."""
        nodes = [
            DagNode(node_id="a", role="ceo", required=True),
            DagNode(node_id="b", role="dev", required=True),
        ]
        edges = [DagEdge(from_node="a", to_node="b")]
        plan = DagPlan(
            plan_id="serialize-test",
            name="Serialization Test",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["b"],
        )

        config = SchedulerConfig()
        scheduler = DagScheduler(plan, "test-task", config)
        scheduler.start()

        # Get serialized state
        state_dict = scheduler._runtime.to_dict()

        # Restore
        restored = DagRuntimeState.from_dict(plan, state_dict)

        assert restored.run_state.run_id == scheduler._runtime.run_state.run_id
        assert restored.run_state.status == scheduler._runtime.run_state.status

    def test_get_status_summary(self):
        """Test status summary contains expected fields."""
        nodes = [
            DagNode(node_id="a", role="ceo", required=True),
        ]
        edges = []
        plan = DagPlan(
            plan_id="status-test",
            name="Status Summary Test",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["a"],
        )

        config = SchedulerConfig()
        scheduler = DagScheduler(plan, "test-task", config)

        summary = scheduler.get_status_summary()

        assert "run_id" in summary
        assert "task_id" in summary
        assert "status" in summary
        assert "nodes" in summary
        assert "merge" in summary
        assert "running_count" in summary
        assert "ready_count" in summary
