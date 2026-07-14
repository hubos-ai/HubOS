# -*- coding: utf-8 -*-
"""Async-local delivery context for tools that need direct user sending."""

from __future__ import annotations

import asyncio
import time
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


SendPartsFunc = Callable[
    [str, list[Any], Optional[dict[str, Any]]],
    Awaitable[Any],
]


@dataclass
class DeliveryProgressState:
    """Shared progress state for the main run and nested tool tasks."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_sent_at: float = 0.0


@dataclass(frozen=True)
class DeliveryContext:
    """Current channel delivery target for tool-initiated sends."""

    channel: str
    to_handle: str
    meta: dict[str, Any]
    send_parts: SendPartsFunc
    task_tracker: Any | None = None
    run_key: str | None = None
    workspace_id: str | None = None
    session_id: str | None = None
    progress_state: DeliveryProgressState = field(
        default_factory=DeliveryProgressState,
    )

    async def send_progress_parts(
        self,
        parts: list[Any],
        *,
        min_interval_seconds: float = 0.0,
    ) -> bool:
        """Send a de-duplicated progress update.

        ``True`` also means a recent progress update already covers this
        interval, allowing generic and specialized reporters to coexist.
        """
        async with self.progress_state.lock:
            now = time.monotonic()
            if (
                self.progress_state.last_sent_at > 0
                and now - self.progress_state.last_sent_at
                < min_interval_seconds
            ):
                return True
            await self.send_parts(self.to_handle, parts, self.meta)
            self.progress_state.last_sent_at = time.monotonic()
            return True


_current_delivery_context: ContextVar[DeliveryContext | None] = ContextVar(
    "hubos_current_delivery_context",
    default=None,
)


def set_current_delivery_context(
    ctx: DeliveryContext | None,
) -> Token[DeliveryContext | None]:
    """Install the current delivery context for this async task."""
    return _current_delivery_context.set(ctx)


def reset_current_delivery_context(
    token: Token[DeliveryContext | None],
) -> None:
    """Restore the previous delivery context."""
    _current_delivery_context.reset(token)


def get_current_delivery_context() -> DeliveryContext | None:
    """Return the active delivery context, if any."""
    return _current_delivery_context.get()
