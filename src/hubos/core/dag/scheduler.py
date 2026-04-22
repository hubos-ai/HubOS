"""DAG scheduler for Parallel Core V1.5 Step 5.

The core scheduling engine that manages DAG execution, node lifecycle,
and coordinates with pluggable executors.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .models import (
    DagPlan,
    DagRunState,
    NodeStatus,
    RetryPolicy,
    MergeState,
)
from .runtime_state import DagRuntimeState
from .validator import DagValidator
from hubos.core.execution.executors.base import BaseExecutor, ExecutionResult
from hubos.core.execution.executors.native_executor import NativeExecutor

# Optional executor: only registered if its module is available.
# Kept out of the default install footprint; can be enabled in a later stage
# by providing the corresponding executor module.
try:
    from hubos.core.execution.executors.camel_executor import CAMELExecutor  # type: ignore
    _OPTIONAL_PARALLEL_EXECUTOR_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    CAMELExecutor = None  # type: ignore[assignment,misc]
    _OPTIONAL_PARALLEL_EXECUTOR_AVAILABLE = False


logger = logging.getLogger(__name__)


# Type for event callbacks
DagEventCallback = Callable[["DagScheduler", str, dict[str, Any]], None]


@dataclass
class SchedulerConfig:
    """Configuration for DAG scheduler."""
    max_parallelism: int = 10
    default_executor: str = "native"  # "camel" or "native"
    enable_fallback: bool = True       # Fall back to native if camel fails
    merge_timeout_ms: int = 300000     # 5 minutes
    default_node_timeout_ms: int = 300000
    tick_interval_ms: int = 100       # Scheduler tick interval


class DagScheduler:
    """DAG-native scheduling engine.

    This is the core scheduler that:
    1. Maintains ready queue based on dependency satisfaction
    2. Dispatches nodes to appropriate executors
    3. Handles node lifecycle (pending -> running -> done/failed)
    4. Manages merge gate logic
    5. Persists state for recovery
    """

    def __init__(
        self,
        plan: DagPlan,
        task_id: str,
        config: Optional[SchedulerConfig] = None,
        state_persister: Optional[Callable[[DagRuntimeState], None]] = None,
    ) -> None:
        self.plan = plan
        self.task_id = task_id
        self.config = config or SchedulerConfig()
        self._persister = state_persister

        # Create run state
        self._run_state = DagRunState(
            run_id=str(uuid.uuid4()),
            plan_id=plan.name,
            task_id=task_id,
            status="initialized",
            created_at=time.time(),
        )

        # Create runtime state
        self._runtime = DagRuntimeState(
            plan=plan,
            run_state=self._run_state,
            max_parallelism=self.config.max_parallelism,
        )

        # Executors
        self._executors: dict[str, BaseExecutor] = {
            "native": NativeExecutor(),
        }
        if _OPTIONAL_PARALLEL_EXECUTOR_AVAILABLE:
            self._executors["camel"] = CAMELExecutor()  # type: ignore[misc]

        # Event callbacks
        self._event_callbacks: list[DagEventCallback] = []

        # Dispatcher ID (instance-unique)
        self._dispatcher_id = str(uuid.uuid4())[:8]

        # Validate plan
        validator = DagValidator(plan)
        result = validator.validate()
        if not result.valid:
            logger.error(f"DAG plan validation failed: {result.errors}")
            raise ValueError(f"Invalid DAG plan: {[e.message for e in result.errors]}")

        # Start merge state
        self._run_state.merge_state = MergeState(
            status="waiting",
            total_required_nodes=len([n for n in plan.nodes if n.required]),
        )

    def start(self) -> None:
        """Start the DAG execution."""
        if self._run_state.status in ("running", "completed", "failed"):
            return

        self._run_state.status = "running"
        self._run_state.started_at = time.time()
        self._emit_event("dag_ready", {"plan_id": self.plan.name, "task_id": self.task_id})
        self._persist_state()

    def tick(self) -> bool:
        """Single scheduler tick. Returns True if work was done.

        Call this repeatedly (e.g., in a loop) to drive execution.
        """
        if self._run_state.status not in ("running",):
            return False

        work_done = False

        # Handle retrying nodes
        work_done |= self._process_retries()

        # Dispatch ready nodes
        work_done |= self._dispatch_ready_nodes()

        # Check for merge readiness
        work_done |= self._check_and_start_merge()

        # Check for DAG completion
        if self._runtime.is_complete():
            self._complete_dag()

        # Persist state periodically
        self._persist_state()

        return work_done

    def _process_retries(self) -> bool:
        """Process nodes that are in retry state."""
        work_done = False
        for node_id, state in self._run_state.nodes.items():
            if state.status == NodeStatus.RETRYING:
                if state.next_retry_at and time.time() >= state.next_retry_at:
                    state.status = NodeStatus.READY
                    self._runtime._ready_queue.append(node_id)
                    self._emit_event("node_retry_scheduled", {
                        "node_id": node_id,
                        "retry_count": state.retry_count,
                    })
                    work_done = True
        return work_done

    def _dispatch_ready_nodes(self) -> bool:
        """Dispatch nodes that are ready to run."""
        work_done = False
        ready_nodes = self._runtime.get_ready_nodes()

        # Respect max parallelism
        running = self._runtime.get_running_count()
        available_slots = self.config.max_parallelism - running

        for node_id in ready_nodes[:available_slots]:
            node = self.plan.get_node(node_id)
            if node is None:
                continue

            # Determine executor
            executor_name = self._runtime.get_executor_for_node(
                node_id,
                self.config.default_executor,
            )
            executor = self._executors.get(executor_name, self._executors["native"])

            # Try dispatch
            if self._runtime.dispatch_node(node_id, executor_name, self._dispatcher_id):
                self._emit_event("node_dispatch", {
                    "node_id": node_id,
                    "role": node.role,
                    "executor": executor_name,
                    "attempt": self._run_state.nodes[node_id].attempt,
                })

                # Execute asynchronously in background (simulated for now)
                self._execute_node_async(node_id, node, executor)
                work_done = True

        return work_done

    def _execute_node_async(self, node_id: str, node, executor: BaseExecutor) -> None:
        """Execute a node asynchronously."""
        def _run():
            try:
                # Mark as running
                self._runtime.start_node(node_id)

                self._emit_event("node_running", {
                    "node_id": node_id,
                    "role": node.role,
                    "executor": executor.executor_name,
                })

                # Execute
                result = executor.execute(
                    node_id=node_id,
                    role=node.role,
                    input_text=self._get_node_input(node),
                    timeout_ms=node.timeout_ms,
                    attempt=self._run_state.nodes[node_id].attempt,
                    metadata={"input_template": node.input_template},
                )

                # Handle result
                if result.success:
                    self._runtime.complete_node(node_id, result.output)
                    self._emit_event("node_completed", {
                        "node_id": node_id,
                        "role": node.role,
                        "duration_ms": result.duration_ms,
                        "executor": result.executor,
                    })
                else:
                    self._runtime.fail_node(node_id, result.error or "Unknown error")
                    self._emit_event("node_failed", {
                        "node_id": node_id,
                        "role": node.role,
                        "error": result.error,
                        "attempt": self._run_state.nodes[node_id].attempt,
                        "executor": result.executor,
                    })

                    # Check if human gate
                    if self._run_state.nodes[node_id].status == NodeStatus.HUMAN_GATE:
                        self._emit_event("node_human_gate", {
                            "node_id": node_id,
                            "reason": self._run_state.nodes[node_id].human_gate_reason,
                        })

            except Exception as e:
                logger.exception(f"Node {node_id} execution error")
                self._runtime.fail_node(node_id, str(e))
                self._emit_event("node_failed", {
                    "node_id": node_id,
                    "error": str(e),
                })

            self._persist_state()

        # Run in thread
        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _get_node_input(self, node) -> str:
        """Get input text for a node from merge inputs or task input."""
        # For now, pass through task_id as input
        # In production, this would aggregate inputs from predecessors
        return self.task_id

    def _check_and_start_merge(self) -> bool:
        """Check if merge can start and start it."""
        if self.plan.merge_node_id is None:
            return False

        if self._run_state.merge_state.status != "waiting":
            return False

        if self._runtime.check_merge_ready():
            self._emit_event("merge_ready", {
                "merge_node_id": self.plan.merge_node_id,
                "required_nodes_complete": self._run_state.merge_state.required_nodes_complete,
            })

            self._runtime.start_merge()
            self._emit_event("merge_started", {
                "merge_node_id": self.plan.merge_node_id,
            })

            # Execute merge (synchronous for now)
            self._execute_merge()
            return True

        return False

    def _execute_merge(self) -> None:
        """Execute the merge node."""
        merge_id = self.plan.merge_node_id
        if merge_id is None:
            return

        try:
            # Aggregate results from all predecessors
            inputs = self._run_state.merge_state.inputs
            merged_output = {
                "response_text": self._format_merge_response(inputs),
                "confidence": 0.9,
                "stages_completed": list(inputs.keys()),
            }

            self._runtime.complete_merge(merged_output)
            self._emit_event("merge_completed", {
                "merge_node_id": merge_id,
                "duration_ms": (
                    self._run_state.merge_state.completed_at -
                    self._run_state.merge_state.started_at
                ) * 1000 if self._run_state.merge_state.started_at else 0,
            })

        except Exception as e:
            logger.exception(f"Merge execution error")
            self._run_state.merge_state.status = "failed"
            self._run_state.merge_state.error = str(e)
            self._emit_event("merge_failed", {"error": str(e)})

    def _format_merge_response(self, inputs: dict[str, Any]) -> str:
        """Format merge response from inputs."""
        lines = []
        for node_id, output in inputs.items():
            if isinstance(output, dict) and "response_text" in output:
                lines.append(output["response_text"])
            else:
                lines.append(f"[{node_id}] {output}")
        return "\n---\n".join(lines)

    def _complete_dag(self) -> None:
        """Complete the DAG execution."""
        # Check if any required node failed
        any_failed = any(
            s.status == NodeStatus.FAILED
            for s in self._run_state.nodes.values()
        )

        if any_failed:
            self._run_state.status = "failed"
            self._run_state.error = "One or more required nodes failed"
        else:
            self._run_state.status = "completed"

        self._run_state.completed_at = time.time()
        self._emit_event("dag_completed", {
            "status": self._run_state.status,
            "duration_ms": (
                self._run_state.completed_at - self._run_state.started_at
            ) * 1000 if self._run_state.started_at else 0,
        })

    def retry_node(self, node_id: str) -> bool:
        """Manually retry a failed or human-gate node."""
        success = self._runtime.retry_node(node_id)
        if success:
            self._emit_event("node_retry_scheduled", {
                "node_id": node_id,
                "manual": True,
            })
            self._persist_state()
        return success

    def resolve_human_gate(self, node_id: str, approved: bool, result: Any = None) -> bool:
        """Resolve a human gate node."""
        success = self._runtime.resolve_human_gate(node_id, approved, result)
        if success:
            status = "approved" if approved else "rejected"
            self._emit_event("node_human_gate_resolved", {
                "node_id": node_id,
                "action": status,
            })
            self._persist_state()
        return success

    def _emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit a DAG event."""
        event = {
            "event_type": event_type,
            "run_id": self._run_state.run_id,
            "task_id": self.task_id,
            "timestamp": time.time(),
            "data": data,
        }

        for callback in self._event_callbacks:
            try:
                callback(self, event_type, event)
            except Exception as e:
                logger.exception(f"Event callback error: {e}")

    def register_event_callback(self, callback: DagEventCallback) -> None:
        """Register a callback for DAG events."""
        self._event_callbacks.append(callback)

    def _persist_state(self) -> None:
        """Persist current state if persister is configured."""
        if self._persister:
            try:
                self._persister(self._runtime)
            except Exception as e:
                logger.exception(f"State persistence error: {e}")

    def get_state(self) -> DagRuntimeState:
        """Get current runtime state."""
        return self._runtime

    def get_status_summary(self) -> dict[str, Any]:
        """Get a summary of current execution status."""
        node_statuses = {}
        for node_id, state in self._run_state.nodes.items():
            node_statuses[node_id] = {
                "status": state.status.value,
                "attempt": state.attempt,
                "executor": state.executor,
                "error": state.error,
            }

        return {
            "run_id": self._run_state.run_id,
            "task_id": self.task_id,
            "status": self._run_state.status,
            "nodes": node_statuses,
            "merge": {
                "status": self._run_state.merge_state.status,
                "required_complete": self._run_state.merge_state.required_nodes_complete,
                "total_required": self._run_state.merge_state.total_required_nodes,
            },
            "running_count": self._runtime.get_running_count(),
            "ready_count": len(self._runtime.get_ready_nodes()),
        }

    def set_executor(self, name: str, executor: BaseExecutor) -> None:
        """Set or replace an executor."""
        self._executors[name] = executor

    def set_default_executor(self, name: str) -> None:
        """Set the default executor for nodes without hints."""
        if name in self._executors:
            self.config.default_executor = name
