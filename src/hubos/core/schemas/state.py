"""Task state machine definitions."""

from enum import Enum
from typing import Callable, Optional


class TaskState(str, Enum):
    """
    Canonical task states as defined in ARCHITECTURE.md.

    State transitions:
    RECEIVED -> NORMALIZED -> PLANNED -> DISPATCHED -> RUNNING -> MERGING -> RESPONDED -> PERSISTED

    Error arcs:
    RUNNING -> RETRYING -> RUNNING
    RUNNING -> FAILED
    MERGING -> NEEDS_HUMAN
    """

    RECEIVED = "received"
    NORMALIZED = "normalized"
    PLANNED = "planned"
    DISPATCHED = "dispatched"
    RUNNING = "running"
    MERGING = "merging"
    RESPONDED = "responded"
    PERSISTED = "persisted"
    RETRYING = "retrying"
    FAILED = "failed"
    NEEDS_HUMAN = "needs_human"


# Legal state transitions: current_state -> set of valid next states
LEGAL_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.RECEIVED: {TaskState.NORMALIZED},
    TaskState.NORMALIZED: {TaskState.PLANNED},
    TaskState.PLANNED: {TaskState.DISPATCHED},
    TaskState.DISPATCHED: {TaskState.RUNNING},
    TaskState.RUNNING: {TaskState.MERGING, TaskState.RETRYING, TaskState.FAILED},
    TaskState.RETRYING: {TaskState.RUNNING},
    TaskState.MERGING: {TaskState.RESPONDED, TaskState.NEEDS_HUMAN},
    TaskState.RESPONDED: {TaskState.PERSISTED},
    TaskState.PERSISTED: set(),  # Terminal state
    TaskState.FAILED: set(),  # Terminal state
    TaskState.NEEDS_HUMAN: set(),  # Terminal state
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal state transition is attempted."""

    def __init__(self, current: TaskState, target: TaskState) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Invalid transition from {current.value} to {target.value}")


class TaskStateMachine:
    """
    Task state machine implementation.

    Only the coordinator can mutate task state.
    Implements all legal transitions per ARCHITECTURE.md.
    """

    def __init__(self, initial_state: TaskState = TaskState.RECEIVED) -> None:
        self._state = initial_state
        self._transition_history: list[tuple[TaskState, TaskState]] = []

    @property
    def current_state(self) -> TaskState:
        """Get the current state."""
        return self._state

    @property
    def transition_history(self) -> list[tuple[TaskState, TaskState]]:
        """Get the history of state transitions."""
        return self._transition_history.copy()

    def can_transition(self, target: TaskState) -> bool:
        """Check if a transition to target state is legal."""
        return target in LEGAL_TRANSITIONS.get(self._state, set())

    def transition(self, target: TaskState, validator: Optional[Callable[[TaskState, TaskState], bool]] = None) -> None:
        """
        Transition to target state.

        Args:
            target: The target state to transition to.
            validator: Optional custom validator function.

        Raises:
            InvalidStateTransitionError: If the transition is not legal.
        """
        if not self.can_transition(target):
            raise InvalidStateTransitionError(self._state, target)

        if validator and not validator(self._state, target):
            raise InvalidStateTransitionError(self._state, target)

        self._transition_history.append((self._state, target))
        self._state = target

    def is_terminal(self) -> bool:
        """Check if current state is a terminal state."""
        return self._state in {TaskState.PERSISTED, TaskState.FAILED, TaskState.NEEDS_HUMAN}
