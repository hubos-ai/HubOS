# -*- coding: utf-8 -*-
"""Heuristic-based automatic TaskPlan generation for complex user requests.

When a user submits a complex task via the Chat interface, this module
decides whether to auto-create a draft plan and builds the plan steps
from rule-based templates (no LLM calls).

Integration point: ``post_console_chat`` in ``routers/console.py``
calls ``maybe_create_or_get_active_plan`` before normal agent execution so
complex tasks enter the plan flow instead of starting an uncontrolled chat run.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from .task_plan import PlanStatus, TaskPlan, get_plan_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex / keyword constants
# ---------------------------------------------------------------------------

_COMPLEX_ZH_KEYWORDS = re.compile(
    r"帮我|开发|优化|修复|实现|搭建|分析|检查|验证|部署|重构|批量|自动化|找客户|调研",
)
_COMPLEX_EN_KEYWORDS = re.compile(
    r"\b(implement|build|fix|optimize|refactor|deploy|analyze|research"
    r"|automate|investigate|validate)\b",
    re.IGNORECASE,
)

_EXCLUDE_ZH = re.compile(
    r"^(是什么|为什么|怎么回事|能不能|可以吗|价格多少|几点|hello|hi|你好|谢谢|好的)",
)
_EXCLUDE_EN = re.compile(
    r"^(what is|why|how come|can you|hello|hi|thanks|ok|sure)",
    re.IGNORECASE,
)

_MIN_LENGTH = 12
_COMPLEX_LENGTH = 80

# ---------------------------------------------------------------------------
# should_autogen_plan
# ---------------------------------------------------------------------------


def should_autogen_plan(user_text: str) -> bool:
    """Return True if *user_text* looks like a complex task worthy of a plan.

    Uses heuristic rules (no LLM).
    """
    text = user_text.strip()
    if not text:
        return False

    # Exclude slash commands
    if text.startswith("/"):
        return False

    # Exclude simple Q&A patterns
    if _EXCLUDE_ZH.match(text) or _EXCLUDE_EN.match(text):
        return False

    # Too short
    if len(text) < _MIN_LENGTH:
        return False

    # Match complex task keywords
    if _COMPLEX_ZH_KEYWORDS.search(text) or _COMPLEX_EN_KEYWORDS.search(text):
        return True

    # Long text with multiple verbs / commas / newlines
    if len(text) > _COMPLEX_LENGTH:
        punctuation_count = (
            text.count("，") + text.count(",") + text.count("\n")
        )
        if punctuation_count >= 2:
            return True

    return False


# ---------------------------------------------------------------------------
# Step templates (with agent_id)
# ---------------------------------------------------------------------------

_FIX_STEPS = [
    {
        "title": "复现和收集错误信息",
        "description": "收集报错、日志、复现路径，确认问题边界",
        "agent_id": "rd",
    },
    {
        "title": "定位根因",
        "description": "查找相关代码、配置和最近变更，定位导致问题的原因",
        "agent_id": "rd",
    },
    {"title": "制定修复方案", "description": "选择最小安全改动，明确风险和验证方式", "agent_id": "rd"},
    {
        "title": "执行最小改动",
        "description": "按方案修改代码或配置，避免扩大影响范围",
        "agent_id": "rd",
    },
    {
        "title": "验证结果",
        "description": "运行相关测试、构建或手动验证，确认问题已解决",
        "agent_id": "rd",
    },
]

_BUILD_STEPS = [
    {"title": "梳理需求和边界", "description": "明确功能范围、验收标准和关键约束", "agent_id": "rd"},
    {"title": "查找相关代码入口", "description": "定位需改动的模块、接口和数据流", "agent_id": "rd"},
    {
        "title": "设计方案",
        "description": "设计实现方案，包括接口、数据结构和异常处理",
        "agent_id": "rd",
    },
    {"title": "实现改动", "description": "按方案编码实现，保持代码清晰可维护", "agent_id": "rd"},
    {"title": "测试验证", "description": "运行单元测试、集成测试，验证功能符合预期", "agent_id": "rd"},
]

_RESEARCH_STEPS = [
    {
        "title": "明确目标市场和客户画像",
        "description": "定义目标市场、理想客户特征和关键筛选条件",
        "agent_id": "research",
    },
    {
        "title": "查找高质量数据源",
        "description": "搜索并评估可用的数据来源和信息渠道",
        "agent_id": "research",
    },
    {
        "title": "收集候选数据",
        "description": "从数据源中提取候选条目，整理为结构化格式",
        "agent_id": "research",
    },
    {
        "title": "过滤和评分",
        "description": "按相关性、质量等维度筛选并排序结果",
        "agent_id": "sales",
    },
    {
        "title": "输出结果和后续建议",
        "description": "汇总调研结果，提出可执行的后续行动建议",
        "agent_id": "sales",
    },
]

_DEFAULT_STEPS = [
    {"title": "理解目标和约束", "description": "明确任务目标、优先级和边界条件"},
    {"title": "收集上下文", "description": "查找相关的背景信息、数据和已有资源"},
    {"title": "拆解执行步骤", "description": "将任务分解为可顺序执行的具体步骤"},
    {"title": "执行关键步骤", "description": "按优先级逐步完成各子任务"},
    {"title": "总结结果和下一步", "description": "汇总执行结果，列出未完成项和后续行动"},
]

_FIX_PATTERN = re.compile(
    r"修复|fix|bug|报错|启动不了|crash|error|exception|异常", re.IGNORECASE
)
_BUILD_PATTERN = re.compile(
    r"开发|实现|build|implement|搭建|create|add|搭建", re.IGNORECASE
)
_RESEARCH_PATTERN = re.compile(
    r"找客户|客户开发|调研|research|investigate|分析市场|market", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Agent routing rules
# ---------------------------------------------------------------------------

_AGENT_RULES: List[tuple[re.Pattern, str]] = [
    (
        re.compile(r"财务|成本|利润|发票|finance|cost|profit|invoice", re.IGNORECASE),
        "finance",
    ),
    (re.compile(r"人事|招聘|\bhr|hiring", re.IGNORECASE), "hr"),
    (re.compile(r"客服|售后|support|service|ticket", re.IGNORECASE), "cs"),
    (
        re.compile(r"运营|流程|交付|operations|process|delivery", re.IGNORECASE),
        "operations",
    ),
    (
        re.compile(
            r"文案|图片|产品图|营销|海报|品牌|marketing|copy|image|design", re.IGNORECASE
        ),
        "marketing",
    ),
    (
        re.compile(
            r"客户|销售|开发信|报价|跟进|sales|customer|lead|outreach|email",
            re.IGNORECASE,
        ),
        "sales",
    ),
    (
        re.compile(
            r"调研|研究|查找|搜索|分析|竞品|市场|research|analyze|investigate|search",
            re.IGNORECASE,
        ),
        "research",
    ),
    (
        re.compile(
            r"代码|修复|实现|开发|部署|测试|构建|bug|code|fix|implement|build|deploy|test",
            re.IGNORECASE,
        ),
        "rd",
    ),
]


def choose_agent_for_step(
    step_title: str,
    step_description: str = "",
    user_text: str = "",
) -> Optional[str]:
    """Pick an agent_id for a step using heuristic keyword matching.

    Returns ``None`` if no rule matches.
    """
    combined = f"{step_title} {step_description}"
    for pattern, agent_id in _AGENT_RULES:
        if pattern.search(combined):
            return agent_id

    # Default fallback based on overall task type
    if user_text:
        if _RESEARCH_PATTERN.search(user_text):
            return "research"
        if _BUILD_PATTERN.search(user_text) or _FIX_PATTERN.search(user_text):
            return "rd"

    return None


def _classify_task_type(user_text: str) -> str:
    """Return task type: 'fix', 'build', 'research', or 'default'."""
    if _FIX_PATTERN.search(user_text):
        return "fix"
    if _BUILD_PATTERN.search(user_text):
        return "build"
    if _RESEARCH_PATTERN.search(user_text):
        return "research"
    return "default"


def _make_plan_title(user_text: str, task_type: str) -> str:
    """Generate a plan title with type prefix and cleaned keyword summary."""
    prefix_map = {
        "fix": "修复任务：",
        "build": "开发任务：",
        "research": "调研任务：",
        "default": "任务计划：",
    }
    prefix = prefix_map.get(task_type, "任务计划：")
    # Clean whitespace: collapse newlines, multiple spaces
    summary = re.sub(r"\s+", " ", user_text.strip())
    # Truncate to ~40 chars
    if len(summary) > 40:
        summary = summary[:40] + "\u2026"
    return prefix + summary


def build_draft_plan(user_text: str) -> List[Dict[str, Any]]:
    """Return a list of step dicts based on heuristic pattern matching."""
    if _FIX_PATTERN.search(user_text):
        template = _FIX_STEPS
    elif _BUILD_PATTERN.search(user_text):
        template = _BUILD_STEPS
    elif _RESEARCH_PATTERN.search(user_text):
        template = _RESEARCH_STEPS
    else:
        template = _DEFAULT_STEPS

    steps: List[Dict[str, Any]] = []
    for s in template:
        agent_id = s.get("agent_id") or choose_agent_for_step(
            s["title"],
            s.get("description", ""),
            user_text,
        )
        step: Dict[str, Any] = {
            "title": s["title"],
            "description": s["description"],
            "metadata": {
                "autogen": True,
                "source": "heuristic",
                "agent_routing": "heuristic",
            },
        }
        if agent_id:
            step["agent_id"] = agent_id
        steps.append(step)

    return steps


def build_inserted_step(
    instruction: str,
    plan_title: str = "",
    original_user_text: str = "",
) -> Dict[str, Any]:
    """Build a step dict from a chat-inserted instruction.

    Uses heuristic agent routing (no LLM).
    """
    title = instruction.strip()[:50]
    agent_id = choose_agent_for_step(
        instruction,
        instruction,
        original_user_text or plan_title,
    )
    step: Dict[str, Any] = {
        "title": title,
        "description": instruction.strip(),
        "metadata": {
            "inserted_from_chat": True,
            "agent_routing": "heuristic",
        },
    }
    if agent_id:
        step["agent_id"] = agent_id
    return step


# ---------------------------------------------------------------------------
# maybe_create_draft_plan
# ---------------------------------------------------------------------------

_DEDUP_WINDOW_SECS = 30 * 60  # 30 minutes


async def maybe_create_draft_plan(
    session_id: str,
    user_text: str,
) -> Optional[TaskPlan]:
    """If appropriate, create a draft plan for the complex task.

    Returns the created ``TaskPlan`` or ``None``.

    - Does **not** call LLM.
    - Does **not** auto-execute.
    - Deduplicates: skips if a recent draft/running/waiting_user plan
      already exists for the same session.
    - Failures are caught and logged; never raises.
    """
    try:
        if not should_autogen_plan(user_text):
            return None

        store = get_plan_store()

        # Dedup: check for recent plan in the same session
        recent = await store.list_plans(
            session_id=session_id,
            limit=5,
        )
        now = time.time()
        for plan in recent:
            if plan.status.value in ("draft", "running", "waiting_user"):
                if (
                    plan.created_at
                    and (now - plan.created_at) < _DEDUP_WINDOW_SECS
                ):
                    logger.debug(
                        "task_plan_autogen: skipping dedup, plan %s is recent",
                        plan.plan_id,
                    )
                    return None

        steps = build_draft_plan(user_text)
        task_type = _classify_task_type(user_text)
        title = _make_plan_title(user_text, task_type)

        plan = await store.create_plan(
            session_id=session_id,
            title=title,
            steps=steps,
            metadata={
                "autogen": True,
                "source": "chat_heuristic",
                "original_user_text": user_text,
            },
        )

        # Risk assessment
        from .task_plan_risk import assess_plan_risk

        risk = assess_plan_risk(title, steps, user_text)
        plan_meta_update: Dict[str, Any] = {
            "risk_level": risk.level,
            "risk_reasons": risk.reasons,
            "requires_confirmation": risk.requires_confirmation,
        }
        if risk.requires_confirmation:
            plan_meta_update["waiting_reason"] = "confirmation_required"
            await store.update_plan(
                plan.plan_id,
                status=PlanStatus.WAITING_USER,
                metadata=plan_meta_update,
            )
        else:
            await store.update_plan(plan.plan_id, metadata=plan_meta_update)

        logger.info(
            "task_plan_autogen: created draft plan %s for session %s",
            plan.plan_id,
            session_id,
        )
        return plan

    except Exception:  # noqa: BLE001
        logger.warning(
            "task_plan_autogen: failed to create draft plan", exc_info=True
        )
        return None


async def get_recent_active_plan(session_id: str) -> Optional[TaskPlan]:
    """Return the newest draft/running/waiting plan for *session_id*.

    This is intentionally session-scoped. A chat command in one session should
    never start, pause, or reuse another session's plan.
    """
    try:
        store = get_plan_store()
        active: List[TaskPlan] = []
        for status in (
            PlanStatus.DRAFT,
            PlanStatus.RUNNING,
            PlanStatus.WAITING_USER,
        ):
            active.extend(
                await store.list_plans(
                    session_id=session_id,
                    status=status,
                    limit=5,
                ),
            )
        active.sort(key=lambda p: p.updated_at, reverse=True)
        return active[0] if active else None
    except Exception:  # noqa: BLE001
        logger.warning(
            "task_plan_autogen: failed to find active plan",
            exc_info=True,
        )
        return None


async def maybe_create_or_get_active_plan(
    session_id: str,
    user_text: str,
) -> Optional[TaskPlan]:
    """Create a plan for a complex request, or reuse an existing active one.

    ``maybe_create_draft_plan`` returns ``None`` both for non-complex input and
    for deduped requests. The console router needs to distinguish those cases:
    if the input is complex and there is already an active plan in the same
    session, we should still stop normal agent execution and route the user back
    to the plan flow.
    """
    if not should_autogen_plan(user_text):
        return None

    plan = await maybe_create_draft_plan(
        session_id=session_id, user_text=user_text
    )
    if plan is not None:
        return plan
    return await get_recent_active_plan(session_id)


# ---------------------------------------------------------------------------
# Plan hint text for assistant messages
# ---------------------------------------------------------------------------

_PLAN_HINT_NORMAL = (
    "我已为这个任务生成执行计划，可在右侧「计划」面板查看。" "你可以说「开始执行」「暂停」「插入一步…」或「取消计划」。"
)
_PLAN_HINT_HIGH_RISK = (
    "我已为这个任务生成执行计划。该计划包含高风险操作，执行前需要你确认。" "你可以在右侧「计划」面板查看，确认后说「继续执行」。"
)


def plan_hint_text(high_risk: bool = False) -> str:
    """Return the user-visible hint about plan creation."""
    return _PLAN_HINT_HIGH_RISK if high_risk else _PLAN_HINT_NORMAL
