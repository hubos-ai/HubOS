# -*- coding: utf-8 -*-
"""Tests for task_plan_autogen — heuristic plan generation."""
from __future__ import annotations

import asyncio
import time

import pytest

from hubos.app.task_plan import PlanStatus, get_plan_store
from hubos.app.task_plan_autogen import (
    _classify_task_type,
    _make_plan_title,
    build_draft_plan,
    build_inserted_step,
    choose_agent_for_step,
    get_recent_active_plan,
    maybe_create_or_get_active_plan,
    maybe_create_draft_plan,
    plan_hint_text,
    should_autogen_plan,
)


# ---------------------------------------------------------------------------
# should_autogen_plan
# ---------------------------------------------------------------------------


class TestShouldAutogenPlan:
    def test_empty(self):
        assert should_autogen_plan("") is False

    def test_short_text(self):
        assert should_autogen_plan("hello") is False

    def test_slash_command(self):
        assert should_autogen_plan("/help 帮我部署") is False

    def test_simple_zh_qa(self):
        assert should_autogen_plan("是什么意思") is False
        assert should_autogen_plan("为什么不行") is False
        assert should_autogen_plan("能不能帮我") is False
        assert should_autogen_plan("可以吗") is False

    def test_simple_en_qa(self):
        assert should_autogen_plan("hello world") is False
        assert should_autogen_plan("what is this") is False

    def test_zh_complex_keyword(self):
        assert should_autogen_plan("帮我部署一下服务到生产环境") is True
        assert should_autogen_plan("请优化这个代码逻辑提高性能") is True
        assert should_autogen_plan("修复这个bug导致系统崩溃") is True
        assert should_autogen_plan("实现一个新功能让用户满意") is True
        assert should_autogen_plan("搭建一个新的微服务架构设计") is True
        assert should_autogen_plan("分析一下数据生成报告结果") is True
        assert should_autogen_plan("批量处理文件转换格式任务") is True
        assert should_autogen_plan("自动化测试流程提高效率方案") is True

    def test_en_complex_keyword(self):
        assert should_autogen_plan("implement a new feature") is True
        assert should_autogen_plan("build a REST API") is True
        assert should_autogen_plan("fix the broken login") is True
        assert should_autogen_plan("optimize the query") is True
        assert should_autogen_plan("deploy to production") is True
        assert should_autogen_plan("research the market") is True

    def test_long_text_with_commas(self):
        text = "我需要完成一个很复杂的任务需要处理，包括很多步骤要处理，还有更多细节需要仔细规划，确保万无一失不能出差错，完成后还要验证一下结果并且提交，最终确认无误后归档保存整理"
        assert len(text) > 80
        assert should_autogen_plan(text) is True

    def test_long_text_without_punctuation(self):
        # Long but no commas — should not trigger on length alone
        text = "a" * 100
        assert should_autogen_plan(text) is False

    def test_min_length_boundary(self):
        # Exactly 12 chars, but no keyword
        assert should_autogen_plan("123456789012") is False
        # 12+ chars with keyword
        assert should_autogen_plan("帮我做这件事情很重要请处理") is True


# ---------------------------------------------------------------------------
# build_draft_plan
# ---------------------------------------------------------------------------


class TestBuildDraftPlan:
    def test_fix_template(self):
        steps = build_draft_plan("修复这个bug，启动不了了")
        assert len(steps) == 5
        assert "复现" in steps[0]["title"] or "错误" in steps[0]["title"]
        assert steps[0]["metadata"] == {"autogen": True, "source": "heuristic", "agent_routing": "heuristic"}

    def test_fix_en(self):
        steps = build_draft_plan("fix the crash error exception")
        assert len(steps) == 5
        assert "错误" in steps[0]["title"] or "收集" in steps[0]["title"]

    def test_build_template(self):
        steps = build_draft_plan("开发一个新的API服务模块")
        assert len(steps) == 5
        assert "需求" in steps[0]["title"]

    def test_build_en(self):
        steps = build_draft_plan("implement the feature build it")
        assert len(steps) == 5

    def test_research_template(self):
        steps = build_draft_plan("调研市场找客户的策略方案")
        assert len(steps) == 5
        assert "客户" in steps[0]["title"] or "目标" in steps[0]["title"]

    def test_default_template(self):
        steps = build_draft_plan("帮我优化并重构整个代码库的架构设计")
        # No specific fix/build/research keyword match → default
        assert len(steps) == 5
        assert "理解" in steps[0]["title"]


# ---------------------------------------------------------------------------
# maybe_create_draft_plan
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_plan_store():
    import hubos.app.task_plan as _mod
    old = _mod._store
    _mod._store = None
    yield
    _mod._store = old


@pytest.mark.asyncio
async def test_maybe_creates_plan():
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="帮我开发一个新的用户管理模块",
    )
    assert plan is not None
    assert plan.status == PlanStatus.DRAFT
    assert plan.session_id == "s1"
    assert "开发任务" in plan.title
    assert "用户管理" in plan.title
    assert len(plan.steps) == 5
    assert plan.metadata["autogen"] is True
    assert plan.metadata["source"] == "chat_heuristic"
    assert plan.metadata["original_user_text"] == "帮我开发一个新的用户管理模块"


@pytest.mark.asyncio
async def test_maybe_skips_simple_question():
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="是什么意思",
    )
    assert plan is None


@pytest.mark.asyncio
async def test_maybe_skips_slash_command():
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="/help",
    )
    assert plan is None


@pytest.mark.asyncio
async def test_dedup_within_30_min():
    # First call creates plan
    plan1 = await maybe_create_draft_plan(
        session_id="s1",
        user_text="implement a new feature for the dashboard",
    )
    assert plan1 is not None

    # Second call within 30 min → dedup
    plan2 = await maybe_create_draft_plan(
        session_id="s1",
        user_text="implement another feature for the backend",
    )
    assert plan2 is None


@pytest.mark.asyncio
async def test_allows_after_30_min():
    store = get_plan_store()

    # Create an old plan manually (created 31 minutes ago)
    old_plan = await store.create_plan(
        session_id="s1",
        title="Old plan",
        steps=[
            {"title": "A", "metadata": {"autogen": True}},
        ],
        metadata={"autogen": True, "source": "chat_heuristic"},
    )
    # Backdate
    old_plan.created_at = time.time() - 31 * 60

    # New request should succeed
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="帮我开发一个新的模块功能",
    )
    assert plan is not None
    assert plan.plan_id != old_plan.plan_id


@pytest.mark.asyncio
async def test_different_sessions_no_dedup():
    await maybe_create_draft_plan(
        session_id="s1",
        user_text="implement a new feature for the dashboard",
    )
    # Different session → no dedup
    plan = await maybe_create_draft_plan(
        session_id="s2",
        user_text="build another feature for the backend",
    )
    assert plan is not None


@pytest.mark.asyncio
async def test_store_exception_returns_none():
    store = get_plan_store()
    original = store.list_plans

    async def _failing_list(*a, **kw):
        raise RuntimeError("store broken")

    store.list_plans = _failing_list

    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="implement a new feature for the dashboard",
    )
    assert plan is None

    store.list_plans = original


@pytest.mark.asyncio
async def test_create_or_get_creates_plan_for_complex_task():
    plan = await maybe_create_or_get_active_plan(
        session_id="s1",
        user_text="帮我开发一个新的任务监控页面",
    )

    assert plan is not None
    assert plan.session_id == "s1"
    assert plan.status == PlanStatus.DRAFT


@pytest.mark.asyncio
async def test_create_or_get_reuses_deduped_active_plan():
    plan1 = await maybe_create_or_get_active_plan(
        session_id="s1",
        user_text="帮我开发一个新的任务监控页面",
    )
    assert plan1 is not None

    plan2 = await maybe_create_or_get_active_plan(
        session_id="s1",
        user_text="帮我开发另一个复杂功能模块",
    )

    assert plan2 is not None
    assert plan2.plan_id == plan1.plan_id


@pytest.mark.asyncio
async def test_create_or_get_stays_session_scoped():
    plan1 = await maybe_create_or_get_active_plan(
        session_id="s1",
        user_text="帮我开发一个新的任务监控页面",
    )
    assert plan1 is not None

    plan2 = await maybe_create_or_get_active_plan(
        session_id="s2",
        user_text="帮我开发另一个复杂功能模块",
    )

    assert plan2 is not None
    assert plan2.plan_id != plan1.plan_id
    assert plan2.session_id == "s2"


@pytest.mark.asyncio
async def test_create_or_get_returns_none_for_simple_chat():
    plan = await maybe_create_or_get_active_plan(
        session_id="s1",
        user_text="你好",
    )
    assert plan is None


@pytest.mark.asyncio
async def test_get_recent_active_plan_ignores_done_plan():
    store = get_plan_store()
    done_plan = await store.create_plan(
        session_id="s1",
        title="Done plan",
        steps=[{"title": "A"}],
    )
    await store.update_plan(done_plan.plan_id, status=PlanStatus.DONE)

    assert await get_recent_active_plan("s1") is None


# ---------------------------------------------------------------------------
# choose_agent_for_step
# ---------------------------------------------------------------------------


class TestChooseAgentForStep:
    def test_research_keywords(self):
        assert choose_agent_for_step("调研市场趋势") == "research"
        assert choose_agent_for_step("分析数据") == "research"
        assert choose_agent_for_step("research the market") == "research"
        assert choose_agent_for_step("搜索相关信息") == "research"
        assert choose_agent_for_step("竞品分析报告") == "research"

    def test_sales_keywords(self):
        assert choose_agent_for_step("跟进客户需求") == "sales"
        assert choose_agent_for_step("发送开发信") == "sales"
        assert choose_agent_for_step("sales outreach") == "sales"
        assert choose_agent_for_step("客户报价方案") == "sales"

    def test_marketing_keywords(self):
        assert choose_agent_for_step("设计营销海报") == "marketing"
        assert choose_agent_for_step("撰写文案") == "marketing"
        assert choose_agent_for_step("marketing campaign") == "marketing"
        assert choose_agent_for_step("产品图优化") == "marketing"

    def test_rd_keywords(self):
        assert choose_agent_for_step("实现新功能") == "rd"
        assert choose_agent_for_step("部署到生产环境") == "rd"
        assert choose_agent_for_step("run tests") == "rd"
        assert choose_agent_for_step("代码构建") == "rd"

    def test_finance_keywords(self):
        assert choose_agent_for_step("财务报表") == "finance"
        assert choose_agent_for_step("成本分析") == "finance"
        assert choose_agent_for_step("profit margin") == "finance"

    def test_operations_keywords(self):
        assert choose_agent_for_step("优化运营流程") == "operations"
        assert choose_agent_for_step("交付管理") == "operations"
        assert choose_agent_for_step("operations process") == "operations"

    def test_cs_keywords(self):
        assert choose_agent_for_step("客服工单处理") == "cs"
        assert choose_agent_for_step("售后服务") == "cs"
        assert choose_agent_for_step("support ticket") == "cs"

    def test_hr_keywords(self):
        assert choose_agent_for_step("人事招聘") == "hr"
        assert choose_agent_for_step("hiring plan") == "hr"
        assert choose_agent_for_step("HR流程") == "hr"

    def test_no_match_returns_none(self):
        assert choose_agent_for_step("普通步骤") is None
        assert choose_agent_for_step("general task") is None

    def test_fallback_research_from_user_text(self):
        assert choose_agent_for_step("普通步骤", user_text="调研市场找客户") == "research"

    def test_fallback_rd_from_user_text(self):
        assert choose_agent_for_step("普通步骤", user_text="开发新功能") == "rd"

    def test_description_used_for_matching(self):
        assert choose_agent_for_step("执行", step_description="investigate the logs") == "research"


# ---------------------------------------------------------------------------
# Template agent assignments
# ---------------------------------------------------------------------------


class TestTemplateAgentAssignments:
    def test_fix_template_all_rd(self):
        steps = build_draft_plan("修复这个bug导致系统崩溃")
        for step in steps:
            assert step["agent_id"] == "rd"
            assert step["metadata"]["agent_routing"] == "heuristic"

    def test_build_template_all_rd(self):
        steps = build_draft_plan("开发一个新的API服务模块")
        for step in steps:
            assert step["agent_id"] == "rd"

    def test_research_template_agents(self):
        steps = build_draft_plan("调研市场找客户的策略方案")
        agents = [s["agent_id"] for s in steps]
        assert agents[0] == "research"
        assert agents[1] == "research"
        assert agents[2] == "research"
        assert agents[3] == "sales"
        assert agents[4] == "sales"

    def test_default_template_uses_choose_fallback(self):
        steps = build_draft_plan("帮我优化并重构整个代码库的架构设计")
        # "优化" matches _COMPLEX_ZH_KEYWORDS but not BUILD/FIX/RESEARCH pattern
        # → default template. Some step descriptions may now match agent rules
        # (e.g. "查找" in research rule). At least some steps should have no agent.
        agents = [s.get("agent_id") for s in steps]
        assert None in agents, f"Expected some steps without agent, got: {agents}"

    def test_all_steps_have_metadata(self):
        for text in [
            "修复这个bug导致系统崩溃",
            "开发一个新的API服务模块",
            "调研市场找客户的策略方案",
        ]:
            steps = build_draft_plan(text)
            for step in steps:
                assert step["metadata"]["autogen"] is True
                assert step["metadata"]["agent_routing"] == "heuristic"


# ---------------------------------------------------------------------------
# maybe_create_draft_plan creates steps with agent_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_maybe_creates_plan_with_agent_ids():
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="调研市场找客户的策略方案很重要",
    )
    assert plan is not None
    assert plan.steps[0].agent_id == "research"
    assert plan.steps[3].agent_id == "sales"
    for step in plan.steps:
        assert step.metadata is not None
        assert step.metadata.get("agent_routing") == "heuristic"


@pytest.mark.asyncio
async def test_maybe_creates_fix_plan_with_rd_agents():
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="修复这个bug导致系统崩溃的问题",
    )
    assert plan is not None
    for step in plan.steps:
        assert step.agent_id == "rd"


@pytest.mark.asyncio
async def test_high_risk_plan_requires_confirmation():
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="帮我群发邮件给所有客户通知新功能发布",
    )
    assert plan is not None
    assert plan.metadata["requires_confirmation"] is True
    assert plan.metadata["risk_level"] == "high"
    assert plan.status == PlanStatus.WAITING_USER


@pytest.mark.asyncio
async def test_low_risk_plan_no_confirmation():
    plan = await maybe_create_draft_plan(
        session_id="s1",
        user_text="帮我分析一下最新的数据报告和趋势",
    )
    assert plan is not None
    assert plan.metadata["requires_confirmation"] is False
    assert plan.metadata["risk_level"] == "low"
    assert plan.status == PlanStatus.DRAFT


class TestBuildInsertedStep:
    def test_fix_instruction_gets_rd(self):
        step = build_inserted_step("修复登录页面的bug")
        assert step["agent_id"] == "rd"
        assert step["metadata"]["inserted_from_chat"] is True
        assert step["metadata"]["agent_routing"] == "heuristic"
        assert step["title"] == "修复登录页面的bug"
        assert step["description"] == "修复登录页面的bug"

    def test_customer_instruction_gets_sales_or_research(self):
        step = build_inserted_step("搜索潜在客户信息")
        # "客户" matches sales (higher priority than research in _AGENT_RULES)
        assert step["agent_id"] in ("research", "sales")
        assert step["metadata"]["inserted_from_chat"] is True

    def test_long_instruction_truncates_title(self):
        long_text = "这是一个非常长的指令" * 10
        step = build_inserted_step(long_text)
        assert len(step["title"]) <= 50
        assert step["description"] == long_text.strip()

    def test_no_match_no_agent(self):
        step = build_inserted_step("普通指令内容")
        assert step.get("agent_id") is None
        assert step["metadata"]["inserted_from_chat"] is True

    def test_fallback_from_plan_title(self):
        step = build_inserted_step("执行任务", plan_title="修复线上bug")
        assert step["agent_id"] == "rd"


# ---------------------------------------------------------------------------
# Step 13: Title prefixes, descriptions, whitespace, plan_hint_text
# ---------------------------------------------------------------------------


class TestClassifyTaskType:
    def test_fix(self):
        assert _classify_task_type("修复这个bug") == "fix"
        assert _classify_task_type("fix the crash") == "fix"

    def test_build(self):
        assert _classify_task_type("开发新模块") == "build"
        assert _classify_task_type("implement feature") == "build"

    def test_research(self):
        assert _classify_task_type("调研市场客户") == "research"
        assert _classify_task_type("research the market") == "research"

    def test_default(self):
        assert _classify_task_type("帮我优化并重构代码") == "default"


class TestMakePlanTitle:
    def test_fix_prefix(self):
        title = _make_plan_title("修复登录页面bug", "fix")
        assert title.startswith("修复任务：")

    def test_build_prefix(self):
        title = _make_plan_title("开发新功能模块", "build")
        assert title.startswith("开发任务：")

    def test_research_prefix(self):
        title = _make_plan_title("调研市场数据", "research")
        assert title.startswith("调研任务：")

    def test_default_prefix(self):
        title = _make_plan_title("优化并重构代码", "default")
        assert title.startswith("任务计划：")

    def test_whitespace_cleaned(self):
        title = _make_plan_title("帮我  做这个\n\n任务", "default")
        assert "\n" not in title
        assert "  " not in title

    def test_truncation_with_ellipsis(self):
        long_text = "a" * 50
        title = _make_plan_title(long_text, "default")
        assert title.endswith("\u2026")
        assert len(title) < 60


class TestStepDescriptionsNonEmpty:
    def test_fix_descriptions(self):
        steps = build_draft_plan("修复这个bug导致系统崩溃")
        for step in steps:
            assert step["description"].strip(), f"Step '{step['title']}' has empty description"

    def test_build_descriptions(self):
        steps = build_draft_plan("开发一个新的API服务模块")
        for step in steps:
            assert step["description"].strip()

    def test_research_descriptions(self):
        steps = build_draft_plan("调研市场找客户的策略方案")
        for step in steps:
            assert step["description"].strip()

    def test_default_descriptions(self):
        steps = build_draft_plan("帮我优化并重构整个代码库")
        for step in steps:
            assert step["description"].strip()


class TestPlanHintText:
    def test_normal_hint(self):
        hint = plan_hint_text(high_risk=False)
        assert "执行计划" in hint
        assert "高风险" not in hint

    def test_high_risk_hint(self):
        hint = plan_hint_text(high_risk=True)
        assert "高风险" in hint
        assert "确认" in hint

    def test_default_is_normal(self):
        hint = plan_hint_text()
        assert "高风险" not in hint
