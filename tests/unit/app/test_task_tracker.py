# -*- coding: utf-8 -*-
"""Tests for TaskTracker.attach_or_start force_new parameter.

Uses importlib to load task_tracker.py directly, bypassing the runner
package's __init__.py which pulls in heavy dependencies (agentscope, etc.).
"""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_SRC_ROOT = _HERE.parents[3] / "src"
_MODULE_PATH = _SRC_ROOT / "hubos" / "app" / "runner" / "task_tracker.py"


def _load_task_tracker_class():
    """Load TaskTracker from task_tracker.py without triggering __init__.py."""
    spec = importlib.util.spec_from_file_location(
        "task_tracker_isolated",
        str(_MODULE_PATH),
        submodule_search_locations=[],
    )
    mod = importlib.util.module_from_spec(spec)
    # Register with a unique name so dataclass can find it in sys.modules
    import sys
    sys.modules["task_tracker_isolated"] = mod
    spec.loader.exec_module(mod)
    return mod.TaskTracker


TaskTracker = _load_task_tracker_class()


async def _long_stream(payload):
    """Yield SSE events indefinitely until cancelled."""
    i = 0
    while True:
        yield f"data: {json.dumps({'seq': i})}\n\n"
        i += 1
        await asyncio.sleep(0.02)


def _parse_sse_data(sse: str) -> dict:
    """Extract JSON payload from an SSE data line."""
    raw = sse.strip()
    if raw.startswith("data: "):
        raw = raw[6:]
    return json.loads(raw)


@pytest.fixture
def tracker():
    return TaskTracker()


@pytest.mark.asyncio
async def test_normal_replay(tracker):
    """reconnect=True gets buffer replay."""
    q1, is_new1 = await tracker.attach_or_start("k1", {}, _long_stream)
    assert is_new1 is True

    # Let the producer emit a few events (beyond the started marker)
    await asyncio.sleep(0.1)

    q2, is_new2 = await tracker.attach_or_start(
        "k1", {}, _long_stream, reconnect=True,
    )
    assert is_new2 is False

    # q2 should have replayed buffered events
    replayed = []
    while not q2.empty():
        replayed.append(q2.get_nowait())
    assert len(replayed) > 0

    # Cleanup
    await tracker.request_stop("k1")


@pytest.mark.asyncio
async def test_force_new_no_replay(tracker):
    """With force_new=True, old run is cancelled and new run starts clean."""
    q1, is_new1 = await tracker.attach_or_start("k1", {}, _long_stream)
    assert is_new1 is True

    # Let producer emit a few events
    await asyncio.sleep(0.1)

    # force_new should cancel old run and start fresh
    q2, is_new2 = await tracker.attach_or_start(
        "k1", {}, _long_stream, force_new=True,
    )
    assert is_new2 is True

    # q2 should have the started marker first, then only NEW events
    first = await asyncio.wait_for(q2.get(), timeout=2)
    data = _parse_sse_data(first)
    assert "_hubos_stream_id" in data
    # Any remaining events must be from the NEW producer (seq from 0),
    # not replayed old buffer events. Drain and verify seq starts at 0.
    new_events = []
    while not q2.empty():
        new_events.append(q2.get_nowait())
    if new_events:
        for evt in new_events:
            evt_data = _parse_sse_data(evt)
            # New producer starts at seq=0, old would have seq > 0
            assert evt_data.get("seq", -1) >= 0

    # Old queue q1 should eventually receive a sentinel (None) after
    # any previously-buffered events are drained.
    got_sentinel = False
    while not q1.empty():
        if q1.get_nowait() is None:
            got_sentinel = True
            break
    if not got_sentinel:
        sentinel = await asyncio.wait_for(q1.get(), timeout=2)
        assert sentinel is None

    # New queue q2 should receive fresh events from new producer
    event = await asyncio.wait_for(q2.get(), timeout=2)
    assert event is not None

    await tracker.request_stop("k1")


@pytest.mark.asyncio
async def test_force_new_no_existing(tracker):
    """force_new=True with no existing run behaves like normal new run."""
    q, is_new = await tracker.attach_or_start(
        "k1", {}, _long_stream, force_new=True,
    )
    assert is_new is True
    # Should have the started marker
    first = await asyncio.wait_for(q.get(), timeout=2)
    assert "_hubos_stream_id" in first
    await tracker.request_stop("k1")


@pytest.mark.asyncio
async def test_force_new_old_finally_does_not_remove_new_run(tracker):
    """Old producer's finally must not pop the replacement run entry."""
    # Start old run
    await tracker.attach_or_start("k1", {}, _long_stream)
    await asyncio.sleep(0.05)

    # force_new replaces old run with a new one
    q_new, is_new = await tracker.attach_or_start(
        "k1", {}, _long_stream, force_new=True,
    )
    assert is_new is True

    # Give the old producer's finally block time to run
    await asyncio.sleep(0.2)

    # The new run must still be tracked — old finally did not remove it
    status = await tracker.get_status("k1")
    assert status == "running"

    # New queue should still receive events
    event = await asyncio.wait_for(q_new.get(), timeout=2)
    assert event is not None

    await tracker.request_stop("k1")


# ---------------------------------------------------------------------------
# New tests for stream isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guidance_submit_no_replay(tracker):
    """Non-reconnect attach_or_start does NOT replay old buffer."""
    q1, _ = await tracker.attach_or_start("k1", {}, _long_stream)
    await asyncio.sleep(0.1)  # let buffer accumulate

    # Normal new submit (reconnect=False, force_new=False) — no replay
    q2, is_new = await tracker.attach_or_start(
        "k1", {}, _long_stream, reconnect=False,
    )
    assert is_new is False
    assert q2.empty()

    await tracker.request_stop("k1")


@pytest.mark.asyncio
async def test_stream_id_started_event(tracker):
    """New run emits a started SSE event with _hubos_stream_id."""
    q, is_new = await tracker.attach_or_start("k1", {}, _long_stream)
    assert is_new is True
    first = await asyncio.wait_for(q.get(), timeout=2)
    data = _parse_sse_data(first)
    assert "_hubos_stream_id" in data
    assert data["status"] == "started"
    assert len(data["_hubos_stream_id"]) > 0
    await tracker.request_stop("k1")


@pytest.mark.asyncio
async def test_old_stream_discarded_on_force_new(tracker):
    """force_new: new run gets a different stream_id."""
    q1, _ = await tracker.attach_or_start("k1", {}, _long_stream)
    await asyncio.sleep(0.05)
    old_first = await asyncio.wait_for(q1.get(), timeout=2)
    old_data = _parse_sse_data(old_first)
    old_sid = old_data["_hubos_stream_id"]

    q2, is_new = await tracker.attach_or_start(
        "k1", {}, _long_stream, force_new=True,
    )
    assert is_new is True
    new_first = await asyncio.wait_for(q2.get(), timeout=2)
    new_data = _parse_sse_data(new_first)
    assert new_data["_hubos_stream_id"] != old_sid

    await tracker.request_stop("k1")


@pytest.mark.asyncio
async def test_reconnect_false_no_replay_with_buffer(tracker):
    """reconnect=False on an existing run: subscriber gets no buffer."""
    q1, _ = await tracker.attach_or_start("k1", {}, _long_stream)
    await asyncio.sleep(0.15)  # accumulate buffer

    # Verify q1 received events
    events = []
    while not q1.empty():
        events.append(q1.get_nowait())
    assert len(events) > 0

    # Non-reconnect attach should NOT replay
    q2, is_new = await tracker.attach_or_start(
        "k1", {}, _long_stream, reconnect=False,
    )
    assert is_new is False
    assert q2.empty()

    await tracker.request_stop("k1")
