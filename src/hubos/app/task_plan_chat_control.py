# -*- coding: utf-8 -*-
"""Lightweight heuristic detection of TaskPlan control intents from chat.

When a user types pause/resume/cancel/start/insert commands in the console
chat, this module detects the intent and routes it to the plan executor
instead of sending the message to the LLM agent.
"""
from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from .task_plan import PlanStatus, get_plan_store
from .task_plan_autogen import build_inserted_step
from .task_plan_executor import get_plan_executor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent types
# ---------------------------------------------------------------------------

INTENT_NONE = "none"
INTENT_PAUSE = "pause"
INTENT_RESUME = "resume"
INTENT_CANCEL = "cancel"
INTENT_START = "start"
INTENT_INSERT_STEP = "insert_step"
INTENT_CONFIRM = "confirm"

# ---------------------------------------------------------------------------
# Detection rules (compiled once)
# ---------------------------------------------------------------------------

_PAUSE_ZH = re.compile(r"暂停|先停|停一下|等一下|先别继续|先不要执行")
_PAUSE_EN = re.compile(
    r"^\s*(pause|stop for now|hold on|wait)\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)

_RESUME_ZH = re.compile(r"继续执行|恢复执行|接着做|恢复|继续")
_RESUME_EN = re.compile(
    r"^\s*(resume|continue|proceed)\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)

# cancel: strict — only match explicit task/plan cancellation phrases
_CANCEL_ZH = re.compile(r"取消任务|取消计划|终止任务|不要做了|停止任务")
_CANCEL_EN = re.compile(
    r"^\s*(cancel\s+(task|plan)|abort|terminate)\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)

_START_ZH = re.compile(r"开始执行|执行计划|按计划执行|开始这个计划|运行计划")
_START_EN = re.compile(
    r"^\s*(start\s+plan|run\s+plan|execute\s+plan|proceed\s+with\s+plan)\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)

_INSERT_ZH = re.compile(
    r"^(先|先帮我|加一步|增加一步|插入|在当前任务|改成先|补充)[\s\S]+",
)
_INSERT_EN = re.compile(
    r"^\s*(first\s+|add a step\s+|insert\s+|before that\s+|also\s+|change it to first\s+)",
    re.IGNORECASE,
)

_CONFIRM_ZH = re.compile(r"^\s*(确认|同意|批准|可以执行|按计划执行)\s*[.!?。！？]?\s*$")
_CONFIRM_EN = re.compile(
    r"^\s*(confirm|approve|yes\s+proceed|go\s+ahead)\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)

_ACTIVE_STATUSES = frozenset((
    PlanStatus.DRAFT,
    PlanStatus.RUNNING,
    PlanStatus.WAITING_USER,
))


# ---------------------------------------------------------------------------
# detect_plan_chat_intent
# ---------------------------------------------------------------------------


def detect_plan_chat_intent(
    user_text: str,
) -> Tuple[str, Optional[str]]:
    """Detect plan control intent from user chat text.

    Returns ``(intent, instruction_text)`` where *instruction_text* is the
    actionable text for ``insert_step``, or ``None`` for other intents.
    """
    text = user_text.strip()

    # Slash commands → never intercept
    if text.startswith("/"):
        return INTENT_NONE, None

    if len(text) < 2:
        return INTENT_NONE, None

    # ── Priority: cancel > confirm > pause > resume/start > insert ──────

    if _CANCEL_ZH.search(text) or _CANCEL_EN.search(text):
        return INTENT_CANCEL, None

    if _CONFIRM_ZH.search(text) or _CONFIRM_EN.search(text):
        return INTENT_CONFIRM, None

    if _PAUSE_ZH.search(text) or _PAUSE_EN.search(text):
        return INTENT_PAUSE, None

    if _RESUME_ZH.search(text) or _RESUME_EN.search(text):
        return INTENT_RESUME, None

    if _START_ZH.search(text) or _START_EN.search(text):
        return INTENT_START, None

    if _INSERT_ZH.match(text) or _INSERT_EN.match(text):
        return INTENT_INSERT_STEP, text

    return INTENT_NONE, None


# ---------------------------------------------------------------------------
# handle_plan_chat_control
# ---------------------------------------------------------------------------


async def handle_plan_chat_control(
    session_id: str,
    user_text: str,
) -> Optional[str]:
    """Detect and execute plan control from chat. Returns reply text or None.

    Returns ``None`` if the message is not a plan control intent or no
    active plan is found, so the caller can proceed with normal chat.
    """
    try:
        intent, instruction_text = detect_plan_chat_intent(user_text)
        if intent == INTENT_NONE:
            return None

        store = get_plan_store()
        executor = get_plan_executor()

        # Find active plan for this session (no cross-session fallback)
        active_plans = []
        for status in _ACTIVE_STATUSES:
            plans = await store.list_plans(
                session_id=session_id, status=status, limit=5,
            )
            active_plans.extend(plans)

        if not active_plans:
            return None

        # Pick the most recent active plan
        active_plans.sort(key=lambda p: p.updated_at, reverse=True)
        plan = active_plans[0]

        # ── Dispatch ─────────────────────────────────────────────────────
        if intent == INTENT_PAUSE:
            ok = await executor.pause_plan(plan.plan_id)
            return (
                "已暂停当前计划。你可以继续插入要求，或说「继续执行」恢复。"
                if ok else "暂停失败：计划可能不在运行状态。"
            )

        if intent == INTENT_RESUME:
            ok = await executor.resume_plan(plan.plan_id)
            return (
                "已确认并继续执行当前计划。"
                if ok else "恢复失败：计划可能不在等待状态。"
            )

        if intent == INTENT_START:
            try:
                started = await executor.start_plan(plan.plan_id)
                return (
                    "已开始执行当前计划。你可以在右侧「计划」面板查看步骤进度，也可以随时说「暂停」或「取消计划」。"
                    if started else "启动失败：计划可能已在运行。"
                )
            except KeyError:
                return None

        if intent == INTENT_CANCEL:
            ok = await executor.cancel_plan(plan.plan_id)
            return (
                "已取消当前计划，未完成步骤不会继续执行。"
                if ok else "取消失败。"
            )

        if intent == INTENT_CONFIRM:
            # Resume acts as confirmation for risk-gated plans/steps
            ok = await executor.resume_plan(plan.plan_id)
            return (
                "已确认并继续执行当前计划。"
                if ok else "确认失败：计划可能不在等待状态。"
            )

        if intent == INTENT_INSERT_STEP:
            if not instruction_text:
                return None
            plan_obj = await store.get_plan(plan.plan_id)
            if plan_obj is None:
                return None

            original_text = ""
            if plan_obj.metadata:
                original_text = plan_obj.metadata.get("original_user_text", "")

            step_data = build_inserted_step(
                instruction_text,
                plan_title=plan_obj.title,
                original_user_text=original_text,
            )
            after_step_id = plan_obj.current_step_id
            try:
                await store.add_step(
                    plan.plan_id,
                    title=step_data["title"],
                    description=step_data["description"],
                    agent_id=step_data.get("agent_id"),
                    metadata=step_data.get("metadata"),
                    after_step_id=after_step_id,
                )
                return "已把你的新要求插入当前计划，并会按当前进度继续执行。"
            except (KeyError, ValueError) as exc:
                logger.warning("task_plan_chat_control: insert failed: %s", exc)
                return f"插入失败：{exc}"

        return None

    except Exception:  # noqa: BLE001
        logger.warning("task_plan_chat_control: error", exc_info=True)
        return None
