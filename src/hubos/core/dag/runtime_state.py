"""DAG runtime state management for Parallel Core V1.5 Step 5.

Manages the runtime state of DAG executions, including node states,
merge state, and persistence for recovery.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .models import (
    DagPlan,
    DagNode,
    DagRunState,
    DagEdge,
    NodeRunState,
    NodeStatus,
    MergeState,
    RetryPolicy,
)


@dataclass
class DagRuntimeState:
    """Runtime state manager for a DAG execution.

    Coordinates node lifecycle, merge gate logic, and failure handling.
    This is the core调度器 state that gets persisted for recovery.
    """

    plan: DagPlan
    run_state: DagRunState
    max_parallelism: int = 10
    _ready_queue: list[str] = field(default_factory=list)  # node_ids ready to dispatch

    def __post_init__(self) -> None:
        self.run_state.set_plan_nodes(self.plan.nodes)
        self._initialize_nodes()

    def _initialize_nodes(self) -> None:
        """Initialize all node run states from the plan."""
        for node in self.plan.nodes:
            if node.node_id not in self.run_state.nodes:
                self.run_state.nodes[node.node_id] = NodeRunState(
                    node_id=node.node_id,
                    status=NodeStatus.PENDING,
                    attempt=0,
                    max_attempts=node.max_attempts,
                )

        # Set entry nodes to READY initially
        for entry_id in self.plan.entry_nodes:
            if entry_id in self.run_state.nodes:
                self.run_state.nodes[entry_id].status = NodeStatus.READY
                self._ready_queue.append(entry_id)

    def get_ready_nodes(self) -> list[str]:
        """Get list of node IDs that are ready to dispatch."""
        self._refresh_ready_queue()
        return list(self._ready_queue)

    def _refresh_ready_queue(self) -> None:
        """Refresh ready queue based on current state.

        A node is ready when:
        1. All its predecessors are DONE (for unconditional edges)
        2. It is not already dispatched/running/done/failed
        3. It has no pending retry
        """
        current_ready = set(self._ready_queue)

        for node in self.plan.nodes:
            node_id = node.node_id
            state = self.run_state.nodes.get(node_id)

            if state is None:
                continue

            # Skip if not in a dispatchable state
            if state.status not in (NodeStatus.PENDING, NodeStatus.READY):
                continue

            # Check if all predecessors are done
            predecessors = self.plan.get_predecessors(node_id)
            all_done = all(
                self.run_state.nodes.get(pred_id, NodeRunState(pred_id)).status == NodeStatus.DONE
                for pred_id in predecessors
            )

            if all_done and node_id not in current_ready:
                self._ready_queue.append(node_id)
                current_ready.add(node_id)

        # Remove nodes that are no longer ready
        self._ready_queue = [
            nid for nid in self._ready_queue
            if self.run_state.nodes[nid].status in (NodeStatus.PENDING, NodeStatus.READY)
        ]

    def dispatch_node(self, node_id: str, executor: str, dispatcher_id: str) -> bool:
        """Mark a node as dispatched.

        Returns True if successful, False if node cannot be dispatched.
        """
        state = self.run_state.nodes.get(node_id)
        if state is None:
            return False

        # Must be in READY state
        if state.status != NodeStatus.READY:
            return False

        # Check parallelism limit (count running nodes)
        running_count = sum(
            1 for s in self.run_state.nodes.values()
            if s.status in (NodeStatus.DISPATCHED, NodeStatus.RUNNING)
        )
        if running_count >= self.max_parallelism:
            return False

        state.status = NodeStatus.DISPATCHED
        state.executor = executor
        state.dispatcher_id = dispatcher_id
        state.dispatched_at = time.time()
        state.attempt += 1

        # Remove from ready queue
        if node_id in self._ready_queue:
            self._ready_queue.remove(node_id)

        return True

    def start_node(self, node_id: str) -> bool:
        """Mark a node as running."""
        state = self.run_state.nodes.get(node_id)
        if state is None:
            return False
        if state.status != NodeStatus.DISPATCHED:
            return False

        state.status = NodeStatus.RUNNING
        state.started_at = time.time()
        return True

    def complete_node(self, node_id: str, result: Any) -> bool:
        """Mark a node as completed successfully."""
        state = self.run_state.nodes.get(node_id)
        if state is None:
            return False
        if state.status != NodeStatus.RUNNING:
            return False

        state.status = NodeStatus.DONE
        state.completed_at = time.time()
        state.result = result

        # Add to merge inputs if merge exists
        if self.plan.merge_node_id:
            self.run_state.merge_state.inputs[node_id] = result

        # Refresh ready queue for dependents
        self._refresh_ready_queue()

        return True

    def fail_node(self, node_id: str, error: str) -> bool:
        """Handle node failure based on retry policy."""
        state = self.run_state.nodes.get(node_id)
        node = self.plan.get_node(node_id)
        if state is None or node is None:
            return False

        if state.status not in (NodeStatus.DISPATCHED, NodeStatus.RUNNING):
            return False

        state.error = error

        if node.retry_policy == RetryPolicy.NONE:
            state.status = NodeStatus.FAILED
            state.completed_at = time.time()
            return True

        if state.attempt >= state.max_attempts:
            # Exhausted retries - go to human gate or failed
            if node.retry_policy == RetryPolicy.HUMAN_GATE:
                state.status = NodeStatus.HUMAN_GATE
                state.human_gate_reason = f"Exhausted {state.max_attempts} attempts: {error}"
            else:
                state.status = NodeStatus.FAILED
                state.completed_at = time.time()
            return True

        # Schedule retry
        state.status = NodeStatus.RETRYING
        state.retry_count += 1
        delay_ms = node.retry_delay_ms * (2 ** (state.retry_count - 1))  # Exponential backoff
        state.next_retry_at = time.time() + (delay_ms / 1000)

        return True

    def retry_node(self, node_id: str) -> bool:
        """Retry a failed or retrying node."""
        state = self.run_state.nodes.get(node_id)
        node = self.plan.get_node(node_id)
        if state is None or node is None:
            return False

        if state.status == NodeStatus.RETRYING:
            # Check if retry delay has passed
            if state.next_retry_at and time.time() < state.next_retry_at:
                return False
            state.status = NodeStatus.READY
            self._ready_queue.append(node_id)
            return True

        if state.status in (NodeStatus.FAILED, NodeStatus.HUMAN_GATE):
            # Allow manual retry
            state.status = NodeStatus.READY
            state.attempt = 0
            state.error = None
            state.retry_count = 0
            self._ready_queue.append(node_id)
            return True

        return False

    def resolve_human_gate(self, node_id: str, approved: bool, result: Any = None) -> bool:
        """Resolve a human gate by approving or rejecting."""
        state = self.run_state.nodes.get(node_id)
        if state is None:
            return False

        if state.status != NodeStatus.HUMAN_GATE:
            return False

        if approved:
            state.status = NodeStatus.DONE
            state.completed_at = time.time()
            state.result = result if result is not None else {"approved": True, "node_id": node_id}
            if self.plan.merge_node_id:
                self.run_state.merge_state.inputs[node_id] = state.result
            self._refresh_ready_queue()
        else:
            state.status = NodeStatus.FAILED
            state.completed_at = time.time()
            state.error = "Rejected by human"

        return True

    def check_merge_ready(self) -> bool:
        """Check if merge can start (all required predecessors done)."""
        if not self.plan.merge_node_id:
            return False

        merge_id = self.plan.merge_node_id
        predecessors = self.plan.get_predecessors(merge_id)

        # Count required predecessors
        required_predecessors = [
            pred_id for pred_id in predecessors
            if self.plan.get_node(pred_id) and self.plan.get_node(pred_id).required  # type: ignore
        ]

        # Check all required are done
        all_required_done = all(
            self.run_state.nodes.get(pred_id, NodeRunState(pred_id)).status == NodeStatus.DONE
            for pred_id in required_predecessors
        )

        self.run_state.merge_state.total_required_nodes = len(required_predecessors)
        self.run_state.merge_state.required_nodes_complete = sum(
            1 for pred_id in required_predecessors
            if self.run_state.nodes.get(pred_id, NodeRunState(pred_id)).status == NodeStatus.DONE
        )

        if all_required_done:
            self.run_state.merge_state.status = "ready"
            return True

        return False

    def start_merge(self) -> bool:
        """Start the merge phase."""
        if not self.check_merge_ready():
            return False
        self.run_state.merge_state.status = "started"
        self.run_state.merge_state.started_at = time.time()
        return True

    def complete_merge(self, result: Any = None) -> bool:
        """Complete the merge phase."""
        self.run_state.merge_state.status = "completed"
        self.run_state.merge_state.completed_at = time.time()
        self.run_state.status = "completed"
        return True

    def is_complete(self) -> bool:
        """Check if the DAG execution is complete."""
        if self.run_state.status in ("completed", "failed", "cancelled"):
            return True

        # Check all non-optional paths are done
        for node in self.plan.nodes:
            if node.required:
                state = self.run_state.nodes.get(node.node_id)
                if state is None or state.status not in (NodeStatus.DONE, NodeStatus.FAILED):
                    return False

        # Merge must be complete if it exists
        if self.plan.merge_node_id:
            if self.run_state.merge_state.status not in ("completed", "failed", "timeout"):
                return False

        return True

    def get_executor_for_node(self, node_id: str, default_executor: str = "native") -> str:
        """Determine which executor should run a node."""
        node = self.plan.get_node(node_id)
        if node and node.executor_hint:
            return node.executor_hint
        return default_executor

    def get_running_count(self) -> int:
        """Count currently running nodes."""
        return sum(
            1 for s in self.run_state.nodes.values()
            if s.status in (NodeStatus.DISPATCHED, NodeStatus.RUNNING)
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize runtime state for persistence."""
        return {
            "run_id": self.run_state.run_id,
            "plan_id": self.run_state.plan_id,
            "task_id": self.run_state.task_id,
            "status": self.run_state.status,
            "nodes": {
                node_id: {
                    "status": state.status.value,
                    "attempt": state.attempt,
                    "executor": state.executor,
                    "error": state.error,
                    "result": state.result,
                    "retry_count": state.retry_count,
                    "human_gate_reason": state.human_gate_reason,
                }
                for node_id, state in self.run_state.nodes.items()
            },
            "merge_state": {
                "status": self.run_state.merge_state.status,
                "required_nodes_complete": self.run_state.merge_state.required_nodes_complete,
                "total_required_nodes": self.run_state.merge_state.total_required_nodes,
                "inputs": self.run_state.merge_state.inputs,
            },
            "created_at": self.run_state.created_at,
            "started_at": self.run_state.started_at,
            "completed_at": self.run_state.completed_at,
        }

    @classmethod
    def from_dict(cls, plan: DagPlan, data: dict[str, Any]) -> "DagRuntimeState":
        """Restore runtime state from persisted dict."""
        run_state = DagRunState(
            run_id=data["run_id"],
            plan_id=data["plan_id"],
            task_id=data["task_id"],
            status=data.get("status", "running"),
            created_at=data.get("created_at", 0.0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
        )

        for node_id, node_data in data.get("nodes", {}).items():
            run_state.nodes[node_id] = NodeRunState(
                node_id=node_id,
                status=NodeStatus(node_data["status"]),
                attempt=node_data.get("attempt", 0),
                result=node_data.get("result"),
                error=node_data.get("error"),
                executor=node_data.get("executor"),
                retry_count=node_data.get("retry_count", 0),
                human_gate_reason=node_data.get("human_gate_reason"),
            )

        merge_data = data.get("merge_state", {})
        run_state.merge_state = MergeState(
            status=merge_data.get("status", "waiting"),
            required_nodes_complete=merge_data.get("required_nodes_complete", 0),
            total_required_nodes=merge_data.get("total_required_nodes", 0),
            inputs=merge_data.get("inputs", {}),
        )

        state = cls(plan=plan, run_state=run_state)
        state._refresh_ready_queue()
        return state
