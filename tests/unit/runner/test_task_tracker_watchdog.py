# -*- coding: utf-8 -*-
"""Tests for TaskTracker watchdog timeout and stale task cleanup."""
from __future__ import annotations

import asyncio
import time

import pytest

from hubos.app.runner.task_tracker import TaskTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _hung_producer(payload):
    """A producer that never yields — simulates a stuck LLM call."""
    await asyncio.sleep(9999)
    yield "unreachable"


async def _fast_producer(payload):
    """A producer that yields one event and finishes quickly."""
    yield '{"type": "text", "content": "hello"}'


async def _slow_producer(payload, delay: float = 0.05):
    """A producer that yields after a short delay."""
    await asyncio.sleep(delay)
    yield '{"type": "text", "content": "done"}'


async def _heartbeat_producer(payload, interval: float = 0.05, count: int = 6):
    """A producer that stays active for a while by yielding regularly."""
    for i in range(count):
        await asyncio.sleep(interval)
        yield f'{{"type": "text", "content": "tick-{i}"}}'


async def _drain_queue(queue: asyncio.Queue) -> list:
    """Drain all items from a queue until sentinel (None)."""
    items = []
    while True:
        item = await asyncio.wait_for(queue.get(), timeout=3.0)
        if item is None:
            break
        items.append(item)
    return items


async def _wait_until_idle(tracker: TaskTracker, run_key: str, timeout: float = 2.0) -> None:
    """Poll until a run_key becomes idle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await tracker.get_status(run_key) == "idle":
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"run_key={run_key} did not become idle within {timeout}s")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_watchdog_cancels_hung_task():
    """A hung producer should be cancelled by the watchdog."""
    tracker = TaskTracker()
    tracker._MAX_RUN_SECONDS = 0.2

    queue, is_new = await tracker.attach_or_start(
        "test-run", "payload", _hung_producer
    )
    assert is_new is True

    # Wait for the watchdog to fire
    await _wait_until_idle(tracker, "test-run", timeout=1.0)
    assert await tracker.get_status("test-run") == "idle"

    # Queue should eventually get a sentinel (may have "started" event first)
    items = await _drain_queue(queue)
    # At least the sentinel ends the stream
    assert items is not None  # drained successfully


@pytest.mark.asyncio
async def test_fast_task_cancels_watchdog():
    """A fast producer should cancel its watchdog; watchdog should not fire."""
    tracker = TaskTracker()
    tracker._MAX_RUN_SECONDS = 0.1

    queue, is_new = await tracker.attach_or_start(
        "test-run", "payload", _fast_producer
    )
    assert is_new is True

    # Drain: "started" event + user event + sentinel
    items = await _drain_queue(queue)
    assert len(items) == 2  # "started" + user event

    assert await tracker.get_status("test-run") == "idle"


@pytest.mark.asyncio
async def test_cancel_stale_tasks():
    """cancel_stale_tasks should cancel runs older than threshold."""
    tracker = TaskTracker()

    await tracker.attach_or_start("stale-1", "payload", _hung_producer)
    await tracker.attach_or_start("stale-2", "payload", _hung_producer)

    # Backdate started_at to simulate age
    now = time.monotonic()
    async with tracker._lock:
        for s in tracker._runs.values():
            s.started_at = now - 700

    cancelled = await tracker.cancel_stale_tasks(max_age_seconds=600.0)
    assert len(cancelled) == 2

    # Wait for tasks to actually finish cancelling
    await _wait_until_idle(tracker, "stale-1", timeout=2.0)
    await _wait_until_idle(tracker, "stale-2", timeout=2.0)


@pytest.mark.asyncio
async def test_get_active_task_info():
    """get_active_task_info should return info about running tasks."""
    tracker = TaskTracker()

    await tracker.attach_or_start(
        "active-1", "payload",
        lambda p: _slow_producer(p, delay=0.5)
    )

    info = await tracker.get_active_task_info()
    assert len(info) == 1
    assert info[0]["run_key"] == "active-1"
    assert info[0]["done"] is False
    assert info[0]["age_seconds"] >= 0

    await asyncio.sleep(1.0)
    info = await tracker.get_active_task_info()
    assert len(info) == 0


@pytest.mark.asyncio
async def test_reconnect_gets_sentinel_on_watchdog():
    """Second subscriber (reconnect) should also get sentinel when watchdog fires."""
    tracker = TaskTracker()
    tracker._MAX_RUN_SECONDS = 0.3

    queue1, is_new = await tracker.attach_or_start(
        "chat-1", "msg1", _hung_producer
    )
    assert is_new is True

    queue2, is_new = await tracker.attach_or_start(
        "chat-1", "msg2", _hung_producer
    )
    assert is_new is False

    # Both queues should drain successfully (events + sentinel)
    items1 = await _drain_queue(queue1)
    items2 = await _drain_queue(queue2)
    # Each should have at least the "started" event
    assert len(items1) >= 1
    assert len(items2) >= 1

    assert await tracker.get_status("chat-1") == "idle"


@pytest.mark.asyncio
async def test_watchdog_does_not_cancel_active_stream():
    """A stream with regular output should not be cancelled for age alone."""
    tracker = TaskTracker()
    tracker._MAX_RUN_SECONDS = 0.2

    queue, is_new = await tracker.attach_or_start(
        "active-stream", "payload", _heartbeat_producer
    )
    assert is_new is True

    items = await _drain_queue(queue)
    # started event + heartbeat events
    assert len(items) == 7
    assert await tracker.get_status("active-stream") == "idle"


@pytest.mark.asyncio
async def test_touch_keeps_silent_active_run_alive():
    """A silent long tool can keep its parent chat run alive with touch()."""
    tracker = TaskTracker()
    tracker._MAX_RUN_SECONDS = 0.15

    queue, is_new = await tracker.attach_or_start(
        "silent-active",
        "payload",
        _hung_producer,
    )
    assert is_new is True

    # Keep refreshing activity longer than the watchdog threshold.  This
    # simulates spawn_subagents waiting for child agents without emitting SSE.
    for _ in range(5):
        await asyncio.sleep(0.06)
        assert await tracker.touch("silent-active", reason="unit-test")

    assert await tracker.get_status("silent-active") == "running"

    await tracker.request_stop("silent-active")
    await _wait_until_idle(tracker, "silent-active", timeout=1.0)
    await _drain_queue(queue)
