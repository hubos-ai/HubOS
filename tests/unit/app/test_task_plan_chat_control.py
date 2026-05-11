# -*- coding: utf-8 -*-
"""Tests for task_plan_chat_control — plan intent detection and handling."""
from __future__ import annotations

import pytest

from hubos.app.task_plan import PlanStatus, get_plan_store
from hubos.app.task_plan_chat_control import (
    INTENT_CANCEL,
    INTENT_CONFIRM,
    INTENT_INSERT_STEP,
    INTENT_NONE,
    INTENT_PAUSE,
    INTENT_RESUME,
    INTENT_START,
    detect_plan_chat_intent,
    handle_plan_chat_control,
)


# ---------------------------------------------------------------------------
# detect_plan_chat_intent
# ---------------------------------------------------------------------------


class TestDetectIntent:
    def test_slash_command(self):
        assert detect_plan_chat_intent("/help")[0] == INTENT_NONE

    def test_empty(self):
        assert detect_plan_chat_intent("")[0] == INTENT_NONE

    def test_too_short(self):
        assert detect_plan_chat_intent("a")[0] == INTENT_NONE

    # ── pause ────────────────────────────────────────────────────────────

    def test_pause_zh(self):
        assert detect_plan_chat_intent("暂停")[0] == INTENT_PAUSE
        assert detect_plan_chat_intent("先停一下")[0] == INTENT_PAUSE
        assert detect_plan_chat_intent("等一下")[0] == INTENT_PAUSE
        assert detect_plan_chat_intent("先别继续")[0] == INTENT_PAUSE
        assert detect_plan_chat_intent("先不要执行")[0] == INTENT_PAUSE

    def test_pause_en(self):
        assert detect_plan_chat_intent("pause")[0] == INTENT_PAUSE
        assert detect_plan_chat_intent("Pause.")[0] == INTENT_PAUSE
        assert detect_plan_chat_intent("hold on")[0] == INTENT_PAUSE
        assert detect_plan_chat_intent("wait")[0] == INTENT_PAUSE

    # ── resume ───────────────────────────────────────────────────────────

    def test_resume_zh(self):
        assert detect_plan_chat_intent("继续")[0] == INTENT_RESUME
        assert detect_plan_chat_intent("继续执行")[0] == INTENT_RESUME
        assert detect_plan_chat_intent("恢复")[0] == INTENT_RESUME
        assert detect_plan_chat_intent("接着做")[0] == INTENT_RESUME

    def test_resume_en(self):
        assert detect_plan_chat_intent("resume")[0] == INTENT_RESUME
        assert detect_plan_chat_intent("continue")[0] == INTENT_RESUME
        assert detect_plan_chat_intent("proceed")[0] == INTENT_RESUME

    # ── cancel ───────────────────────────────────────────────────────────

    def test_cancel_zh(self):
        assert detect_plan_chat_intent("取消任务")[0] == INTENT_CANCEL
        assert detect_plan_chat_intent("取消计划")[0] == INTENT_CANCEL
        assert detect_plan_chat_intent("终止任务")[0] == INTENT_CANCEL
        assert detect_plan_chat_intent("不要做了")[0] == INTENT_CANCEL
        assert detect_plan_chat_intent("停止任务")[0] == INTENT_CANCEL

    def test_cancel_en(self):
        assert detect_plan_chat_intent("cancel task")[0] == INTENT_CANCEL
        assert detect_plan_chat_intent("cancel plan")[0] == INTENT_CANCEL
        assert detect_plan_chat_intent("abort")[0] == INTENT_CANCEL
        assert detect_plan_chat_intent("terminate")[0] == INTENT_CANCEL

    def test_ambiguous_not_cancel(self):
        """'停止使用旧方法，改成先...' should NOT be cancel."""
        intent, _ = detect_plan_chat_intent("停止使用旧方法，改成先用新方案")
        assert intent != INTENT_CANCEL

    # ── start ────────────────────────────────────────────────────────────

    def test_start_zh(self):
        assert detect_plan_chat_intent("开始执行")[0] == INTENT_START
        assert detect_plan_chat_intent("执行计划")[0] == INTENT_START
        assert detect_plan_chat_intent("开始这个计划")[0] == INTENT_START
        assert detect_plan_chat_intent("运行计划")[0] == INTENT_START

    def test_start_en(self):
        assert detect_plan_chat_intent("start plan")[0] == INTENT_START
        assert detect_plan_chat_intent("run plan")[0] == INTENT_START
        assert detect_plan_chat_intent("execute plan")[0] == INTENT_START
        assert detect_plan_chat_intent("proceed with plan")[0] == INTENT_START

    def test_start_not_triggered_by_bare_kaishi(self):
        """裸词'开始'不应触发 start intent — 需要明确的计划执行意图。"""
        assert detect_plan_chat_intent("开始")[0] != INTENT_START
        assert detect_plan_chat_intent("我刚开始学 Python")[0] != INTENT_START
        assert detect_plan_chat_intent("开始写代码吧")[0] != INTENT_START
        assert detect_plan_chat_intent("我们从第一步开始")[0] != INTENT_START
        assert detect_plan_chat_intent("开始一个新的项目")[0] != INTENT_START

    # ── insert_step ──────────────────────────────────────────────────────

    def test_insert_zh(self):
        intent, text = detect_plan_chat_intent("先用最新的数据跑一遍")
        assert intent == INTENT_INSERT_STEP
        assert text is not None

        intent, text = detect_plan_chat_intent("加一步：检查配置")
        assert intent == INTENT_INSERT_STEP

        intent, text = detect_plan_chat_intent("补充一个步骤：验证权限")
        assert intent == INTENT_INSERT_STEP

    def test_insert_en(self):
        intent, text = detect_plan_chat_intent("first check the config")
        assert intent == INTENT_INSERT_STEP

        intent, text = detect_plan_chat_intent("add a step to verify")
        assert intent == INTENT_INSERT_STEP

        intent, text = detect_plan_chat_intent("also run the tests")
        assert intent == INTENT_INSERT_STEP

    # ── none ─────────────────────────────────────────────────────────────

    def test_normal_chat(self):
        assert detect_plan_chat_intent("你好")[0] == INTENT_NONE
        assert detect_plan_chat_intent("帮我看看这个代码")[0] == INTENT_NONE
        assert detect_plan_chat_intent("what is this")[0] == INTENT_NONE

    # ── priority ─────────────────────────────────────────────────────────

    def test_cancel_beats_pause(self):
        assert detect_plan_chat_intent("取消任务，暂停")[0] == INTENT_CANCEL

    # ── confirm ──────────────────────────────────────────────────────────

    def test_confirm_zh(self):
        assert detect_plan_chat_intent("确认")[0] == INTENT_CONFIRM
        assert detect_plan_chat_intent("同意")[0] == INTENT_CONFIRM
        assert detect_plan_chat_intent("批准")[0] == INTENT_CONFIRM
        assert detect_plan_chat_intent("可以执行")[0] == INTENT_CONFIRM

    def test_confirm_en(self):
        assert detect_plan_chat_intent("confirm")[0] == INTENT_CONFIRM
        assert detect_plan_chat_intent("approve")[0] == INTENT_CONFIRM
        assert detect_plan_chat_intent("yes proceed")[0] == INTENT_CONFIRM
        assert detect_plan_chat_intent("go ahead")[0] == INTENT_CONFIRM


# ---------------------------------------------------------------------------
# handle_plan_chat_control
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_stores():
    import hubos.app.task_plan as _plan_mod
    import hubos.app.task_plan_executor as _exec_mod

    old_plan = _plan_mod._store
    old_exec = _exec_mod._executor
    _plan_mod._store = None
    _exec_mod._executor = None
    yield
    _plan_mod._store = old_plan
    _exec_mod._executor = old_exec


@pytest.mark.asyncio
async def test_none_intent_returns_none():
    result = await handle_plan_chat_control("s1", "你好")
    assert result is None


@pytest.mark.asyncio
async def test_no_active_plan_returns_none():
    result = await handle_plan_chat_control("s1", "暂停")
    assert result is None


@pytest.mark.asyncio
async def test_handle_pause_with_active_plan():
    store = get_plan_store()
    plan = await store.create_plan(
        session_id="s1", title="P",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    from hubos.app.task_plan_executor import get_plan_executor
    executor = get_plan_executor()
    await executor.start_plan(plan.plan_id)
    import asyncio
    await asyncio.sleep(0.1)

    result = await handle_plan_chat_control("s1", "暂停")
    assert result is not None
    assert "暂停" in result

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.WAITING_USER


@pytest.mark.asyncio
async def test_handle_resume_with_active_plan():
    store = get_plan_store()
    plan = await store.create_plan(
        session_id="s1", title="P",
        steps=[{"title": "A"}, {"title": "B"}],
    )
    from hubos.app.task_plan_executor import get_plan_executor
    executor = get_plan_executor()
    await executor.start_plan(plan.plan_id)
    import asyncio
    await asyncio.sleep(0.1)
    await executor.pause_plan(plan.plan_id)

    result = await handle_plan_chat_control("s1", "继续执行")
    assert result is not None
    assert "继续执行" in result


@pytest.mark.asyncio
async def test_handle_start_with_draft_plan():
    store = get_plan_store()
    await store.create_plan(
        session_id="s1", title="P",
        steps=[{"title": "A"}],
    )

    result = await handle_plan_chat_control("s1", "开始执行")
    assert result is not None
    assert "开始" in result


@pytest.mark.asyncio
async def test_handle_cancel_with_active_plan():
    store = get_plan_store()
    plan = await store.create_plan(
        session_id="s1", title="P",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    from hubos.app.task_plan_executor import get_plan_executor
    executor = get_plan_executor()
    await executor.start_plan(plan.plan_id)
    import asyncio
    await asyncio.sleep(0.05)

    result = await handle_plan_chat_control("s1", "取消任务")
    assert result is not None
    assert "取消" in result


@pytest.mark.asyncio
async def test_handle_insert_step_with_active_plan():
    store = get_plan_store()
    plan = await store.create_plan(
        session_id="s1", title="P",
        steps=[{"title": "A"}, {"title": "B"}],
    )

    result = await handle_plan_chat_control("s1", "补充一步：检查配置文件")
    assert result is not None
    assert "插入" in result

    refreshed = await store.get_plan(plan.plan_id)
    assert len(refreshed.steps) == 3
    # New step should have metadata
    new_step = [s for s in refreshed.steps if s.title == "补充一步：检查配置文件"]
    assert len(new_step) == 1
    assert new_step[0].metadata is not None
    assert new_step[0].metadata.get("inserted_from_chat") is True


@pytest.mark.asyncio
async def test_handle_insert_assigns_agent():
    store = get_plan_store()
    await store.create_plan(
        session_id="s1", title="P",
        steps=[{"title": "A"}],
    )

    await handle_plan_chat_control("s1", "补充一步：修复代码中的bug")

    refreshed = await store.get_plan("s1")
    refreshed_plans = await store.list_plans(session_id="s1")
    plan = refreshed_plans[0]
    inserted = [s for s in plan.steps if s.title.startswith("补充一步")]
    assert len(inserted) == 1
    assert inserted[0].agent_id == "rd"


@pytest.mark.asyncio
async def test_slash_command_not_intercepted():
    result = await handle_plan_chat_control("s1", "/help 暂停")
    assert result is None


@pytest.mark.asyncio
async def test_handle_confirm_waiting_high_risk_plan():
    store = get_plan_store()
    plan = await store.create_plan(
        session_id="s1", title="Deploy",
        steps=[{"title": "deploy"}],
        metadata={"requires_confirmation": True},
    )
    from hubos.app.task_plan_executor import get_plan_executor
    executor = get_plan_executor()
    # start will gate to waiting_user
    await executor.start_plan(plan.plan_id)

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status == PlanStatus.WAITING_USER

    result = await handle_plan_chat_control("s1", "确认")
    assert result is not None
    assert "确认" in result


@pytest.mark.asyncio
async def test_resume_also_confirms_high_risk_plan():
    store = get_plan_store()
    plan = await store.create_plan(
        session_id="s1", title="Deploy",
        steps=[{"title": "deploy"}],
        metadata={"requires_confirmation": True},
    )
    from hubos.app.task_plan_executor import get_plan_executor
    executor = get_plan_executor()
    await executor.start_plan(plan.plan_id)

    result = await handle_plan_chat_control("s1", "继续执行")
    assert result is not None
    assert "继续执行" in result


# ---------------------------------------------------------------------------
# P0-2: Cross-session isolation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_cross_session_cancel():
    """Cancel in session s_empty should NOT affect a running plan in s_other."""
    store = get_plan_store()
    # Create a running plan in s_other
    plan_other = await store.create_plan(
        session_id="s_other", title="Other session plan",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    from hubos.app.task_plan_executor import get_plan_executor
    executor = get_plan_executor()
    await executor.start_plan(plan_other.plan_id)
    import asyncio
    await asyncio.sleep(0.1)

    # s_empty has no plan — cancel intent should return None
    result = await handle_plan_chat_control("s_empty", "取消任务")
    assert result is None

    # s_other's plan should still be running/waiting
    refreshed = await store.get_plan(plan_other.plan_id)
    assert refreshed.status in (PlanStatus.RUNNING, PlanStatus.WAITING_USER, PlanStatus.DONE)


@pytest.mark.asyncio
async def test_no_cross_session_pause():
    """Pause in session s_empty should NOT affect a plan in s_other."""
    store = get_plan_store()
    plan_other = await store.create_plan(
        session_id="s_other", title="Other plan",
        steps=[{"title": "A"}, {"title": "B"}, {"title": "C"}],
    )
    from hubos.app.task_plan_executor import get_plan_executor
    executor = get_plan_executor()
    await executor.start_plan(plan_other.plan_id)
    import asyncio
    await asyncio.sleep(0.1)

    result = await handle_plan_chat_control("s_empty", "暂停")
    assert result is None


@pytest.mark.asyncio
async def test_same_session_works_normally():
    """Same-session control should work as before."""
    store = get_plan_store()
    plan = await store.create_plan(
        session_id="s1", title="My plan",
        steps=[{"title": "A"}, {"title": "B"}],
    )

    # start
    result = await handle_plan_chat_control("s1", "开始执行")
    assert result is not None
    assert "开始" in result

    refreshed = await store.get_plan(plan.plan_id)
    assert refreshed.status in (PlanStatus.RUNNING, PlanStatus.DONE)
