# -*- coding: utf-8 -*-
"""Tests for RunControlStore — unified run index, cancel routing, parent-child,
guidance, and cancel metadata."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hubos.app.run_control import (
    RunControlStore,
    RunEntry,
    RunType,
    _serialise_entry,
    _current_run_id_var,
    get_current_run_id,
    get_run_control_store,
    register_chat_cancel_handler,
    set_current_run_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_store_and_ctx():
    import hubos.app.run_control as _mod

    old = _mod._store
    _mod._store = None
    _mod._CHAT_CANCEL_HANDLER = None
    _current_run_id_var.set(None)
    yield
    _mod._store = old
    _current_run_id_var.set(None)


def _make_entry(**overrides) -> RunEntry:
    defaults = {
        "run_id": "",
        "run_type": RunType.SPAWN,
        "session_id": "s1",
        "monitor_task_id": "mon-1",
    }
    defaults.update(overrides)
    return RunEntry(**defaults)


# ---------------------------------------------------------------------------
# register / get_run / list_runs / unregister
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_returns_run_id():
    store = RunControlStore()
    rid = await store.register(_make_entry())
    assert rid


@pytest.mark.asyncio
async def test_register_uses_provided_run_id():
    store = RunControlStore()
    rid = await store.register(_make_entry(run_id="custom-id"))
    assert rid == "custom-id"


@pytest.mark.asyncio
async def test_get_run_found():
    store = RunControlStore()
    rid = await store.register(_make_entry())
    entry = await store.get_run(rid)
    assert entry is not None
    assert entry.run_type == RunType.SPAWN


@pytest.mark.asyncio
async def test_get_run_not_found():
    store = RunControlStore()
    assert await store.get_run("nope") is None


@pytest.mark.asyncio
async def test_list_runs_session():
    store = RunControlStore()
    await store.register(_make_entry(session_id="s1"))
    await store.register(_make_entry(session_id="s1"))
    await store.register(_make_entry(session_id="s2"))
    runs = await store.list_runs("s1")
    assert len(runs) == 2


@pytest.mark.asyncio
async def test_list_runs_active_only():
    store = RunControlStore()
    rid = await store.register(_make_entry(session_id="s1"))
    await store.update_status(rid, "done")
    active = await store.list_runs("s1", active_only=True)
    assert len(active) == 0
    all_runs = await store.list_runs("s1", active_only=False)
    assert len(all_runs) == 1


@pytest.mark.asyncio
async def test_update_status():
    store = RunControlStore()
    rid = await store.register(_make_entry())
    await store.update_status(rid, "done")
    entry = await store.get_run(rid)
    assert entry is not None
    assert entry.status == "done"


@pytest.mark.asyncio
async def test_update_status_nonexistent():
    store = RunControlStore()
    await store.update_status("nope", "done")


@pytest.mark.asyncio
async def test_unregister():
    store = RunControlStore()
    rid = await store.register(_make_entry(session_id="s1"))
    await store.unregister(rid)
    assert await store.get_run(rid) is None


# ---------------------------------------------------------------------------
# P0-1: CHAT cancel via registered handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_cancel_calls_handler():
    store = RunControlStore()
    mock_handler = AsyncMock(return_value=True)
    register_chat_cancel_handler(mock_handler)

    rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            monitor_task_id=None,
            chat_id="chat-123",
        ),
    )

    assert await store.cancel_run(rid) is True
    mock_handler.assert_called_once_with("chat-123")

    entry = await store.get_run(rid)
    assert entry is not None
    assert entry.status == "cancelled"


@pytest.mark.asyncio
async def test_chat_cancel_no_handler_registered():
    store = RunControlStore()
    rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            monitor_task_id=None,
            chat_id="chat-123",
        ),
    )
    # No handler registered — cancel returns False
    assert await store.cancel_run(rid) is False


@pytest.mark.asyncio
async def test_chat_cancel_handler_failure():
    store = RunControlStore()
    mock_handler = AsyncMock(side_effect=RuntimeError("broken"))
    register_chat_cancel_handler(mock_handler)

    rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            monitor_task_id=None,
            chat_id="chat-123",
        ),
    )
    assert await store.cancel_run(rid) is False


# ---------------------------------------------------------------------------
# P0-2: Guidance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_guidance_saves_and_cancels():
    store = RunControlStore()
    mock_handler = AsyncMock(return_value=True)
    register_chat_cancel_handler(mock_handler)

    rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            monitor_task_id=None,
            chat_id="chat-1",
        ),
    )

    result = await store.request_guidance(rid, "请改为搜索方案B")
    assert result is not None
    assert result["guidance_ack"].startswith("guidance-")

    entry = await store.get_run(rid)
    assert entry is not None
    assert "请改为搜索方案B" in entry.guidance_messages
    assert entry.status == "cancelled"


@pytest.mark.asyncio
async def test_request_guidance_terminal_returns_none():
    store = RunControlStore()
    rid = await store.register(_make_entry())
    await store.update_status(rid, "done")
    assert await store.request_guidance(rid, "text") is None


@pytest.mark.asyncio
async def test_request_guidance_nonexistent():
    store = RunControlStore()
    assert await store.request_guidance("nope", "text") is None


# ---------------------------------------------------------------------------
# P0-3: Parent-child run tree
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parent_child_link():
    store = RunControlStore()
    parent_rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="chat-1",
            session_id="s1",
        ),
    )
    child_rid = await store.register(
        _make_entry(
            run_type=RunType.SPAWN,
            monitor_task_id="mon-spawn",
            parent_run_id=parent_rid,
            session_id="s1",
        ),
    )

    parent = await store.get_run(parent_rid)
    assert child_rid in parent.child_run_ids

    child = await store.get_run(child_rid)
    assert child.parent_run_id == parent_rid


@pytest.mark.asyncio
async def test_get_run_tree():
    store = RunControlStore()
    root_rid = await store.register(
        _make_entry(run_type=RunType.CHAT, chat_id="c1", session_id="s1")
    )
    child1_rid = await store.register(
        _make_entry(
            run_type=RunType.SPAWN,
            monitor_task_id="m1",
            parent_run_id=root_rid,
            session_id="s1",
        )
    )
    child2_rid = await store.register(
        _make_entry(
            run_type=RunType.WORKFLOW,
            workflow_id="wf1",
            parent_run_id=root_rid,
            session_id="s1",
        )
    )
    grandchild_rid = await store.register(
        _make_entry(
            run_type=RunType.SPAWN,
            monitor_task_id="m2",
            parent_run_id=child1_rid,
            session_id="s1",
        )
    )

    tree = await store.get_run_tree(root_rid)
    assert len(tree) == 4
    tree_ids = {e.run_id for e in tree}
    assert tree_ids == {root_rid, child1_rid, child2_rid, grandchild_rid}


@pytest.mark.asyncio
async def test_cancel_parent_cancels_children():
    """cancel_run on parent should cancel all descendants."""
    store = RunControlStore()

    root_rid = await store.register(
        _make_entry(run_type=RunType.CHAT, chat_id="c1", session_id="s1")
    )
    child_rid = await store.register(
        _make_entry(
            run_type=RunType.SPAWN,
            monitor_task_id="m1",
            parent_run_id=root_rid,
            session_id="s1",
        )
    )

    # Register handler for monitor cancel
    with patch(
        "hubos.app.task_monitor_helpers.request_cancel_task",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_cancel:
        # Register chat handler for root cancel
        register_chat_cancel_handler(AsyncMock(return_value=True))

        ok = await store.cancel_run(root_rid)
        assert ok is True

    root = await store.get_run(root_rid)
    child = await store.get_run(child_rid)
    assert root.status == "cancelled"
    assert child.status == "cancelled"


@pytest.mark.asyncio
async def test_contextvar_propagation():
    set_current_run_id("run-abc")
    assert get_current_run_id() == "run-abc"
    set_current_run_id(None)
    assert get_current_run_id() is None


# ---------------------------------------------------------------------------
# P0-5: Cancel metadata (cancellable / cancel_behavior)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_only_cancel():
    """cancellable=False entries get status=cancelled but no real cancel call."""
    store = RunControlStore()
    rid = await store.register(
        _make_entry(
            run_type=RunType.DELEGATE,
            monitor_task_id="mon-del",
            cancellable=False,
            cancel_behavior="mark_only",
        ),
    )

    # Should NOT call request_cancel_task
    with patch(
        "hubos.app.task_monitor_helpers.request_cancel_task",
        new_callable=AsyncMock,
    ) as mock_cancel:
        assert await store.cancel_run(rid) is True
        mock_cancel.assert_not_called()

    entry = await store.get_run(rid)
    assert entry.status == "cancelled"


# ---------------------------------------------------------------------------
# cancel_run routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_run_not_found():
    store = RunControlStore()
    assert await store.cancel_run("nope") is False


@pytest.mark.asyncio
async def test_cancel_run_terminal():
    store = RunControlStore()
    rid = await store.register(_make_entry())
    await store.update_status(rid, "done")
    assert await store.cancel_run(rid) is False


@pytest.mark.asyncio
async def test_cancel_run_via_monitor():
    store = RunControlStore()
    rid = await store.register(_make_entry(monitor_task_id="mon-1"))
    with patch(
        "hubos.app.task_monitor_helpers.request_cancel_task",
        new_callable=AsyncMock,
        return_value=True,
    ):
        assert await store.cancel_run(rid) is True
    entry = await store.get_run(rid)
    assert entry.status == "cancelled"


@pytest.mark.asyncio
async def test_cancel_run_via_plan():
    store = RunControlStore()
    rid = await store.register(
        _make_entry(monitor_task_id=None, plan_id="plan-1")
    )
    mock_executor = MagicMock()
    mock_executor.cancel_plan = AsyncMock(return_value=True)
    with patch(
        "hubos.app.task_plan_executor.get_plan_executor",
        return_value=mock_executor,
    ):
        assert await store.cancel_run(rid) is True
        mock_executor.cancel_plan.assert_called_once_with("plan-1")


@pytest.mark.asyncio
async def test_cancel_run_via_workflow():
    store = RunControlStore()
    rid = await store.register(
        _make_entry(monitor_task_id=None, workflow_id="wf-1")
    )
    fake_cancel = AsyncMock()
    with patch.dict(
        "sys.modules",
        {
            "hubos.agents.tools.agent_workforce": MagicMock(
                cancel_workflow=fake_cancel
            )
        },
    ):
        assert await store.cancel_run(rid) is True
        fake_cancel.assert_called_once_with("wf-1")


# ---------------------------------------------------------------------------
# cancel_all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_all():
    store = RunControlStore()
    await store.register(_make_entry(session_id="s1", monitor_task_id="mon-1"))
    await store.register(_make_entry(session_id="s1", monitor_task_id="mon-2"))
    await store.register(_make_entry(session_id="s2", monitor_task_id="mon-3"))
    with patch(
        "hubos.app.task_monitor_helpers.request_cancel_task",
        new_callable=AsyncMock,
        return_value=True,
    ):
        cancelled = await store.cancel_all("s1")
        assert len(cancelled) == 2


@pytest.mark.asyncio
async def test_cancel_all_skips_terminal():
    store = RunControlStore()
    rid1 = await store.register(
        _make_entry(session_id="s1", monitor_task_id="mon-1")
    )
    await store.update_status(rid1, "done")
    await store.register(_make_entry(session_id="s1", monitor_task_id="mon-2"))
    cancelled = await store.cancel_all("s1")
    assert len(cancelled) == 1


# ---------------------------------------------------------------------------
# TTL eviction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evict_old_entries():
    store = RunControlStore()
    old_entry = _make_entry(session_id="s1", monitor_task_id="mon-old")
    old_entry.created_at = time.time() - 7200
    await store.register(old_entry)
    await store.update_status(old_entry.run_id, "done")
    await store.register(
        _make_entry(session_id="s1", monitor_task_id="mon-new")
    )
    assert await store.get_run(old_entry.run_id) is None


@pytest.mark.asyncio
async def test_evict_keeps_running():
    store = RunControlStore()
    old_entry = _make_entry(session_id="s1", monitor_task_id="mon-old")
    old_entry.created_at = time.time() - 7200
    await store.register(old_entry)
    await store.register(
        _make_entry(session_id="s1", monitor_task_id="mon-new")
    )
    runs = await store.list_runs("s1", active_only=False)
    assert len(runs) == 2


# ---------------------------------------------------------------------------
# Singleton + Serialisation
# ---------------------------------------------------------------------------


def test_singleton():
    assert get_run_control_store() is get_run_control_store()


def test_serialise_entry_includes_new_fields():
    entry = RunEntry(
        run_id="r1",
        run_type=RunType.DELEGATE,
        session_id="s1",
        status="running",
        monitor_task_id="mon-1",
        parent_run_id="p1",
        child_run_ids=["c1", "c2"],
        guidance_messages=["turn left"],
        cancellable=False,
        cancel_behavior="mark_only",
    )
    data = _serialise_entry(entry)
    assert data["parent_run_id"] == "p1"
    assert data["child_run_ids"] == ["c1", "c2"]
    assert data["guidance_messages"] == ["turn left"]
    assert data["cancellable"] is False
    assert data["cancel_behavior"] == "mark_only"


# ---------------------------------------------------------------------------
# Phase 3 P1: ContextVar propagation in asyncio.create_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contextvar_visible_in_created_task():
    """current_run_id set before asyncio.create_task is inherited by the task."""
    set_current_run_id("run-before-create")

    async def check():
        return get_current_run_id()

    task = asyncio.create_task(check())
    result = await task
    assert result == "run-before-create"
    set_current_run_id(None)


@pytest.mark.asyncio
async def test_contextvar_none_in_created_task():
    """When no run_id is set, created task sees None."""

    async def check():
        return get_current_run_id()

    task = asyncio.create_task(check())
    result = await task
    assert result is None


# ---------------------------------------------------------------------------
# Phase 3 P1: Parent-child cascade cancel across run types
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_root_cancels_all_descendants():
    """Cancelling the root run cancels children of different types."""
    store = RunControlStore()
    register_chat_cancel_handler(AsyncMock(return_value=True))

    root = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c1",
            session_id="s1",
        ),
    )
    child_spawn = await store.register(
        _make_entry(
            run_type=RunType.SPAWN,
            monitor_task_id="m1",
            parent_run_id=root,
            session_id="s1",
        ),
    )
    child_wf = await store.register(
        _make_entry(
            run_type=RunType.WORKFLOW,
            workflow_id="wf1",
            parent_run_id=root,
            session_id="s1",
        ),
    )
    grandchild = await store.register(
        _make_entry(
            run_type=RunType.DELEGATE,
            monitor_task_id="m2",
            parent_run_id=child_spawn,
            session_id="s1",
        ),
    )

    with patch(
        "hubos.app.task_monitor_helpers.request_cancel_task",
        new_callable=AsyncMock,
        return_value=True,
    ):
        with patch.dict(
            "sys.modules",
            {
                "hubos.agents.tools.agent_workforce": MagicMock(
                    cancel_workflow=AsyncMock()
                )
            },
        ):
            ok = await store.cancel_run(root)
            assert ok is True

    for rid in [root, child_spawn, child_wf, grandchild]:
        entry = await store.get_run(rid)
        assert entry.status == "cancelled", f"{rid} not cancelled"


# ---------------------------------------------------------------------------
# Phase 3 P1: Guidance returns ack and tracks source
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guidance_returns_ack_and_cancels():
    """request_guidance returns guidance_ack, guidance_text, cancelled_run_id."""
    store = RunControlStore()
    register_chat_cancel_handler(AsyncMock(return_value=True))

    rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c1",
            session_id="s1",
        ),
    )

    result = await store.request_guidance(rid, "改为方案B")
    assert result is not None
    assert result["guidance_ack"].startswith("guidance-")
    assert result["guidance_text"] == "改为方案B"
    assert result["cancelled_run_id"] == rid

    entry = await store.get_run(rid)
    assert entry is not None
    assert entry.status == "cancelled"
    assert entry.guidance_text == "改为方案B"


@pytest.mark.asyncio
async def test_guidance_restart_guided_from_run_id():
    """A new chat run registered with guided_from_run_id tracks the source."""
    store = RunControlStore()
    register_chat_cancel_handler(AsyncMock(return_value=True))

    old_rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c1",
            session_id="s1",
        ),
    )
    await store.request_guidance(old_rid, "turn left")

    # Simulate restart — new run with guided_from_run_id
    new_rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c2",
            session_id="s1",
            guided_from_run_id=old_rid,
            guidance_text="turn left",
        ),
    )

    new_entry = await store.get_run(new_rid)
    assert new_entry is not None
    assert new_entry.guided_from_run_id == old_rid
    assert new_entry.guidance_text == "turn left"


# ---------------------------------------------------------------------------
# Phase 3 P1: findControllableRun-equivalent tests (backend logic)
# ---------------------------------------------------------------------------


_ACTIVE_STATUSES = {"running", "pending", "waiting"}


def _pick_controllable(runs):
    """Mirror frontend findControllableRun logic in Python for testing."""
    active = [r for r in runs if r.status in _ACTIVE_STATUSES]
    if not active:
        return None
    roots = [r for r in active if not r.parent_run_id]
    if roots:
        roots.sort(key=lambda r: r.created_at, reverse=True)
        return roots[0]
    active.sort(key=lambda r: r.created_at, reverse=True)
    return active[0]


@pytest.mark.asyncio
async def test_find_controllable_prefers_root():
    store = RunControlStore()
    register_chat_cancel_handler(AsyncMock(return_value=True))

    root = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c1",
            session_id="s1",
        ),
    )
    child = await store.register(
        _make_entry(
            run_type=RunType.SPAWN,
            monitor_task_id="m1",
            parent_run_id=root,
            session_id="s1",
        ),
    )

    runs = await store.list_runs("s1", active_only=True)
    target = _pick_controllable(runs)
    assert target is not None
    assert target.run_id == root


@pytest.mark.asyncio
async def test_find_controllable_no_root_picks_recent():
    store = RunControlStore()

    r1 = await store.register(
        _make_entry(
            run_type=RunType.SPAWN,
            monitor_task_id="m1",
            parent_run_id="orphan",
            session_id="s1",
        ),
    )
    # Small sleep to ensure different created_at
    await asyncio.sleep(0.01)
    r2 = await store.register(
        _make_entry(
            run_type=RunType.DELEGATE,
            monitor_task_id="m2",
            parent_run_id="orphan",
            session_id="s1",
        ),
    )

    runs = await store.list_runs("s1", active_only=True)
    target = _pick_controllable(runs)
    assert target is not None
    assert target.run_id == r2  # more recent


@pytest.mark.asyncio
async def test_find_controllable_none_when_all_terminal():
    store = RunControlStore()

    rid = await store.register(
        _make_entry(session_id="s1", monitor_task_id="m1")
    )
    await store.update_status(rid, "done")

    runs = await store.list_runs("s1", active_only=True)
    target = _pick_controllable(runs)
    assert target is None


# ---------------------------------------------------------------------------
# Phase 3 P1: Serialise includes guided_from_run_id and guidance_text
# ---------------------------------------------------------------------------


def test_serialise_includes_guidance_fields():
    entry = RunEntry(
        run_id="r1",
        run_type=RunType.CHAT,
        session_id="s1",
        guided_from_run_id="old-r1",
        guidance_text="turn left",
    )
    data = _serialise_entry(entry)
    assert data["guided_from_run_id"] == "old-r1"
    assert data["guidance_text"] == "turn left"


# ---------------------------------------------------------------------------
# Phase 4: Guidance restart preserves guidance_text end-to-end
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guidance_restart_preserves_text():
    """Full cycle: guidance → cancel → new run with guidance_text."""
    store = RunControlStore()
    register_chat_cancel_handler(AsyncMock(return_value=True))

    # Old chat run
    old_rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c1",
            session_id="s1",
        ),
    )

    # User sends guidance
    result = await store.request_guidance(old_rid, "先停，改成检查日志")
    assert result is not None
    assert result["guidance_text"] == "先停，改成检查日志"

    # Simulate new chat run with biz_params forwarded
    new_rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c2",
            session_id="s1",
            guided_from_run_id=old_rid,
            guidance_text="先停，改成检查日志",
        ),
    )

    new_entry = await store.get_run(new_rid)
    assert new_entry is not None
    assert new_entry.guided_from_run_id == old_rid
    assert new_entry.guidance_text == "先停，改成检查日志"


def test_biz_params_guidance_text_forwarded():
    """Verify that biz_params forwarding logic (as in console.py _extract_session_and_payload)
    preserves guidance_text and related fields from request_data into native_payload.

    This replicates the exact forwarding logic from console.py lines 71-77
    to test without importing the heavy module chain.
    """
    request_data = {
        "query": "【运行中引导】先停，改成检查日志",
        "biz_params": {
            "runtime_guidance": True,
            "guided_from_run_id": "old-run-123",
            "guidance_ack": "guidance-abc",
            "guidance_text": "先停，改成检查日志",
        },
    }

    # Replicate console.py biz_params forwarding logic
    native_payload = {
        "channel_id": "console",
        "sender_id": "default",
        "content_parts": [],
        "meta": {"session_id": "s1", "user_id": "default"},
    }
    if isinstance(request_data, dict):
        _biz = request_data.get("biz_params")
    else:
        _biz = getattr(request_data, "biz_params", None)
    if _biz:
        native_payload["biz_params"] = _biz

    assert native_payload["biz_params"]["guidance_text"] == "先停，改成检查日志"
    assert native_payload["biz_params"]["guided_from_run_id"] == "old-run-123"
    assert native_payload["biz_params"]["guidance_ack"] == "guidance-abc"
    assert native_payload["biz_params"]["runtime_guidance"] is True


def test_biz_params_guidance_text_read_by_base_channel():
    """Verify the base.py logic reads guidance_text from biz_params correctly.

    This replicates the exact reading logic from base.py lines 411-415.
    """
    payload = {
        "channel_id": "console",
        "biz_params": {
            "runtime_guidance": True,
            "guided_from_run_id": "old-run-123",
            "guidance_text": "先停，改成检查日志",
        },
    }

    # Replicate base.py biz_params reading logic
    _biz = payload if isinstance(payload, dict) else {}
    _biz_params = _biz.get("biz_params") or {}
    _guided_from = None
    _guidance_text = None
    if isinstance(_biz_params, dict):
        _guided_from = _biz_params.get("guided_from_run_id")
        _guidance_text = _biz_params.get("guidance_text")

    assert _guided_from == "old-run-123"
    assert _guidance_text == "先停，改成检查日志"


# ---------------------------------------------------------------------------
# Phase 4: findControllableRun includes pending/waiting statuses
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_find_controllable_includes_pending():
    store = RunControlStore()

    rid = await store.register(
        _make_entry(
            run_type=RunType.WORKFLOW,
            workflow_id="wf1",
            session_id="s1",
        ),
    )
    await store.update_status(rid, "pending")

    # list_runs with active_only uses _TERMINAL filter — pending is NOT terminal
    runs = await store.list_runs("s1", active_only=True)
    assert len(runs) == 1

    target = _pick_controllable(runs)
    assert target is not None
    assert target.run_id == rid


@pytest.mark.asyncio
async def test_find_controllable_includes_waiting():
    store = RunControlStore()

    rid = await store.register(
        _make_entry(
            run_type=RunType.DELEGATE,
            monitor_task_id="m1",
            session_id="s1",
        ),
    )
    await store.update_status(rid, "waiting")

    runs = await store.list_runs("s1", active_only=True)
    assert len(runs) == 1

    target = _pick_controllable(runs)
    assert target is not None
    assert target.run_id == rid


@pytest.mark.asyncio
async def test_find_controllable_includes_mixed_statuses():
    """When both running and pending roots exist, _pick_controllable picks the
    most recent root (by created_at), regardless of active-status."""
    store = RunControlStore()

    running_rid = await store.register(
        _make_entry(
            run_type=RunType.CHAT,
            chat_id="c1",
            session_id="s1",
        ),
    )
    await asyncio.sleep(0.01)
    pending_rid = await store.register(
        _make_entry(
            run_type=RunType.WORKFLOW,
            workflow_id="wf1",
            session_id="s1",
        ),
    )
    await store.update_status(pending_rid, "pending")

    runs = await store.list_runs("s1", active_only=True)
    target = _pick_controllable(runs)
    assert target is not None
    # Most recent root — the pending one was created after running
    assert target.run_id == pending_rid
