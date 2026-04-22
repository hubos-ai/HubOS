"""DAG core data models for Parallel Core V1.5 Step 5.

Defines the fundamental DAG structures: DagPlan, DagNode, DagEdge, and DagRunState.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class NodeStatus(str, Enum):
    """Lifecycle status of a DAG node."""
    PENDING = "pending"          # Not yet schedulable
    READY = "ready"             # Dependencies satisfied, waiting to dispatch
    DISPATCHED = "dispatched"    # Sent to executor
    RUNNING = "running"          # Actively executing
    DONE = "done"               # Completed successfully
    FAILED = "failed"           # Completed with failure
    RETRYING = "retrying"       # Scheduled for retry
    HUMAN_GATE = "human_gate"    # Waiting for human intervention


class RetryPolicy(str, Enum):
    """Node retry policy on failure."""
    NONE = "none"                # No retry
    LIMITED = "limited"          # Retry up to max_attempts
    EXPONENTIAL = "exponential"  # Exponential backoff
    HUMAN_GATE = "human_gate"    # Go to human gate on failure


@dataclass
class Condition:
    """Edge condition for conditional DAG routing."""
    type: str = "always"         # "always", "success", "failure"
    expression: Optional[str] = None  # For future expression-based conditions


@dataclass
class DagNode:
    """A node in the DAG representing a unit of work."""
    node_id: str
    role: str                    # e.g., "ceo", "info", "dev", "review"
    required: bool = True        # Whether this node is required for merge
    timeout_ms: int = 300000     # 5 minutes default
    retry_policy: RetryPolicy = RetryPolicy.LIMITED
    max_attempts: int = 3
    executor_hint: Optional[str] = None  # "camel", "native", or None for default
    retry_delay_ms: int = 1000    # Initial retry delay
    input_template: str = "{input}"  # How to construct node input
    metadata: dict[str, Any] = field(default_factory=dict)

    def __hash__(self) -> int:
        return hash(self.node_id)


@dataclass
class DagEdge:
    """A directed edge between nodes in the DAG."""
    from_node: str
    to_node: str
    condition: Condition = field(default_factory=Condition)

    def is_unconditional(self) -> bool:
        return self.condition.type == "always"

    def is_success_path(self) -> bool:
        return self.condition.type == "success"

    def is_failure_path(self) -> bool:
        return self.condition.type == "failure"


@dataclass
class DagPlan:
    """A complete DAG execution plan.

    A DAG plan consists of nodes (vertices) and edges (directed connections).
    The plan has entry nodes (no incoming edges) and exit nodes (no outgoing edges).
    The merge_node_id is a special node that aggregates results from required paths.
    """
    plan_id: str
    name: str
    nodes: list[DagNode] = field(default_factory=list)
    edges: list[DagEdge] = field(default_factory=list)
    entry_nodes: list[str] = field(default_factory=list)   # Nodes with no incoming edges
    exit_nodes: list[str] = field(default_factory=list)    # Nodes with no outgoing edges
    merge_node_id: Optional[str] = None                    # Special merge aggregation node

    # DAG metadata
    description: str = ""
    version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_node(self, node_id: str) -> Optional[DagNode]:
        """Get a node by ID."""
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        return None

    def get_incoming_edges(self, node_id: str) -> list[DagEdge]:
        """Get all edges leading into a node."""
        return [e for e in self.edges if e.to_node == node_id]

    def get_outgoing_edges(self, node_id: str) -> list[DagEdge]:
        """Get all edges leading out from a node."""
        return [e for e in self.edges if e.from_node == node_id]

    def get_predecessors(self, node_id: str) -> list[str]:
        """Get node IDs that are direct predecessors."""
        return [e.from_node for e in self.edges if e.to_node == node_id]

    def get_successors(self, node_id: str) -> list[str]:
        """Get node IDs that are direct successors."""
        return [e.to_node for e in self.edges if e.from_node == node_id]

    def is_entry_node(self, node_id: str) -> bool:
        """Check if node is an entry node (no incoming edges)."""
        return node_id in self.entry_nodes

    def is_exit_node(self, node_id: str) -> bool:
        """Check if node is an exit node (no outgoing edges)."""
        return node_id in self.exit_nodes


@dataclass
class MergeState:
    """State of the merge node."""
    status: str = "waiting"      # waiting, ready, started, completed, failed, timeout
    required_nodes_complete: int = 0
    total_required_nodes: int = 0
    inputs: dict[str, Any] = field(default_factory=dict)  # node_id -> result
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None


@dataclass
class NodeRunState:
    """Runtime state for a single node execution."""
    node_id: str
    status: NodeStatus = NodeStatus.PENDING
    attempt: int = 0
    max_attempts: int = 3
    dispatcher_id: Optional[str] = None  # Which instance dispatched
    dispatched_at: Optional[float] = None
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Any] = None
    error: Optional[str] = None
    executor: Optional[str] = None  # Which executor ran this node
    retry_count: int = 0
    next_retry_at: Optional[float] = None
    human_gate_reason: Optional[str] = None


@dataclass
class DagRunState:
    """Runtime state for a complete DAG execution.

    Tracks the status of all nodes and the overall DAG execution.
    This state is persisted to enable recovery after restart.
    """
    run_id: str
    plan_id: str
    task_id: str                 # External task reference
    status: str = "initialized"  # initialized, running, completed, failed, cancelled
    nodes: dict[str, NodeRunState] = field(default_factory=dict)
    merge_state: MergeState = field(default_factory=MergeState)
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    error: Optional[str] = None
    fallback_executor: Optional[str] = None  # If we fell back to native
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_node_state(self, node_id: str) -> Optional[NodeRunState]:
        """Get runtime state for a node."""
        return self.nodes.get(node_id)

    def get_status(self, node_id: str) -> NodeStatus:
        """Get status of a node, defaulting to PENDING."""
        node_state = self.nodes.get(node_id)
        if node_state is None:
            return NodeStatus.PENDING
        return node_state.status

    def all_required_nodes_done(self) -> bool:
        """Check if all required nodes have completed successfully."""
        for node_id, node_state in self.nodes.items():
            node = self._plan_nodes.get(node_id)
            if node and node.required and node_state.status != NodeStatus.DONE:
                return False
        return True

    def set_plan_nodes(self, nodes: list[DagNode]) -> None:
        """Set plan nodes for required-node checking (call after load)."""
        self._plan_nodes = {n.node_id: n for n in nodes}

    # Use object.__setattr__ to handle _plan_nodes as a regular attribute
    def __post_init__(self) -> None:
        object.__setattr__(self, '_plan_nodes', {})
