"""Tests for schema validation."""

import pytest
from uuid import uuid4

from hubos.core.schemas.events import ConversationEvent, EventSource, SourceMetadata
from hubos.core.schemas.memory import MemoryContext, MemoryEntry, MemoryNamespace, MemoryUpdate
from hubos.core.schemas.planning import ExecutionPlan, PlanStep, PlanStepType
from hubos.core.schemas.tasks import TaskResult, TaskStatus, TaskUnit
from hubos.core.schemas.responses import FinalResponse, MergeResult
from hubos.core.schemas.state import TaskState, TaskStateMachine


class TestConversationEvent:
    """Test ConversationEvent schema."""

    def test_valid_event(self) -> None:
        """Test creating a valid event."""
        event = ConversationEvent(
            trace_id="trace-123",
            session_id="session-456",
            payload={"text": "hello"},
        )
        assert event.trace_id == "trace-123"
        assert event.session_id == "session-456"
        assert event.schema_version == "1.0.0"

    def test_event_requires_trace_id(self) -> None:
        """Test event requires trace_id."""
        with pytest.raises(ValueError, match="trace_id"):
            ConversationEvent(trace_id="", session_id="session-123")

    def test_event_requires_session_id(self) -> None:
        """Test event requires session_id."""
        with pytest.raises(ValueError, match="session_id"):
            ConversationEvent(trace_id="trace-123", session_id="")


class TestTaskUnit:
    """Test TaskUnit schema."""

    def test_valid_task_unit(self) -> None:
        """Test creating a valid task unit."""
        unit = TaskUnit(
            task_id="task-123",
            worker_provider="claude",
            input_data={"query": "test"},
        )
        assert unit.task_id == "task-123"
        assert unit.worker_provider == "claude"

    def test_task_unit_requires_task_id(self) -> None:
        """Test task unit requires task_id."""
        with pytest.raises(ValueError, match="task_id"):
            TaskUnit(task_id="", worker_provider="claude")

    def test_task_unit_requires_worker_provider(self) -> None:
        """Test task unit requires worker_provider."""
        with pytest.raises(ValueError, match="worker_provider"):
            TaskUnit(task_id="task-123", worker_provider="")


class TestTaskResult:
    """Test TaskResult schema."""

    def test_valid_result(self) -> None:
        """Test creating a valid task result."""
        result = TaskResult(
            unit_id=uuid4(),
            task_id="task-123",
            status=TaskStatus.SUCCESS,
            confidence=0.95,
            output_data={"answer": "42"},
        )
        assert result.confidence == 0.95
        assert result.status == TaskStatus.SUCCESS

    def test_confidence_must_be_valid_range(self) -> None:
        """Test confidence must be between 0 and 1."""
        with pytest.raises(ValueError, match="confidence"):
            TaskResult(
                unit_id=uuid4(),
                task_id="task-123",
                confidence=1.5,
            )


class TestExecutionPlan:
    """Test ExecutionPlan schema."""

    def test_valid_plan(self) -> None:
        """Test creating a valid execution plan."""
        plan = ExecutionPlan(
            task_id="task-123",
            trace_id="trace-456",
            session_id="session-789",
            steps=[
                PlanStep(name="step1", step_type=PlanStepType.SEQUENTIAL),
            ],
        )
        assert plan.task_id == "task-123"
        assert len(plan.steps) == 1

    def test_plan_requires_task_id(self) -> None:
        """Test plan requires task_id."""
        with pytest.raises(ValueError, match="task_id"):
            ExecutionPlan(task_id="", trace_id="trace", session_id="session", steps=[])

    def test_plan_requires_steps(self) -> None:
        """Test plan requires at least one step."""
        with pytest.raises(ValueError, match="at least one step"):
            ExecutionPlan(task_id="task-123", trace_id="trace", session_id="session", steps=[])


class TestMemoryEntry:
    """Test MemoryEntry schema."""

    def test_valid_entry(self) -> None:
        """Test creating a valid memory entry."""
        entry = MemoryEntry(
            namespace=MemoryNamespace.USER_PROFILE,
            key="user:123",
            value={"name": "Test User"},
            session_id="session-456",
        )
        assert entry.namespace == MemoryNamespace.USER_PROFILE
        assert entry.key == "user:123"


class TestMemoryContext:
    """Test MemoryContext schema."""

    def test_valid_context(self) -> None:
        """Test creating a valid memory context."""
        ctx = MemoryContext(
            task_id="task-123",
            session_id="session-456",
            trace_id="trace-789",
        )
        assert ctx.task_id == "task-123"
        assert ctx.schema_version == "1.0.0"

    def test_context_requires_task_id(self) -> None:
        """Test context requires task_id."""
        with pytest.raises(ValueError, match="task_id"):
            MemoryContext(task_id="", session_id="session", trace_id="trace")


class TestMemoryUpdate:
    """Test MemoryUpdate schema."""

    def test_valid_update(self) -> None:
        """Test creating a valid memory update."""
        update = MemoryUpdate(
            task_id="task-123",
            session_id="session-456",
            trace_id="trace-789",
            namespace=MemoryNamespace.EPISODIC_TASK,
        )
        assert update.namespace == MemoryNamespace.EPISODIC_TASK

    def test_update_requires_task_id(self) -> None:
        """Test update requires task_id."""
        with pytest.raises(ValueError, match="task_id"):
            MemoryUpdate(task_id="", session_id="session", trace_id="trace")


class TestFinalResponse:
    """Test FinalResponse schema."""

    def test_valid_response(self) -> None:
        """Test creating a valid final response."""
        response = FinalResponse(
            task_id="task-123",
            session_id="session-456",
            trace_id="trace-789",
            content="Final answer",
        )
        assert response.task_id == "task-123"
        assert response.content == "Final answer"

    def test_response_requires_task_id(self) -> None:
        """Test response requires task_id."""
        with pytest.raises(ValueError, match="task_id"):
            FinalResponse(task_id="", session_id="session", trace_id="trace")
