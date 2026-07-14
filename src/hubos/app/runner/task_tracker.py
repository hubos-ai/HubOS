# -*- coding: utf-8 -*-
"""Task tracker for background runs: streaming, reconnect, multi-subscriber.

run_key is ChatSpec.id (chat_id). Per run: task, queues, event buffer.
Reconnects get buffer replay + new events. Cleanup when task completes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
import weakref
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Coroutine

logger = logging.getLogger(__name__)

_SENTINEL = None


@dataclass
class _RunState:
    """Per-run state (task, queues, buffer), guarded by tracker lock."""

    task: asyncio.Future
    queues: list[asyncio.Queue] = field(default_factory=list)
    buffer: list[str] = field(default_factory=list)
    stream_id: str = ""
    watchdog: asyncio.Task | None = None
    started_at: float = 0.0
    last_activity_at: float = 0.0


class TaskTracker:
    """Per-workspace tracker: run_key -> RunState.

    All mutations to _runs under _lock. Producer broadcasts under lock.
    Subscribers use unbounded per-connection queues; disconnect removes them
    via :meth:`detach_subscriber`.
    """

    # Maximum idle time for a single run before forced cancellation.
    # Active long-running streams should not be killed just because they
    # have been running for a long time.
    _MAX_RUN_SECONDS: float = 600.0  # 10 minutes of inactivity

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._runs: dict[str, _RunState] = {}

    @property
    def lock(self) -> asyncio.Lock:
        return self._lock

    async def get_status(self, run_key: str) -> str:
        """Return ``'idle'`` or ``'running'``."""
        async with self._lock:
            state = self._runs.get(run_key)
        if state is None or state.task.done():
            return "idle"
        return "running"

    async def has_active_tasks(self) -> bool:
        """Check if any tasks are currently running.

        Returns:
            bool: True if any tasks are active, False otherwise
        """
        async with self._lock:
            for state in self._runs.values():
                if not state.task.done():
                    return True
            return False

    async def list_active_tasks(self) -> list[str]:
        """List all currently running task keys.

        Returns:
            list[str]: List of active run_keys
        """
        async with self._lock:
            return [
                run_key
                for run_key, state in self._runs.items()
                if not state.task.done()
            ]

    async def wait_all_done(self, timeout: float = 300.0) -> bool:
        """Wait for all active tasks to complete.

        Args:
            timeout: Maximum time to wait in seconds (default: 300s = 5min)

        Returns:
            bool: True if all tasks completed, False if timeout occurred
        """

        async def _wait_loop() -> None:
            while await self.has_active_tasks():
                await asyncio.sleep(0.5)

        try:
            await asyncio.wait_for(_wait_loop(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    async def attach(self, run_key: str) -> asyncio.Queue | None:
        """Attach to an existing run.

        Returns a new queue pre-filled with the event buffer, or ``None``
        if no run is active for *run_key*.
        """
        async with self._lock:
            state = self._runs.get(run_key)
            if state is None or state.task.done():
                return None
            q: asyncio.Queue = asyncio.Queue()
            for sse in state.buffer:
                q.put_nowait(sse)
            state.queues.append(q)
            return q

    async def detach_subscriber(
        self,
        run_key: str,
        queue: asyncio.Queue,
    ) -> None:
        """Remove *queue* from *run_key*'s subscriber list.

        Idempotent if the run ended or *queue* was already removed.
        """
        async with self._lock:
            state = self._runs.get(run_key)
            if state is None:
                return
            try:
                state.queues.remove(queue)
            except ValueError:
                pass

    async def request_stop(self, run_key: str) -> bool:
        """Cancel the run. Returns ``True`` if it was running."""
        async with self._lock:
            state = self._runs.get(run_key)
            if state is None or state.task.done():
                return False
            state.task.cancel()
            return True

    async def touch(self, run_key: str, reason: str = "") -> bool:
        """Mark an active run as alive without emitting a user-visible event.

        Long-running tools may be doing useful work while the outer chat stream
        is silent.  Refreshing ``last_activity_at`` lets the watchdog keep
        protecting truly stuck runs without cancelling active delegated work.
        """
        async with self._lock:
            state = self._runs.get(run_key)
            if state is None or state.task.done():
                return False
            state.last_activity_at = time.monotonic()
        if reason:
            logger.debug(
                "TaskTracker touch: run_key=%s reason=%s",
                run_key,
                reason,
            )
        return True

    async def attach_or_start(
        self,
        run_key: str,
        payload: Any,
        stream_fn: Callable[..., Coroutine],
        *,
        force_new: bool = False,
        reconnect: bool = False,
    ) -> tuple[asyncio.Queue, bool]:
        """Attach to an existing run or start a new one.

        Returns ``(queue, is_new_run)``.

        When *force_new* is ``True`` and an active run exists, the old run
        is cancelled, its subscribers are sent the sentinel, and a brand-new
        run is created without replaying the old buffer.  This is used for
        runtime-guidance restarts where the caller wants a clean stream.

        When *reconnect* is ``True`` and an active run exists (without
        *force_new*), the subscriber receives a replay of the buffered events.
        This is only appropriate for SSE reconnects, not for new user messages
        or guidance submits.
        """
        async with self._lock:
            state = self._runs.get(run_key)
            if state is not None and not state.task.done():
                if force_new:
                    # Guidance restart: cancel old producer, purge old state,
                    # then fall through to create a fresh run (no buffer replay).
                    state.task.cancel()
                    for subscriber_queue in state.queues:
                        subscriber_queue.put_nowait(_SENTINEL)
                    self._runs.pop(run_key, None)
                else:
                    # Attach to existing run.
                    new_queue: asyncio.Queue = asyncio.Queue()
                    if reconnect:
                        # Reconnect: replay buffer so subscriber catches up.
                        for sse in state.buffer:
                            new_queue.put_nowait(sse)
                    # Non-reconnect (new submit / session-switch): no replay.
                    state.queues.append(new_queue)
                    return new_queue, False

            my_queue: asyncio.Queue = asyncio.Queue()
            run = _RunState(
                task=asyncio.Future(),  # placeholder, replaced below
                queues=[my_queue],
                buffer=[],
            )
            self._runs[run_key] = run

            tracker_ref = weakref.ref(self)

            async def _producer() -> None:
                try:
                    # Emit a stream-start marker so the frontend can
                    # distinguish this run from a previous one and reset
                    # its parser state.
                    stream_id = uuid.uuid4().hex[:12]
                    run.stream_id = stream_id
                    start_evt = (
                        "data: "
                        + json.dumps(
                            {
                                "_hubos_stream_id": stream_id,
                                "status": "started",
                            },
                        )
                        + "\n\n"
                    )
                    tracker = tracker_ref()
                    if tracker is not None:
                        async with tracker.lock:
                            run.buffer.append(start_evt)
                            run.last_activity_at = time.monotonic()
                            for q in run.queues:
                                q.put_nowait(start_evt)

                    async for sse in stream_fn(payload):
                        tracker = tracker_ref()
                        if tracker is None:
                            return
                        async with tracker.lock:
                            run.buffer.append(sse)
                            run.last_activity_at = time.monotonic()
                            # Cap buffer to bound memory for long streams.
                            if len(run.buffer) > 200:
                                run.buffer = run.buffer[-200:]
                            for q in run.queues:
                                q.put_nowait(sse)
                except asyncio.CancelledError:
                    logger.debug("run cancelled run_key=%s", run_key)
                except Exception:
                    logger.exception("run error run_key=%s", run_key)
                    err_sse = (
                        "data: "
                        f"{json.dumps({'error': 'internal server error'})}\n\n"
                    )
                    tracker = tracker_ref()
                    if tracker is not None:
                        async with tracker.lock:
                            run.buffer.append(err_sse)
                            for q in run.queues:
                                q.put_nowait(err_sse)
                finally:
                    tracker = tracker_ref()
                    if tracker is not None:
                        async with tracker.lock:
                            for q in run.queues:
                                q.put_nowait(_SENTINEL)
                            # Only pop if this run is still the active entry.
                            # A force_new replacement may have already replaced
                            # the entry — popping unconditionally would delete
                            # the new run.
                            # pylint: disable=protected-access
                            if tracker._runs.get(run_key) is run:
                                tracker._runs.pop(
                                    run_key,
                                    None,
                                )
                        # Cancel the watchdog since producer finished normally
                        if run.watchdog and not run.watchdog.done():
                            run.watchdog.cancel()

            run.task = asyncio.create_task(_producer())
            run.started_at = time.monotonic()
            run.last_activity_at = run.started_at

            # ── Watchdog: cancel the producer if it goes idle too long ──
            async def _watchdog() -> None:
                try:
                    while True:
                        await asyncio.sleep(min(5.0, self._MAX_RUN_SECONDS))
                        if run.task.done():
                            return
                        idle_for = time.monotonic() - max(
                            run.last_activity_at,
                            run.started_at,
                        )
                        if idle_for <= self._MAX_RUN_SECONDS:
                            continue
                        logger.warning(
                            "TaskTracker watchdog: run_key=%s idle for %.0fs "
                            "(threshold %.0fs), cancelling",
                            run_key,
                            idle_for,
                            self._MAX_RUN_SECONDS,
                        )
                        run.task.cancel()
                        return
                except asyncio.CancelledError:
                    return  # producer finished normally

            run.watchdog = asyncio.create_task(_watchdog())

            return my_queue, True

    async def stream_from_queue(
        self,
        queue: asyncio.Queue,
        run_key: str,
    ) -> AsyncGenerator[str, None]:
        """Yield SSE strings from *queue* until the sentinel ``None``.

        Always detaches *queue* from *run_key* when this stream ends or is
        closed (including client disconnect), so reconnects do not leak queues.
        """
        try:
            while True:
                try:
                    event = await queue.get()
                    if event is _SENTINEL:
                        break
                    yield event
                except asyncio.CancelledError:
                    break
        finally:
            await self.detach_subscriber(run_key, queue)

    # ── Stale task management ──────────────────────────────────────

    async def cancel_stale_tasks(
        self,
        max_age_seconds: float | None = None,
    ) -> list[str]:
        """Cancel runs that have been running longer than *max_age_seconds*.

        Returns list of cancelled run_keys.
        """
        threshold = max_age_seconds or self._MAX_RUN_SECONDS
        now = time.monotonic()
        cancelled: list[str] = []

        async with self._lock:
            stale_keys = [
                k
                for k, s in self._runs.items()
                if not s.task.done()
                and s.started_at > 0
                and (now - s.started_at) > threshold
            ]

        for key in stale_keys:
            logger.warning(
                "TaskTracker.cancel_stale_tasks: cancelling stale run_key=%s "
                "(age=%.0fs > threshold=%.0fs)",
                key,
                now - self._runs.get(key, _RunState(task=None)).started_at,  # type: ignore[union-attr]
                threshold,
            )
            cancelled.append(key)
            await self.request_stop(key)

        return cancelled

    async def get_active_task_info(self) -> list[dict[str, Any]]:
        """Return info about all active (non-done) tasks for diagnostics."""
        now = time.monotonic()
        async with self._lock:
            return [
                {
                    "run_key": k,
                    "done": s.task.done(),
                    "age_seconds": round(now - s.started_at, 1)
                    if s.started_at
                    else -1,
                    "stream_id": s.stream_id,
                    "queue_count": len(s.queues),
                }
                for k, s in self._runs.items()
                if not s.task.done()
            ]
