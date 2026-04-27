# -*- coding: utf-8 -*-
"""Tests for task state machine legal and illegal transitions."""

import pytest

from hubos.core.schemas.state import (
    InvalidStateTransitionError,
    LEGAL_TRANSITIONS,
    TaskState,
    TaskStateMachine,
)


class TestLegalTransitions:
    """Test all legal state transitions."""

    def test_received_to_normalized(self) -> None:
        """Test RECEIVED -> NORMALIZED transition."""
        sm = TaskStateMachine(TaskState.RECEIVED)
        assert sm.current_state == TaskState.RECEIVED
        sm.transition(TaskState.NORMALIZED)
        assert sm.current_state == TaskState.NORMALIZED

    def test_normalized_to_planned(self) -> None:
        """Test NORMALIZED -> PLANNED transition."""
        sm = TaskStateMachine(TaskState.NORMALIZED)
        sm.transition(TaskState.PLANNED)
        assert sm.current_state == TaskState.PLANNED

    def test_planned_to_dispatched(self) -> None:
        """Test PLANNED -> DISPATCHED transition."""
        sm = TaskStateMachine(TaskState.PLANNED)
        sm.transition(TaskState.DISPATCHED)
        assert sm.current_state == TaskState.DISPATCHED

    def test_dispatched_to_running(self) -> None:
        """Test DISPATCHED -> RUNNING transition."""
        sm = TaskStateMachine(TaskState.DISPATCHED)
        sm.transition(TaskState.RUNNING)
        assert sm.current_state == TaskState.RUNNING

    def test_running_to_merging(self) -> None:
        """Test RUNNING -> MERGING transition."""
        sm = TaskStateMachine(TaskState.RUNNING)
        sm.transition(TaskState.MERGING)
        assert sm.current_state == TaskState.MERGING

    def test_running_to_retrying(self) -> None:
        """Test RUNNING -> RETRYING error arc."""
        sm = TaskStateMachine(TaskState.RUNNING)
        sm.transition(TaskState.RETRYING)
        assert sm.current_state == TaskState.RETRYING

    def test_retrying_to_running(self) -> None:
        """Test RETRYING -> RUNNING error arc."""
        sm = TaskStateMachine(TaskState.RETRYING)
        sm.transition(TaskState.RUNNING)
        assert sm.current_state == TaskState.RUNNING

    def test_running_to_failed(self) -> None:
        """Test RUNNING -> FAILED error arc."""
        sm = TaskStateMachine(TaskState.RUNNING)
        sm.transition(TaskState.FAILED)
        assert sm.current_state == TaskState.FAILED

    def test_merging_to_responded(self) -> None:
        """Test MERGING -> RESPONDED transition."""
        sm = TaskStateMachine(TaskState.MERGING)
        sm.transition(TaskState.RESPONDED)
        assert sm.current_state == TaskState.RESPONDED

    def test_merging_to_needs_human(self) -> None:
        """Test MERGING -> NEEDS_HUMAN error arc."""
        sm = TaskStateMachine(TaskState.MERGING)
        sm.transition(TaskState.NEEDS_HUMAN)
        assert sm.current_state == TaskState.NEEDS_HUMAN

    def test_responded_to_persisted(self) -> None:
        """Test RESPONDED -> PERSISTED transition."""
        sm = TaskStateMachine(TaskState.RESPONDED)
        sm.transition(TaskState.PERSISTED)
        assert sm.current_state == TaskState.PERSISTED


class TestIllegalTransitions:
    """Test illegal state transitions raise errors."""

    def test_cannot_skip_states(self) -> None:
        """Test that states cannot be skipped."""
        sm = TaskStateMachine(TaskState.RECEIVED)
        with pytest.raises(InvalidStateTransitionError) as exc_info:
            sm.transition(TaskState.PLANNED)
        assert exc_info.value.current == TaskState.RECEIVED
        assert exc_info.value.target == TaskState.PLANNED

    def test_cannot_go_backward(self) -> None:
        """Test that backward transitions are not allowed."""
        sm = TaskStateMachine(TaskState.RUNNING)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(TaskState.DISPATCHED)

    def test_cannot_transition_from_persisted(self) -> None:
        """Test that PERSISTED is terminal."""
        sm = TaskStateMachine(TaskState.PERSISTED)
        assert sm.is_terminal()
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(TaskState.RECEIVED)

    def test_cannot_transition_from_failed(self) -> None:
        """Test that FAILED is terminal."""
        sm = TaskStateMachine(TaskState.FAILED)
        assert sm.is_terminal()
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(TaskState.RETRYING)

    def test_cannot_transition_from_needs_human(self) -> None:
        """Test that NEEDS_HUMAN is terminal."""
        sm = TaskStateMachine(TaskState.NEEDS_HUMAN)
        assert sm.is_terminal()
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(TaskState.MERGING)

    def test_cannot_go_from_running_to_planned(self) -> None:
        """Test invalid transition from RUNNING."""
        sm = TaskStateMachine(TaskState.RUNNING)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(TaskState.PLANNED)

    def test_cannot_go_from_merging_to_running(self) -> None:
        """Test invalid transition from MERGING."""
        sm = TaskStateMachine(TaskState.MERGING)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(TaskState.RUNNING)

    def test_cannot_go_from_dispatched_to_normalized(self) -> None:
        """Test invalid transition from DISPATCHED."""
        sm = TaskStateMachine(TaskState.DISPATCHED)
        with pytest.raises(InvalidStateTransitionError):
            sm.transition(TaskState.NORMALIZED)


class TestStateMachineProperties:
    """Test state machine properties."""

    def test_initial_state_default(self) -> None:
        """Test default initial state is RECEIVED."""
        sm = TaskStateMachine()
        assert sm.current_state == TaskState.RECEIVED

    def test_initial_state_custom(self) -> None:
        """Test custom initial state."""
        sm = TaskStateMachine(TaskState.RUNNING)
        assert sm.current_state == TaskState.RUNNING

    def test_transition_history(self) -> None:
        """Test transition history is recorded."""
        sm = TaskStateMachine(TaskState.RECEIVED)
        sm.transition(TaskState.NORMALIZED)
        sm.transition(TaskState.PLANNED)
        history = sm.transition_history
        assert len(history) == 2
        assert history[0] == (TaskState.RECEIVED, TaskState.NORMALIZED)
        assert history[1] == (TaskState.NORMALIZED, TaskState.PLANNED)

    def test_can_transition_legal(self) -> None:
        """Test can_transition returns True for legal transitions."""
        sm = TaskStateMachine(TaskState.RECEIVED)
        assert sm.can_transition(TaskState.NORMALIZED) is True

    def test_can_transition_illegal(self) -> None:
        """Test can_transition returns False for illegal transitions."""
        sm = TaskStateMachine(TaskState.RECEIVED)
        assert sm.can_transition(TaskState.PLANNED) is False

    def test_is_terminal_false_for_non_terminal(self) -> None:
        """Test is_terminal returns False for non-terminal states."""
        sm = TaskStateMachine(TaskState.RUNNING)
        assert sm.is_terminal() is False

    def test_is_terminal_true_for_terminal(self) -> None:
        """Test is_terminal returns True for terminal states."""
        for terminal_state in [
            TaskState.PERSISTED,
            TaskState.FAILED,
            TaskState.NEEDS_HUMAN,
        ]:
            sm = TaskStateMachine(terminal_state)
            assert sm.is_terminal() is True


class TestLegalTransitionsComplete:
    """Verify all legal transitions from LEGAL_TRANSITIONS map are tested."""

    def test_all_legal_transitions_defined(self) -> None:
        """Verify LEGAL_TRANSITIONS has entries for all non-terminal states."""
        for state in TaskState:
            if state not in [
                TaskState.PERSISTED,
                TaskState.FAILED,
                TaskState.NEEDS_HUMAN,
            ]:
                assert (
                    state in LEGAL_TRANSITIONS
                ), f"Missing transition definition for {state}"

    def test_all_legal_transitions_work(self) -> None:
        """Test that all defined legal transitions succeed."""
        for current_state, target_states in LEGAL_TRANSITIONS.items():
            for target_state in target_states:
                sm = TaskStateMachine(current_state)
                sm.transition(target_state)
                assert sm.current_state == target_state
