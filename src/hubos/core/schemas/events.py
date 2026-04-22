"""Conversation event schemas for ingress."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


SCHEMA_VERSION = "1.0.0"


class EventSource(str, Enum):
    """Supported ingress channels."""

    TELEGRAM = "telegram"
    WEBHOOK = "webhook"
    WEB_UI = "web_ui"
    API = "api"


@dataclass(frozen=True)
class SourceMetadata:
    """Metadata about the event source."""

    channel: Optional[EventSource] = None
    channel_message_id: Optional[str] = None
    user_id: Optional[str] = None
    chat_id: Optional[str] = None
    raw_payload: Optional[dict] = None


@dataclass(frozen=True)
class ConversationEvent:
    """
    Canonical input event from any channel.

    This is the Input Contract as defined in ARCHITECTURE.md.
    All channels produce ConversationEvent with source metadata,
    identity, payload, and trace ID.
    """

    schema_version: str = field(default=SCHEMA_VERSION)
    event_id: UUID = field(default_factory=uuid4)
    trace_id: str = ""
    session_id: str = ""
    task_id: Optional[str] = None
    source: SourceMetadata = field(default_factory=SourceMetadata)
    payload: dict = field(default_factory=dict)
    timestamp: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.trace_id:
            raise ValueError("trace_id is required")
        if not self.session_id:
            raise ValueError("session_id is required")
