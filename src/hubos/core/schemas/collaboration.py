# -*- coding: utf-8 -*-
"""Collaboration message schemas for worker-to-worker communication."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


SCHEMA_VERSION = "1.0.0"


class MessageType(str, Enum):
    """Types of collaboration messages."""

    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    DATA_SHARE = "data_share"
    CONTROL = "control"


class CollaborationMessage:
    """
    Structured message for worker-to-worker collaboration via coordinator.

    Per ARCHITECTURE.md:
    - Workers never message each other directly.
    - Horizontal communication goes through coordinator message bus.
    """

    def __init__(
        self,
        message_id: Optional[UUID] = None,
        from_unit_id: Optional[UUID] = None,
        to_unit_id: Optional[UUID] = None,
        task_id: str = "",
        trace_id: str = "",
        session_id: str = "",
        message_type: MessageType = MessageType.REQUEST,
        payload: dict[str, Any] = field(default_factory=dict),
        broadcast: bool = False,
        timestamp: Optional[datetime] = None,
    ) -> None:
        self.message_id = message_id or uuid4()
        self.from_unit_id = from_unit_id
        self.to_unit_id = to_unit_id
        self.task_id = task_id
        self.trace_id = trace_id
        self.session_id = session_id
        self.message_type = message_type
        self.payload = payload
        self.broadcast = broadcast
        self.timestamp = timestamp or _utcnow()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message_id": str(self.message_id),
            "from_unit_id": str(self.from_unit_id)
            if self.from_unit_id
            else None,
            "to_unit_id": str(self.to_unit_id) if self.to_unit_id else None,
            "task_id": self.task_id,
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "message_type": self.message_type.value,
            "payload": self.payload,
            "broadcast": self.broadcast,
            "timestamp": self.timestamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CollaborationMessage":
        """Create from dictionary."""
        return cls(
            message_id=UUID(data["message_id"])
            if data.get("message_id")
            else None,
            from_unit_id=UUID(data["from_unit_id"])
            if data.get("from_unit_id")
            else None,
            to_unit_id=UUID(data["to_unit_id"])
            if data.get("to_unit_id")
            else None,
            task_id=data.get("task_id", ""),
            trace_id=data.get("trace_id", ""),
            session_id=data.get("session_id", ""),
            message_type=MessageType(data["message_type"])
            if data.get("message_type")
            else MessageType.REQUEST,
            payload=data.get("payload", {}),
            broadcast=data.get("broadcast", False),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if data.get("timestamp")
            else None,
        )
