"""Coordinator core implementation with task state machine."""

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from hubos.core.schemas import (
    ConversationEvent,
    ExecutionPlan,
    FinalResponse,
    MergeResult,
    MemoryContext,
    MemoryUpdate,
    TaskResult,
    TaskState,
    TaskStateMachine,
    TaskStatus,
    TaskUnit,
)

logger = logging.getLogger(__name__)


class CoordinatorError(Exception):
    """Base exception for coordinator errors."""

    pass


class InvalidEventError(CoordinatorError):
    """Raised when an invalid event is received."""

    pass


class InvalidStateError(CoordinatorError):
    """Raised when operation is called in invalid state."""

    pass


class Coordinator:
    """
    Coordinator core implementation.

    Owns decomposition, planning, assignment, retries, merge, and finalization.
    Implements the authoritative task state machine per ARCHITECTURE.md.

    Only coordinator can mutate task state (Modular Boundaries rule #3).
    """

    def __init__(self, worker_registry: Optional[dict[str, Any]] = None) -> None:
        """
        Initialize the coordinator.

        Args:
            worker_registry: Optional registry of available worker providers.
        """
        self._state_machine = TaskStateMachine()
        self._worker_registry = worker_registry or {}
        self._task_units: dict[UUID, TaskUnit] = {}
        self._task_results: dict[UUID, TaskResult] = {}
        self._execution_plan: Optional[ExecutionPlan] = None
        self._event: Optional[ConversationEvent] = None
        self._final_response: Optional[FinalResponse] = None
        self._normalized_data: dict[str, Any] = {}

    @property
    def current_state(self) -> TaskState:
        """Get the current task state."""
        return self._state_machine.current_state

    @property
    def task_id(self) -> Optional[str]:
        """Get the current task ID."""
        if self._event:
            return self._event.task_id
        return None

    @property
    def trace_id(self) -> str:
        """Get the trace ID from the current event."""
        if self._event:
            return self._event.trace_id
        return ""

    def process_event(self, event: ConversationEvent) -> None:
        """
        Process an incoming conversation event.

        State: RECEIVED -> NORMALIZED

        Args:
            event: The conversation event to process.

        Raises:
            InvalidEventError: If the event is invalid.
        """
        if not event.trace_id:
            raise InvalidEventError("event.trace_id is required")
        if not event.session_id:
            raise InvalidEventError("event.session_id is required")

        self._event = event
        logger.info(
            "Processing event",
            extra={
                "trace_id": event.trace_id,
                "session_id": event.session_id,
                "task_id": event.task_id,
                "event_id": str(event.event_id),
            },
        )
        self._transition_to(TaskState.NORMALIZED)

    def normalize(self, normalized_data: dict[str, Any]) -> None:
        """
        Normalize the incoming event data.

        This method performs normalization but does NOT change state.
        State transition to PLANNED happens in create_plan().

        State: stays NORMALIZED

        Args:
            normalized_data: The normalized event data.

        Raises:
            InvalidStateError: If called in wrong state.
        """
        if self.current_state != TaskState.NORMALIZED:
            raise InvalidStateError(
                f"normalize() called in state {self.current_state.value}, expected NORMALIZED"
            )
        self._normalized_data = normalized_data
        logger.info(
            "Event normalized",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
            },
        )

    def create_plan(self, steps: list[dict[str, Any]], acceptance_criteria: list[str]) -> ExecutionPlan:
        """
        Create an execution plan.

        State: NORMALIZED -> PLANNED

        Args:
            steps: List of step definitions.
            acceptance_criteria: List of acceptance criteria.

        Returns:
            The created execution plan.

        Raises:
            CoordinatorError: If plan creation fails.
            InvalidStateError: If called in wrong state.
        """
        if self.current_state != TaskState.NORMALIZED:
            raise InvalidStateError(
                f"create_plan() called in state {self.current_state.value}, expected NORMALIZED"
            )
        if not self._event:
            raise CoordinatorError("No event loaded")

        plan = ExecutionPlan(
            task_id=self._event.task_id or str(uuid4()),
            trace_id=self._event.trace_id,
            session_id=self._event.session_id,
            steps=self._create_plan_steps(steps),
            acceptance_criteria=acceptance_criteria,
        )
        self._execution_plan = plan
        self._transition_to(TaskState.PLANNED)
        logger.info(
            "Execution plan created",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
                "plan_id": str(plan.plan_id),
                "steps_count": len(steps),
            },
        )
        return plan

    def dispatch(self) -> None:
        """
        Dispatch the task to workers.

        State: PLANNED -> DISPATCHED

        Raises:
            InvalidStateError: If called in wrong state.
        """
        if self.current_state != TaskState.PLANNED:
            raise InvalidStateError(
                f"dispatch() called in state {self.current_state.value}, expected PLANNED"
            )
        self._transition_to(TaskState.DISPATCHED)
        logger.info(
            "Task dispatched",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
            },
        )

    def receive_result(self, result: TaskResult) -> None:
        """
        Receive a task result from a worker.

        Transitions DISPATCHED -> RUNNING when first result is received.

        Args:
            result: The task result from the worker.

        Raises:
            InvalidStateError: If called in wrong state.
        """
        if self.current_state != TaskState.DISPATCHED and self.current_state != TaskState.RUNNING:
            raise InvalidStateError(
                f"receive_result() called in state {self.current_state.value}, expected DISPATCHED or RUNNING"
            )
        # Transition to RUNNING if we're still in DISPATCHED (first result)
        if self.current_state == TaskState.DISPATCHED:
            self._transition_to(TaskState.RUNNING)

        self._task_results[result.unit_id] = result
        logger.info(
            "Result received",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
                "unit_id": str(result.unit_id),
                "status": result.status.value,
                "confidence": result.confidence,
            },
        )

    def merge(self) -> FinalResponse:
        """
        Merge all task results into a final response.

        State: RUNNING -> MERGING -> RESPONDED

        Note: DISPATCHED -> RUNNING transition happens in receive_result().

        Returns:
            The final merged response.

        Raises:
            CoordinatorError: If merge fails.
            InvalidStateError: If called in state other than RUNNING.
        """
        if self.current_state != TaskState.RUNNING:
            raise InvalidStateError(
                f"merge() called in state {self.current_state.value}, expected RUNNING. "
                f"Did you forget to call receive_result() first?"
            )

        self._transition_to(TaskState.MERGING)

        if not self._execution_plan:
            raise CoordinatorError("No execution plan available")

        # Minimal merge strategy: concatenate all results
        results = list(self._task_results.values())
        merge_results: list[MergeResult] = []
        all_content: list[str] = []
        conflicts: list[str] = []
        high_conflicts: list[str] = []  # Different conclusions = HIGH severity
        all_artifacts: list[dict[str, Any]] = []

        for result in results:
            mr = MergeResult(
                unit_id=result.unit_id,
                status="merged",
                merged_data=result.output_data,
            )

            # Check for HIGH conflicts (different conclusions)
            if result.output_data.get("conclusion") and all_content:
                if result.output_data["conclusion"] != all_content[-1]:
                    high_conflicts.append(
                        f"Unit {result.unit_id}: conflicting conclusions: "
                        f"{result.output_data['conclusion']} vs {all_content[-1]}"
                    )
                    conflicts.append(f"Unit {result.unit_id}: conflicting conclusions")

            if result.output_data.get("content"):
                all_content.append(result.output_data["content"])
            all_artifacts.extend(result.artifacts)

            # Check confidence (MEDIUM severity - auto-merge with warning)
            if result.confidence < 0.7:
                conflicts.append(f"Unit {result.unit_id}: low confidence {result.confidence}")

            merge_results.append(mr)

        # Build final content
        if all_content:
            final_content = "\n\n".join(all_content)
        else:
            final_content = "No content generated"

        response = FinalResponse(
            task_id=self._execution_plan.task_id,
            session_id=self._execution_plan.session_id,
            trace_id=self._execution_plan.trace_id,
            content=final_content,
            merge_results=merge_results,
            conflict_summary="; ".join(conflicts) if conflicts else None,
            artifacts=all_artifacts,
        )

        self._final_response = response

        # Check for HIGH severity conflicts (different conclusions) - require human
        if high_conflicts:
            logger.warning(
                "HIGH severity conflicts detected, requesting human intervention",
                extra={
                    "trace_id": self.trace_id,
                    "task_id": self.task_id,
                    "high_conflicts": high_conflicts,
                },
            )
            self.request_human(f"HIGH conflict: {'; '.join(high_conflicts)}")
            return response

        self._transition_to(TaskState.RESPONDED)
        logger.info(
            "Results merged",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
                "results_count": len(results),
                "has_conflicts": bool(conflicts),
            },
        )
        return response

    def persist(self) -> None:
        """
        Persist the final response.

        State: RESPONDED -> PERSISTED (terminal).

        Raises:
            InvalidStateError: If called in wrong state.
        """
        if self.current_state != TaskState.RESPONDED:
            raise InvalidStateError(
                f"persist() called in state {self.current_state.value}, expected RESPONDED"
            )
        self._transition_to(TaskState.PERSISTED)
        logger.info(
            "Response persisted",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
            },
        )

    def retry(self) -> None:
        """
        Retry the current task.

        State: RUNNING -> RETRYING -> RUNNING

        Raises:
            InvalidStateError: If called in wrong state.
        """
        if self.current_state != TaskState.RUNNING:
            raise InvalidStateError(
                f"retry() called in state {self.current_state.value}, expected RUNNING"
            )
        self._transition_to(TaskState.RETRYING)
        logger.info(
            "Task scheduled for retry",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
            },
        )

    def fail(self, error: str) -> None:
        """
        Mark the task as failed.

        State: any non-terminal -> FAILED (terminal).

        Args:
            error: The error message.
        """
        if self._state_machine.is_terminal():
            raise InvalidStateError(f"fail() called but state {self.current_state.value} is terminal")
        self._transition_to(TaskState.FAILED)
        logger.error(
            "Task failed",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
                "error": error,
            },
        )

    def request_human(self, reason: str) -> None:
        """
        Request human intervention.

        State: MERGING -> NEEDS_HUMAN (terminal).

        Args:
            reason: The reason for needing human intervention.
        """
        if self.current_state != TaskState.MERGING:
            raise InvalidStateError(
                f"request_human() called in state {self.current_state.value}, expected MERGING"
            )
        self._transition_to(TaskState.NEEDS_HUMAN)
        logger.warning(
            "Human intervention requested",
            extra={
                "trace_id": self.trace_id,
                "task_id": self.task_id,
                "reason": reason,
            },
        )

    def get_memory_context(self) -> MemoryContext:
        """
        Get memory context before planning.

        Returns:
            Memory context with relevant entries.
        """
        if not self._event:
            raise CoordinatorError("No event loaded")

        return MemoryContext(
            task_id=self._event.task_id or str(uuid4()),
            session_id=self._event.session_id,
            trace_id=self._event.trace_id,
            entries=[],
        )

    def write_memory(self, update: MemoryUpdate) -> None:
        """
        Write memory after final response.

        Args:
            update: The memory update to write.
        """
        logger.info(
            "Writing memory",
            extra={
                "trace_id": update.trace_id,
                "task_id": update.task_id,
                "namespace": update.namespace.value,
            },
        )

    def _transition_to(self, target: TaskState) -> None:
        """
        Internal method to transition state.

        Args:
            target: The target state.

        Raises:
            CoordinatorError: If transition fails.
        """
        try:
            self._state_machine.transition(target)
        except Exception as e:
            raise CoordinatorError(f"State transition failed: {e}") from e

    def _create_plan_steps(self, steps: list[dict[str, Any]]) -> list:
        """Create plan steps from step definitions."""
        from hubos.core.schemas.planning import PlanStep, PlanStepType

        plan_steps = []
        for step_def in steps:
            plan_steps.append(
                PlanStep(
                    name=step_def.get("name", ""),
                    step_type=PlanStepType(step_def.get("type", "sequential")),
                    worker_provider=step_def.get("worker_provider"),
                    input_data=step_def.get("input_data", {}),
                    acceptance_criteria=step_def.get("acceptance_criteria", []),
                    timeout_seconds=step_def.get("timeout_seconds", 300),
                )
            )
        return plan_steps
