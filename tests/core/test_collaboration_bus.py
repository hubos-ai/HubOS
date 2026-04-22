"""Tests for collaboration bus."""

from uuid import uuid4

import pytest

from hubos.core.orchestrator.collaboration_bus import CollaborationBus
from hubos.core.schemas.collaboration import CollaborationMessage, MessageType


class TestCollaborationBus:
    """Tests for CollaborationBus."""

    def test_publish_and_receive_direct_message(self) -> None:
        """Test publishing a direct message to a specific unit."""
        bus = CollaborationBus()
        received_messages: list[CollaborationMessage] = []

        unit_a = uuid4()
        unit_b = uuid4()

        # Unit B subscribes to messages for itself
        bus.subscribe(lambda m: received_messages.append(m), unit_id=unit_b)

        # Unit A sends message to Unit B
        message = CollaborationMessage(
            from_unit_id=unit_a,
            to_unit_id=unit_b,
            task_id="task-123",
            trace_id="trace-456",
            session_id="session-789",
            message_type=MessageType.REQUEST,
            payload={"data": "hello from A"},
        )
        bus.publish(message)

        assert len(received_messages) == 1
        assert received_messages[0].from_unit_id == unit_a
        assert received_messages[0].to_unit_id == unit_b
        assert received_messages[0].payload["data"] == "hello from A"

    def test_publish_and_receive_broadcast(self) -> None:
        """Test broadcast message reaches all subscribers."""
        bus = CollaborationBus()
        received: list[CollaborationMessage] = []

        unit_a = uuid4()
        unit_b = uuid4()
        unit_c = uuid4()

        # All units subscribe to broadcasts
        for unit_id in [unit_a, unit_b, unit_c]:
            bus.subscribe(lambda m: received.append(m), unit_id=unit_id)

        # Unit A broadcasts
        message = CollaborationMessage(
            from_unit_id=unit_a,
            to_unit_id=None,
            task_id="task-123",
            trace_id="trace-456",
            session_id="session-789",
            message_type=MessageType.NOTIFICATION,
            payload={"event": "work_complete"},
            broadcast=True,
        )
        bus.publish(message)

        # All subscribers should receive broadcast
        assert len(received) == 3

    def test_message_type_filtering(self) -> None:
        """Test subscribing to specific message types."""
        bus = CollaborationBus()
        notifications: list[CollaborationMessage] = []

        unit_a = uuid4()

        # Subscribe to only NOTIFICATION messages
        bus.subscribe(
            lambda m: notifications.append(m),
            message_type=MessageType.NOTIFICATION,
        )

        # Publish different message types
        bus.publish(
            CollaborationMessage(
                from_unit_id=unit_a,
                task_id="task-1",
                trace_id="trace",
                session_id="session",
                message_type=MessageType.REQUEST,
                payload={},
            )
        )
        bus.publish(
            CollaborationMessage(
                from_unit_id=unit_a,
                task_id="task-2",
                trace_id="trace",
                session_id="session",
                message_type=MessageType.NOTIFICATION,
                payload={},
            )
        )

        # Only NOTIFICATION should be received
        assert len(notifications) == 1
        assert notifications[0].task_id == "task-2"

    def test_get_messages_with_filters(self) -> None:
        """Test retrieving messages with filters."""
        bus = CollaborationBus()

        unit_a = uuid4()
        unit_b = uuid4()

        # Publish messages
        for i in range(5):
            bus.publish(
                CollaborationMessage(
                    from_unit_id=unit_a,
                    to_unit_id=unit_b if i % 2 == 0 else None,
                    task_id=f"task-{i}",
                    trace_id=f"trace-{i}",
                    session_id="session-1" if i < 3 else "session-2",
                    message_type=MessageType.REQUEST if i % 2 == 0 else MessageType.NOTIFICATION,
                    payload={},
                )
            )

        # Filter by task_id
        task_filtered = bus.get_messages(task_id="task-1")
        assert len(task_filtered) == 1

        # Filter by session_id
        session_filtered = bus.get_messages(session_id="session-1")
        assert len(session_filtered) == 3

        # Filter by unit_id (sender or recipient)
        unit_filtered = bus.get_messages(unit_id=unit_b)
        assert len(unit_filtered) == 3  # messages where unit_b is sender or recipient

    def test_worker_to_worker_via_coordinator(self) -> None:
        """Test A->Coordinator->B collaboration chain."""
        bus = CollaborationBus()
        unit_b_received: list[CollaborationMessage] = []

        unit_a = uuid4()
        unit_b = uuid4()
        task_id = "task-collab-123"
        trace_id = "trace-collab-456"

        # Unit B subscribes
        bus.subscribe(lambda m: unit_b_received.append(m), unit_id=unit_b)

        # Unit A publishes request to Unit B
        request = CollaborationMessage(
            from_unit_id=unit_a,
            to_unit_id=unit_b,
            task_id=task_id,
            trace_id=trace_id,
            session_id="session",
            message_type=MessageType.REQUEST,
            payload={
                "type": "data_request",
                "what": "analysis_results",
            },
        )
        bus.publish(request)

        # Unit B receives the request
        assert len(unit_b_received) == 1
        assert unit_b_received[0].from_unit_id == unit_a
        assert unit_b_received[0].payload["what"] == "analysis_results"

        # Unit B responds to Unit A
        response_received: list[CollaborationMessage] = []
        bus.subscribe(lambda m: response_received.append(m), unit_id=unit_a)

        response = CollaborationMessage(
            from_unit_id=unit_b,
            to_unit_id=unit_a,
            task_id=task_id,
            trace_id=trace_id,
            session_id="session",
            message_type=MessageType.RESPONSE,
            payload={
                "type": "data_response",
                "results": {"key": "value"},
            },
        )
        bus.publish(response)

        # Unit A receives the response
        assert len(response_received) == 1
        assert response_received[0].from_unit_id == unit_b
        assert response_received[0].payload["results"] == {"key": "value"}

    def test_collaboration_message_to_dict(self) -> None:
        """Test message serialization."""
        msg_id = uuid4()
        from_id = uuid4()
        to_id = uuid4()

        message = CollaborationMessage(
            message_id=msg_id,
            from_unit_id=from_id,
            to_unit_id=to_id,
            task_id="task-123",
            trace_id="trace-456",
            session_id="session-789",
            message_type=MessageType.DATA_SHARE,
            payload={"shared": "data"},
            broadcast=False,
        )

        data = message.to_dict()

        assert data["message_id"] == str(msg_id)
        assert data["from_unit_id"] == str(from_id)
        assert data["to_unit_id"] == str(to_id)
        assert data["message_type"] == "data_share"
        assert data["payload"] == {"shared": "data"}

    def test_clear_messages(self) -> None:
        """Test clearing all messages."""
        bus = CollaborationBus()

        for i in range(3):
            bus.publish(
                CollaborationMessage(
                    task_id=f"task-{i}",
                    trace_id="trace",
                    session_id="session",
                    message_type=MessageType.NOTIFICATION,
                    payload={},
                )
            )

        assert len(bus.get_messages()) == 3

        bus.clear()

        assert len(bus.get_messages()) == 0


class TestCollaborationBusLogging:
    """Tests for collaboration bus observability."""

    def test_trace_id_in_message(self) -> None:
        """Test that trace_id is preserved in messages."""
        bus = CollaborationBus()
        received: list[CollaborationMessage] = []

        unit_a = uuid4()
        unit_b = uuid4()
        trace_id = "unique-trace-123"

        bus.subscribe(lambda m: received.append(m), unit_id=unit_b)

        bus.publish(
            CollaborationMessage(
                from_unit_id=unit_a,
                to_unit_id=unit_b,
                task_id="task-123",
                trace_id=trace_id,
                session_id="session",
                message_type=MessageType.REQUEST,
                payload={},
            )
        )

        assert received[0].trace_id == trace_id

    def test_full_path_traceable(self) -> None:
        """Test that collaboration path is fully traceable via trace_id."""
        bus = CollaborationBus()
        all_messages: list[CollaborationMessage] = []

        unit_a = uuid4()
        unit_b = uuid4()
        unit_c = uuid4()
        trace_id = "collaboration-trace-789"

        # All units subscribe
        for uid in [unit_a, unit_b, unit_c]:
            bus.subscribe(lambda m: all_messages.append(m), unit_id=uid)

        # A -> B: data request
        bus.publish(
            CollaborationMessage(
                from_unit_id=unit_a,
                to_unit_id=unit_b,
                task_id="task-123",
                trace_id=trace_id,
                session_id="session",
                message_type=MessageType.REQUEST,
                payload={"step": 1, "type": "request"},
            )
        )

        # B -> C: forwarded request
        bus.publish(
            CollaborationMessage(
                from_unit_id=unit_b,
                to_unit_id=unit_c,
                task_id="task-123",
                trace_id=trace_id,
                session_id="session",
                message_type=MessageType.REQUEST,
                payload={"step": 2, "type": "forwarded"},
            )
        )

        # C -> A: response
        bus.publish(
            CollaborationMessage(
                from_unit_id=unit_c,
                to_unit_id=unit_a,
                task_id="task-123",
                trace_id=trace_id,
                session_id="session",
                message_type=MessageType.RESPONSE,
                payload={"step": 3, "type": "response"},
            )
        )

        # All messages share same trace_id for full path tracking
        assert all(m.trace_id == trace_id for m in all_messages)
        assert len(all_messages) == 3
