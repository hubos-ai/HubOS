# -*- coding: utf-8 -*-
"""Collaboration bus for worker-to-worker communication via coordinator."""

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from hubos.core.schemas.collaboration import CollaborationMessage, MessageType

logger = logging.getLogger(__name__)


class CollaborationBus:
    """
    In-memory collaboration bus for worker-to-worker messaging.

    Per ARCHITECTURE.md:
    - Workers never message each other directly.
    - Horizontal communication goes through coordinator message bus.
    - Coordinator decides tie-breakers on conflicting outputs.
    """

    def __init__(self) -> None:
        """Initialize the collaboration bus."""
        self._messages: list[CollaborationMessage] = []
        self._subscriptions: dict[
            str,
            list[Callable[[CollaborationMessage], None]],
        ] = {}

    def publish(
        self,
        message: CollaborationMessage,
    ) -> None:
        """
        Publish a collaboration message.

        Args:
            message: The collaboration message to publish.
        """
        self._messages.append(message)

        logger.info(
            "Collaboration message published",
            extra={
                "trace_id": message.trace_id,
                "session_id": message.session_id,
                "task_id": message.task_id,
                "message_id": str(message.message_id),
                "from_unit_id": str(message.from_unit_id)
                if message.from_unit_id
                else None,
                "to_unit_id": str(message.to_unit_id)
                if message.to_unit_id
                else None,
                "broadcast": message.broadcast,
                "message_type": message.message_type.value,
            },
        )

        # Notify subscribers
        self._notify_subscribers(message)

    def subscribe(
        self,
        callback: Callable[[CollaborationMessage], None],
        unit_id: Optional[UUID] = None,
        message_type: Optional[MessageType] = None,
    ) -> None:
        """
        Subscribe to collaboration messages.

        Args:
            callback: Function to call when matching message arrives.
            unit_id: Optional unit ID to filter for (to receive direct messages).
            message_type: Optional message type to filter for.
        """
        key = f"{unit_id or 'all'}:{message_type.value if message_type else 'all'}"
        if key not in self._subscriptions:
            self._subscriptions[key] = []
        self._subscriptions[key].append(callback)

    def _notify_subscribers(self, message: CollaborationMessage) -> None:
        """Notify all matching subscribers."""
        # Direct message to specific unit
        if message.to_unit_id:
            # Notify specific unit (unit_id:all)
            key = f"{message.to_unit_id}:all"
            if key in self._subscriptions:
                for callback in self._subscriptions[key]:
                    try:
                        callback(message)
                    except Exception as e:
                        logger.error(
                            "Subscriber callback failed",
                            extra={
                                "trace_id": message.trace_id,
                                "error": str(e),
                            },
                        )

        # Broadcast messages - notify ALL unit-specific subscribers
        if message.broadcast:
            for key, callbacks in self._subscriptions.items():
                # Extract unit_id from key (format: "unit_id:message_type" or "all:message_type")
                parts = key.split(":")
                if len(parts) >= 1:
                    key_unit_id = parts[0]
                    # Broadcast goes to "all" unit subscriptions and specific unit subscriptions
                    # (but not message-type-only subscriptions like "all:notification")
                    if key_unit_id == "all" or key_unit_id.startswith("-"):
                        continue  # Skip message-type-only subscriptions
                    for callback in callbacks:
                        try:
                            callback(message)
                        except Exception as e:
                            logger.error(
                                "Broadcast subscriber callback failed",
                                extra={
                                    "trace_id": message.trace_id,
                                    "error": str(e),
                                },
                            )

        # Message type specific - notify all subscribers of this message type
        key = f"all:{message.message_type.value}"
        if key in self._subscriptions:
            for callback in self._subscriptions[key]:
                try:
                    callback(message)
                except Exception as e:
                    logger.error(
                        "Message type subscriber callback failed",
                        extra={
                            "trace_id": message.trace_id,
                            "error": str(e),
                        },
                    )

        # Also notify unit_id + message_type combo
        if message.to_unit_id:
            key = f"{message.to_unit_id}:{message.message_type.value}"
            if key in self._subscriptions:
                for callback in self._subscriptions[key]:
                    try:
                        callback(message)
                    except Exception as e:
                        logger.error(
                            "Unit message type subscriber callback failed",
                            extra={
                                "trace_id": message.trace_id,
                                "error": str(e),
                            },
                        )

    def get_messages(
        self,
        task_id: Optional[str] = None,
        unit_id: Optional[UUID] = None,
        message_type: Optional[MessageType] = None,
        session_id: Optional[str] = None,
    ) -> list[CollaborationMessage]:
        """
        Get messages matching filters.

        Args:
            task_id: Optional task ID filter.
            unit_id: Optional unit ID filter (as sender or recipient).
            message_type: Optional message type filter.
            session_id: Optional session ID filter.

        Returns:
            List of matching messages.
        """
        results = self._messages

        if task_id:
            results = [m for m in results if m.task_id == task_id]

        if session_id:
            results = [m for m in results if m.session_id == session_id]

        if unit_id:
            results = [
                m
                for m in results
                if m.from_unit_id == unit_id
                or m.to_unit_id == unit_id
                or m.broadcast
            ]

        if message_type:
            results = [m for m in results if m.message_type == message_type]

        return results

    def clear(self) -> None:
        """Clear all messages (for testing)."""
        self._messages.clear()
        self._subscriptions.clear()
