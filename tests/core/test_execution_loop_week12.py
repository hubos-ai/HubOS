# -*- coding: utf-8 -*-
"""Week 12 Execution Loop MVP tests."""

import pytest
import threading
import time
from unittest.mock import MagicMock, patch

from hubos.core.execution.task_store import (
    TaskStore,
    TaskStatus,
    TaskStage,
    StageStatus,
    DeadLetterQueue,
)
from hubos.core.execution.event_store import (
    EventStore,
    EventType,
)
from hubos.core.execution.orchestrator import ExecutionOrchestrator


class TestTaskStore:
    """Test task store operations."""

    def test_create_task(self):
        """Test creating a new task."""
        store = TaskStore()
        task = store.create_task(
            input_text="Test task",
            session_id="session-123",
            channel="api",
            priority="high",
            requested_workflow="one_person_default",
        )

        assert task.task_id is not None
        assert task.trace_id is not None
        assert task.input_text == "Test task"
        assert task.session_id == "session-123"
        assert task.channel == "api"
        assert task.priority == "high"
        assert task.requested_workflow == "one_person_default"
        assert task.current_status == TaskStatus.RECEIVED
        assert task.retry_count == 0
        assert task.max_retries == 3

    def test_get_task(self):
        """Test retrieving a task."""
        store = TaskStore()
        created = store.create_task(input_text="Test task")
        retrieved = store.get_task(created.task_id)

        assert retrieved is not None
        assert retrieved.task_id == created.task_id

    def test_get_task_not_found(self):
        """Test retrieving non-existent task."""
        store = TaskStore()
        task = store.get_task("nonexistent")
        assert task is None

    def test_update_status(self):
        """Test updating task status."""
        store = TaskStore()
        task = store.create_task(input_text="Test task")

        updated = store.update_status(task.task_id, TaskStatus.RUNNING)
        assert updated is not None
        assert updated.current_status == TaskStatus.RUNNING
        assert updated.started_at is not None

    def test_update_stage_status(self):
        """Test updating stage status."""
        store = TaskStore()
        task = store.create_task(input_text="Test task")

        updated = store.update_stage_status(
            task_id=task.task_id,
            stage=TaskStage.CEO,
            status="running",
        )
        assert updated is not None
        assert TaskStage.CEO.value in updated.stage_statuses
        assert updated.stage_statuses[TaskStage.CEO.value].status == "running"

    def test_list_tasks(self):
        """Test listing tasks."""
        store = TaskStore()
        for i in range(5):
            store.create_task(input_text=f"Task {i}")

        tasks = store.list_tasks(limit=10)
        assert len(tasks) == 5

    def test_list_tasks_pagination(self):
        """Test task list pagination."""
        store = TaskStore()
        for i in range(5):
            store.create_task(input_text=f"Task {i}")

        tasks = store.list_tasks(limit=2, offset=0)
        assert len(tasks) == 2

        tasks_page2 = store.list_tasks(limit=2, offset=2)
        assert len(tasks_page2) == 2

    def test_count(self):
        """Test task count."""
        store = TaskStore()
        for i in range(3):
            store.create_task(input_text=f"Task {i}")

        assert store.count() == 3

    def test_thread_safety(self):
        """Test thread-safe operations."""
        store = TaskStore()
        errors = []

        def create_tasks():
            try:
                for i in range(10):
                    store.create_task(input_text=f"Task {i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_tasks) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert store.count() == 50


class TestDeadLetterQueue:
    """Test dead letter queue operations."""

    def test_add_entry(self):
        """Test adding entry to DLQ."""
        dlq = DeadLetterQueue()
        dlq.add("task-1", "Max retries exceeded", {"task_id": "task-1"})

        assert dlq.count() == 1
        entry = dlq.get("task-1")
        assert entry is not None
        assert entry["reason"] == "Max retries exceeded"

    def test_list_all(self):
        """Test listing all DLQ entries."""
        dlq = DeadLetterQueue()
        dlq.add("task-1", "Error 1", {"task_id": "task-1"})
        dlq.add("task-2", "Error 2", {"task_id": "task-2"})

        entries = dlq.list_all()
        assert len(entries) == 2

    def test_remove_entry(self):
        """Test removing entry from DLQ."""
        dlq = DeadLetterQueue()
        dlq.add("task-1", "Error", {"task_id": "task-1"})

        removed = dlq.remove("task-1")
        assert removed is True
        assert dlq.count() == 0

        removed = dlq.remove("nonexistent")
        assert removed is False


class TestTaskStoreRetry:
    """Test task retry and recovery."""

    def test_retry_task(self):
        """Test retrying a failed task."""
        store = TaskStore()
        task = store.create_task(input_text="Test task")
        store.update_status(
            task.task_id,
            TaskStatus.FAILED,
            failure_reason="Test failure",
        )

        retried = store.retry_task(task.task_id)
        assert retried is not None
        assert retried.current_status == TaskStatus.RECEIVED
        assert retried.retry_count == 1
        assert retried.failure_reason is None

    def test_retry_task_max_retries_exceeded(self):
        """Test retry task when max retries exceeded."""
        store = TaskStore()
        task = store.create_task(input_text="Test task")
        task.retry_count = 3
        task.max_retries = 3
        store.update_status(
            task.task_id,
            TaskStatus.FAILED,
            failure_reason="Test failure",
        )

        retried = store.retry_task(task.task_id)
        assert retried is None

    def test_retry_task_not_failed(self):
        """Test retry task that is not failed."""
        store = TaskStore()
        task = store.create_task(input_text="Test task")

        retried = store.retry_task(task.task_id)
        assert retried is None

    def test_move_to_dlq(self):
        """Test moving task to DLQ."""
        store = TaskStore()
        task = store.create_task(input_text="Test task")

        result = store.move_to_dlq(task.task_id, "Unrecoverable error")
        assert result is True
        assert store.dlq.count() == 1

        dlq_entry = store.dlq.get(task.task_id)
        assert dlq_entry is not None
        assert "Unrecoverable error" in dlq_entry["reason"]

    def test_reset_task(self):
        """Test force resetting a task."""
        store = TaskStore()
        task = store.create_task(input_text="Test task")
        store.update_status(task.task_id, TaskStatus.RUNNING)
        store.update_stage_status(
            task.task_id,
            TaskStage.CEO,
            status="completed",
        )

        reset = store.reset_task(task.task_id)
        assert reset is not None
        assert reset.current_status == TaskStatus.RECEIVED
        assert reset.stage_statuses[TaskStage.CEO.value].status == "pending"

    def test_get_stuck_tasks(self):
        """Test getting stuck tasks."""
        from datetime import datetime, timedelta, timezone

        store = TaskStore()
        task = store.create_task(input_text="Test task")
        task.current_status = TaskStatus.RUNNING
        task.started_at = datetime.now(timezone.utc) - timedelta(seconds=400)

        stuck = store.get_stuck_tasks(timeout_seconds=300)
        assert len(stuck) == 1
        assert stuck[0].task_id == task.task_id

    def test_get_failed_tasks(self):
        """Test getting failed tasks."""
        store = TaskStore()
        task1 = store.create_task(input_text="Task 1")
        task2 = store.create_task(input_text="Task 2")
        store.update_status(
            task1.task_id,
            TaskStatus.FAILED,
            failure_reason="Error 1",
        )
        store.update_status(task2.task_id, TaskStatus.DONE)

        failed = store.get_failed_tasks()
        assert len(failed) == 1
        assert failed[0].task_id == task1.task_id


class TestEventStore:
    """Test event store operations."""

    def test_add_event(self):
        """Test adding an event."""
        store = EventStore()
        event = store.add_event(
            task_id="task-1",
            trace_id="trace-1",
            event_type=EventType.TASK_SUBMITTED,
            to_status="received",
        )

        assert event is not None
        assert event.task_id == "task-1"
        assert event.event_type == EventType.TASK_SUBMITTED

    def test_get_events(self):
        """Test retrieving events."""
        store = EventStore()
        store.add_event(
            task_id="task-1",
            trace_id="trace-1",
            event_type=EventType.TASK_SUBMITTED,
        )
        store.add_event(
            task_id="task-1",
            trace_id="trace-1",
            event_type=EventType.STAGE_DISPATCH,
        )

        events = store.get_events("task-1")
        assert len(events) == 2

    def test_get_events_empty(self):
        """Test retrieving events for non-existent task."""
        store = EventStore()
        events = store.get_events("nonexistent")
        assert len(events) == 0


class TestExecutionOrchestrator:
    """Test execution orchestrator."""

    def test_submit_task(self):
        """Test task submission."""
        with patch(
            "hubos.core.infra.metrics.get_metrics_service",
        ) as mock_metrics:
            mock_metrics.return_value = MagicMock()
            orchestrator = ExecutionOrchestrator()
            task = orchestrator.submit_task(input_text="Test task")

            assert task is not None
            assert task.input_text == "Test task"
            assert task.current_status == TaskStatus.RECEIVED

    def test_execute_task_not_found(self):
        """Test executing non-existent task."""
        with patch("hubos.core.infra.metrics.get_metrics_service"):
            orchestrator = ExecutionOrchestrator()
            with pytest.raises(ValueError, match="Task not found"):
                orchestrator.execute_task("nonexistent")

    def test_execute_task_invalid_state(self):
        """Test executing task in invalid state."""
        with patch(
            "hubos.core.infra.metrics.get_metrics_service",
        ) as mock_metrics:
            mock_metrics.return_value = MagicMock()
            orchestrator = ExecutionOrchestrator()
            task = orchestrator.submit_task(input_text="Test task")
            # Execute once - this should transition to RUNNING then DONE (or fail if no agents)
            # Since we're testing invalid state, we just check that a second execute fails
            try:
                orchestrator.execute_task(task.task_id)
            except Exception:
                pass  # Expected to fail if no agents

            # Now try to execute again - should fail with "not in executable state"
            with pytest.raises(ValueError, match="not in executable state"):
                orchestrator.execute_task(task.task_id)

    def test_human_gate(self):
        """Test entering human gate."""
        with patch(
            "hubos.core.infra.metrics.get_metrics_service",
        ) as mock_metrics:
            mock_metrics.return_value = MagicMock()
            orchestrator = ExecutionOrchestrator()
            task = orchestrator.submit_task(input_text="Test task")
            # Move task to running state manually for testing human gate
            orchestrator._transition_status(task, TaskStatus.RUNNING)

            gate_task = orchestrator.enter_human_gate(
                task.task_id,
                "Needs approval",
            )
            assert gate_task.current_status == TaskStatus.HUMAN_GATE


class TestTaskStatusEnum:
    """Test task status enumeration."""

    def test_status_values(self):
        """Test status enum values."""
        assert TaskStatus.RECEIVED.value == "received"
        assert TaskStatus.PLANNED.value == "planned"
        assert TaskStatus.RUNNING.value == "running"
        assert TaskStatus.HUMAN_GATE.value == "human_gate"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.FAILED.value == "failed"


class TestTaskStageEnum:
    """Test task stage enumeration."""

    def test_stage_values(self):
        """Test stage enum values."""
        assert TaskStage.CEO.value == "ceo"
        assert TaskStage.INFO.value == "info"
        assert TaskStage.DEV.value == "dev"
        assert TaskStage.REVIEW.value == "review"
        assert TaskStage.SUMMARY.value == "summary"
