# -*- coding: utf-8 -*-
"""DAG validator for Parallel Core V1.5 Step 5.

Validates DAG plans for structural correctness before execution.
"""

from dataclasses import dataclass, field
from typing import Any, Optional
from collections import deque

from .models import DagPlan, DagNode, DagEdge


@dataclass
class ValidationError:
    """A single validation error."""

    error_type: str  # e.g., "cycle", "orphan", "unreachable"
    node_id: Optional[str]
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class ValidationResult:
    """Result of DAG validation."""

    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(
        self,
        error_type: str,
        node_id: Optional[str],
        message: str,
        **kwargs: Any,
    ) -> None:
        self.errors.append(
            ValidationError(error_type, node_id, message, kwargs),
        )
        self.valid = False

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


class DagValidator:
    """Validates DAG plans for structural correctness."""

    def __init__(self, plan: DagPlan) -> None:
        self.plan = plan
        self._node_ids: set[str] = {n.node_id for n in plan.nodes}
        self._result: Optional[ValidationResult] = None

    def validate(self) -> ValidationResult:
        """Run all validations and return result."""
        self._result = ValidationResult(valid=True, errors=[], warnings=[])

        self._check_cycle_detection()
        self._check_no_orphan_nodes()
        self._check_required_node_reachability()
        self._check_merge_node_integrity()
        self._check_entry_exit_consistency()
        self._check_node_id_uniqueness()

        return self._result

    def _check_cycle_detection(self) -> None:
        """Detect cycles using DFS with three-color marking."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {n: WHITE for n in self._node_ids}
        parent: dict[str, Optional[str]] = {n: None for n in self._node_ids}

        def dfs(node_id: str) -> bool:
            color[node_id] = GRAY
            for edge in self.plan.get_outgoing_edges(node_id):
                neighbor = edge.to_node
                if neighbor not in self._node_ids:
                    continue
                if color[neighbor] == GRAY:
                    # Back edge found - cycle detected
                    cycle_nodes = self._reconstruct_cycle(
                        neighbor,
                        node_id,
                        parent,
                    )
                    self._result.add_error(
                        "cycle",
                        node_id,
                        f"Cycle detected: {' -> '.join(cycle_nodes)}",
                        cycle=cycle_nodes,
                    )
                    return True
                if color[neighbor] == WHITE:
                    parent[neighbor] = node_id
                    if dfs(neighbor):
                        return True
            color[node_id] = BLACK
            return False

        for node_id in self._node_ids:
            if color[node_id] == WHITE:
                if dfs(node_id):
                    break

    def _reconstruct_cycle(
        self,
        start: str,
        end: str,
        parent: dict[str, Optional[str]],
    ) -> list[str]:
        """Reconstruct the cycle path from end back to start."""
        path = [end]
        current = end
        while current != start:
            current = parent[current]  # type: ignore
            if current is None:
                break
            path.append(current)
        path.append(start)
        path.reverse()
        return path

    def _check_no_orphan_nodes(self) -> None:
        """Detect orphan nodes (not reachable from any entry node)."""
        if not self.plan.entry_nodes:
            for node in self.plan.nodes:
                self._result.add_error(
                    "orphan",
                    node.node_id,
                    f"Node '{node.node_id}' has no incoming edges and no entry_nodes defined",
                )
            return

        reachable = self._compute_reachable_from_entries()
        unreachable = self._node_ids - reachable
        for node_id in unreachable:
            self._result.add_error(
                "orphan",
                node_id,
                f"Node '{node_id}' is not reachable from any entry node",
            )

    def _compute_reachable_from_entries(self) -> set[str]:
        """Compute all nodes reachable from entry nodes via BFS."""
        reachable: set[str] = set()
        queue = deque(self.plan.entry_nodes)

        while queue:
            node_id = queue.popleft()
            if node_id in reachable:
                continue
            reachable.add(node_id)
            for edge in self.plan.get_outgoing_edges(node_id):
                if edge.to_node not in reachable:
                    queue.append(edge.to_node)

        return reachable

    def _check_required_node_reachability(self) -> None:
        """Ensure all required nodes are reachable from entry nodes."""
        reachable = self._compute_reachable_from_entries()
        for node in self.plan.nodes:
            if node.required and node.node_id not in reachable:
                self._result.add_error(
                    "unreachable_required",
                    node.node_id,
                    f"Required node '{node.node_id}' is not reachable from entry nodes",
                )

    def _check_merge_node_integrity(self) -> None:
        """Validate merge node prerequisites."""
        if not self.plan.merge_node_id:
            # No merge node - check if exit_nodes have required paths
            return

        merge_id = self.plan.merge_node_id
        if merge_id not in self._node_ids:
            self._result.add_error(
                "missing_merge_node",
                merge_id,
                f"merge_node_id '{merge_id}' references a non-existent node",
            )
            return

        # Check that merge node has incoming edges from required nodes
        incoming = self.plan.get_incoming_edges(merge_id)
        required_predecessors = [
            e.from_node
            for e in incoming
            if self.plan.get_node(e.from_node) and self.plan.get_node(e.from_node).required  # type: ignore
        ]

        # A merge should have at least one required predecessor
        if not required_predecessors:
            self._result.add_warning(
                f"Merge node '{merge_id}' has no required predecessor nodes",
            )

    def _check_entry_exit_consistency(self) -> None:
        """Validate entry_nodes and exit_nodes match actual graph structure."""
        # Entry nodes should have no incoming edges
        for entry_id in self.plan.entry_nodes:
            if entry_id not in self._node_ids:
                self._result.add_error(
                    "invalid_entry",
                    entry_id,
                    f"entry_nodes contains unknown node '{entry_id}'",
                )
                continue
            incoming = self.plan.get_incoming_edges(entry_id)
            if incoming:
                self._result.add_warning(
                    f"Entry node '{entry_id}' has {len(incoming)} incoming edges",
                )

        # Exit nodes should have no outgoing edges (unless they lead to merge)
        for exit_id in self.plan.exit_nodes:
            if exit_id not in self._node_ids:
                self._result.add_error(
                    "invalid_exit",
                    exit_id,
                    f"exit_nodes contains unknown node '{exit_id}'",
                )
                continue
            outgoing = self.plan.get_outgoing_edges(exit_id)
            # Filter out edges that go to merge node
            non_merge_outgoing = [
                e for e in outgoing if e.to_node != self.plan.merge_node_id
            ]
            if non_merge_outgoing:
                self._result.add_warning(
                    f"Exit node '{exit_id}' has {len(non_merge_outgoing)} outgoing edges (non-merge)",
                )

    def _check_node_id_uniqueness(self) -> None:
        """Ensure all node IDs are unique."""
        seen: set[str] = set()
        for node in self.plan.nodes:
            if node.node_id in seen:
                self._result.add_error(
                    "duplicate_node_id",
                    node.node_id,
                    f"Duplicate node ID '{node.node_id}'",
                )
            seen.add(node.node_id)

    @staticmethod
    def validate_plan(plan: DagPlan) -> ValidationResult:
        """Static method to validate a plan."""
        validator = DagValidator(plan)
        return validator.validate()
