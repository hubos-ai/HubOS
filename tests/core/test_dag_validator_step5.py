#!/usr/bin/env python3
"""Tests for DAG validator - Parallel Core V1.5 Step 5."""

import pytest
from hubos.core.dag.models import (
    DagPlan,
    DagNode,
    DagEdge,
    Condition,
)
from hubos.core.dag.validator import DagValidator, ValidationResult


class TestDagValidator:
    """Test DAG validator."""

    def test_valid_linear_dag(self):
        """Test a simple linear DAG passes validation."""
        nodes = [
            DagNode(node_id="a", role="ceo", required=True),
            DagNode(node_id="b", role="dev", required=True),
            DagNode(node_id="c", role="review", required=True),
        ]
        edges = [
            DagEdge(from_node="a", to_node="b"),
            DagEdge(from_node="b", to_node="c"),
        ]
        plan = DagPlan(
            plan_id="linear",
            name="Linear DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["c"],
        )

        result = DagValidator.validate_plan(plan)
        assert result.valid, f"Valid DAG should pass: {result.errors}"

    def test_valid_parallel_dag(self):
        """Test a parallel DAG (one-to-many) passes validation."""
        nodes = [
            DagNode(node_id="ceo", role="ceo", required=True),
            DagNode(node_id="info", role="info", required=False),
            DagNode(node_id="dev", role="dev", required=True),
            DagNode(node_id="review", role="review", required=True),
        ]
        edges = [
            DagEdge(from_node="ceo", to_node="info"),
            DagEdge(from_node="ceo", to_node="dev"),
            DagEdge(from_node="ceo", to_node="review"),
        ]
        plan = DagPlan(
            plan_id="parallel",
            name="Parallel DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["ceo"],
            exit_nodes=["info", "dev", "review"],
        )

        result = DagValidator.validate_plan(plan)
        assert result.valid

    def test_detects_cycle(self):
        """Test that cycles are detected."""
        nodes = [
            DagNode(node_id="a", role="dev", required=True),
            DagNode(node_id="b", role="dev", required=True),
            DagNode(node_id="c", role="dev", required=True),
        ]
        edges = [
            DagEdge(from_node="a", to_node="b"),
            DagEdge(from_node="b", to_node="c"),
            DagEdge(from_node="c", to_node="a"),  # Cycle!
        ]
        plan = DagPlan(
            plan_id="cyclic",
            name="Cyclic DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["c"],
        )

        result = DagValidator.validate_plan(plan)
        assert not result.valid
        assert any(e.error_type == "cycle" for e in result.errors)

    def test_detects_self_loop(self):
        """Test that self-loops are detected as cycles."""
        nodes = [
            DagNode(node_id="a", role="dev", required=True),
        ]
        edges = [
            DagEdge(from_node="a", to_node="a"),  # Self-loop!
        ]
        plan = DagPlan(
            plan_id="self-loop",
            name="Self Loop DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["a"],
        )

        result = DagValidator.validate_plan(plan)
        assert not result.valid
        assert any(e.error_type == "cycle" for e in result.errors)

    def test_detects_orphan_node(self):
        """Test that orphan nodes are detected."""
        nodes = [
            DagNode(node_id="a", role="ceo", required=True),
            DagNode(node_id="b", role="dev", required=True),
            DagNode(node_id="orphan", role="info", required=False),  # Orphan!
        ]
        edges = [
            DagEdge(from_node="a", to_node="b"),
        ]
        plan = DagPlan(
            plan_id="orphan",
            name="Orphan DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["b", "orphan"],
        )

        result = DagValidator.validate_plan(plan)
        assert not result.valid
        assert any(e.error_type == "orphan" for e in result.errors)

    def test_detects_unreachable_required(self):
        """Test that unreachable required nodes are detected."""
        nodes = [
            DagNode(node_id="a", role="ceo", required=True),
            DagNode(node_id="b", role="dev", required=True),
            DagNode(node_id="unreachable", role="review", required=True),  # Unreachable!
        ]
        edges = [
            DagEdge(from_node="a", to_node="b"),
        ]
        plan = DagPlan(
            plan_id="unreachable",
            name="Unreachable DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["b", "unreachable"],
        )

        result = DagValidator.validate_plan(plan)
        assert not result.valid
        assert any(e.error_type == "unreachable_required" for e in result.errors)

    def test_valid_dag_with_merge(self):
        """Test a valid DAG with merge node."""
        nodes = [
            DagNode(node_id="ceo", role="ceo", required=True),
            DagNode(node_id="dev", role="dev", required=True),
            DagNode(node_id="merge", role="ceo", required=True),
        ]
        edges = [
            DagEdge(from_node="ceo", to_node="dev"),
            DagEdge(from_node="dev", to_node="merge"),
        ]
        plan = DagPlan(
            plan_id="with-merge",
            name="DAG with Merge",
            nodes=nodes,
            edges=edges,
            entry_nodes=["ceo"],
            exit_nodes=["merge"],
            merge_node_id="merge",
        )

        result = DagValidator.validate_plan(plan)
        assert result.valid, f"Valid DAG with merge should pass: {result.errors}"

    def test_duplicate_node_ids(self):
        """Test that duplicate node IDs are detected."""
        nodes = [
            DagNode(node_id="a", role="ceo", required=True),
            DagNode(node_id="a", role="dev", required=True),  # Duplicate!
        ]
        edges = []
        plan = DagPlan(
            plan_id="duplicate",
            name="Duplicate Nodes",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["a"],
        )

        result = DagValidator.validate_plan(plan)
        assert not result.valid
        assert any(e.error_type == "duplicate_node_id" for e in result.errors)

    def test_empty_dag(self):
        """Test empty DAG validation."""
        plan = DagPlan(
            plan_id="empty",
            name="Empty DAG",
            nodes=[],
            edges=[],
            entry_nodes=[],
            exit_nodes=[],
        )

        result = DagValidator.validate_plan(plan)
        # Empty DAG is technically valid (no structure to violate)
        # but generates a warning
        assert result.valid or len(result.warnings) > 0

    def test_conditional_edges(self):
        """Test DAG with conditional edges."""
        nodes = [
            DagNode(node_id="a", role="ceo", required=True),
            DagNode(node_id="b", role="dev", required=True),
            DagNode(node_id="c", role="review", required=False),
        ]
        edges = [
            DagEdge(from_node="a", to_node="b"),
            DagEdge(from_node="b", to_node="c", condition=Condition(type="success")),
        ]
        plan = DagPlan(
            plan_id="conditional",
            name="Conditional DAG",
            nodes=nodes,
            edges=edges,
            entry_nodes=["a"],
            exit_nodes=["b", "c"],
        )

        result = DagValidator.validate_plan(plan)
        assert result.valid
