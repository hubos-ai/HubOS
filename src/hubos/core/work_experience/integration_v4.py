# -*- coding: utf-8 -*-
"""Work Experience v4 — Integration layer.

Core flow:
  pre_execute:  classify task → match card → inject guidance
  post_chat_turn:  buffer turns → detect completion → reflect → UPDATE card

One card per task type. Updated, never duplicated.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from .retriever_v4 import CardRetriever
from .schemas_v4 import WorkflowCard, _slugify, _utcnow
from .store_v4 import CardStore

logger = logging.getLogger(__name__)

# Completion signals in assistant responses
_TASK_COMPLETE_PATTERNS = re.compile(
    r"搞定了|完成了|commit|全绿|测试通过|已发送|已创建|已导入|已更新"
    r"|done|finished|committed|deployed|merged|成功"
    r"|✅|✓|🎉",
    re.IGNORECASE,
)

# Max turns to keep in buffer
_MAX_BUFFER_SIZE = 50


class WorkExperienceInterceptor:
    """
    Bridges the Runner/Orchestrator with the v4 card system.

    Key design: update existing cards, don't create duplicates.
    """

    _instance: Optional[WorkExperienceInterceptor] = None

    def __init__(self, store: Optional[CardStore] = None) -> None:
        self._store = store or CardStore()
        self._retriever = CardRetriever(self._store)
        self._turn_buffer: list[dict[str, str]] = []
        # Session state: track matched card for current session
        self._session_card_id: Optional[str] = None
        self._session_task_type: Optional[str] = None
        self._last_promote_time: float = 0

    @classmethod
    def get_instance(cls) -> WorkExperienceInterceptor:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    # ====================================================================
    # Pre-execute: classify + match + inject
    # ====================================================================

    def pre_execute(
        self,
        user_message: str,
        session_id: str = "",
    ) -> Optional[WorkflowCard]:
        """
        Called before task execution. Classify task, find matching card.

        Returns matched card for injection, or None.
        """
        try:
            card, suggestion = self._retriever.get_or_suggest(user_message)

            if card:
                self._session_card_id = card.card_id
                self._session_task_type = card.task_type
                logger.info(
                    "WorkExperience v4: matched card",
                    extra={
                        "card_id": card.card_id,
                        "task_type": card.task_type,
                        "executions": card.executions,
                        "session_id": session_id,
                    },
                )
                return card

            # No match — store suggestion for later card creation
            if suggestion:
                self._session_task_type = suggestion.get("new_type", "")
                self._session_card_id = None
                logger.info(
                    "WorkExperience v4: no match, new type suggested",
                    extra={"new_type": self._session_task_type},
                )
            return None

        except Exception as exc:
            logger.warning("WorkExperience v4 pre_execute failed: %s", exc)
            return None

    # ====================================================================
    # Post-execute: buffer turns, reflect on completion, update card
    # ====================================================================

    def post_chat_turn(
        self,
        *,
        session_id: str,
        user_input: str,
        assistant_response: str,
        channel: str = "console",
        agent_id: str = "default",
        execution_time_ms: int = 0,
    ) -> Optional[dict[str, Any]]:
        """
        Buffer chat turn. On task completion, reflect and update card.
        """
        query = (user_input or "").strip()
        response = (assistant_response or "").strip()
        if not query or not response:
            return None

        # Buffer this turn
        self._turn_buffer.append(
            {
                "user_input": query,
                "assistant_response": response[:1000],
                "channel": channel,
                "agent_id": agent_id,
            },
        )
        if len(self._turn_buffer) > _MAX_BUFFER_SIZE:
            self._turn_buffer = self._turn_buffer[-_MAX_BUFFER_SIZE:]

        # Check if task just completed
        result = None
        if self._detect_completion(response):
            result = self._flush_and_update(
                session_id=session_id,
                agent_id=agent_id,
            )

        return result

    # ====================================================================
    # Internal: completion detection
    # ====================================================================

    @staticmethod
    def _detect_completion(assistant_response: str) -> bool:
        """Check if the response signals task completion."""
        return bool(_TASK_COMPLETE_PATTERNS.search(assistant_response))

    # ====================================================================
    # Internal: reflect + update card
    # ====================================================================

    def _flush_and_update(
        self,
        session_id: str = "",
        agent_id: str = "default",
    ) -> Optional[dict[str, Any]]:
        """
        Task completed. LLM reflects on the buffered conversation,
        then updates (or creates) the matching card.
        """
        if not self._turn_buffer:
            return None

        buf_count = len(self._turn_buffer)
        turns = self._turn_buffer[:]
        self._turn_buffer = []

        # Find existing card or prepare to create new one
        existing_card: Optional[WorkflowCard] = None
        if self._session_card_id:
            existing_card = self._store.get(self._session_card_id)
        elif self._session_task_type:
            existing_card = self._store.get_by_task_type(self._session_task_type)

        # LLM reflection
        reflection = self._reflect_with_llm(turns, existing_card)

        if reflection is None:
            logger.debug(
                "No reflection extracted from %d turns", buf_count,
            )
            return None

        # Update or create card
        if existing_card:
            card = self._merge_reflection_into_card(existing_card, reflection)
            self._store.save(card)
            logger.info(
                "WorkExperience v4: card updated",
                extra={
                    "card_id": card.card_id,
                    "task_type": card.task_type,
                    "executions": card.executions,
                    "turns_processed": buf_count,
                },
            )
            return {
                "action": "updated",
                "card_id": card.card_id,
                "task_type": card.task_type,
            }
        else:
            # Create new card
            card = self._create_card_from_reflection(
                reflection, session_id=session_id,
            )
            if card:
                self._store.save(card)
                self._session_card_id = card.card_id
                self._session_task_type = card.task_type
                logger.info(
                    "WorkExperience v4: new card created",
                    extra={
                        "card_id": card.card_id,
                        "task_type": card.task_type,
                    },
                )
                return {
                    "action": "created",
                    "card_id": card.card_id,
                    "task_type": card.task_type,
                }

        return None

    # ====================================================================
    # LLM reflection
    # ====================================================================

    @staticmethod
    def _reflect_with_llm(
        turns: list[dict[str, str]],
        existing_card: Optional[WorkflowCard] = None,
    ) -> Optional[dict]:
        """
        Ask LLM to reflect on the completed task.

        Returns structured reflection dict, or None if no useful insights.
        """
        # Build context from last 10 turns
        context_parts = []
        for i, t in enumerate(turns[-10:]):
            q = (t.get("user_input") or "")[:200]
            r = (t.get("assistant_response") or "")[:500]
            context_parts.append(f"Round {i + 1}:\nUser: {q}\nAssistant: {r}\n")
        context_text = "\n".join(context_parts)

        # Include existing card content if available
        existing_info = ""
        if existing_card:
            existing_info = (
                f"\n\n已有的经验卡片内容：\n"
                f"任务类型：{existing_card.task_type}\n"
                f"当前工作流程：{json.dumps(existing_card.workflow, ensure_ascii=False)}\n"
                f"当前工具要点：{json.dumps(existing_card.tools, ensure_ascii=False)}\n"
                f"当前踩坑记录：{json.dumps(existing_card.pitfalls, ensure_ascii=False)}\n"
                f"当前成功经验：{json.dumps(existing_card.success_patterns, ensure_ascii=False)}\n"
            )

        prompt = (
            "你是一个工作经验总结专家。以下是一段完整的工作过程（从开始到完成）。\n\n"
            f"{context_text}\n"
            f"{existing_info}"
            "请分析这段工作，提取以下信息：\n\n"
            "1. task_type: 这属于什么类型的任务？（简洁中文名，10字内。如果已有卡片，用已有的类型名）\n"
            "2. description: 一句话描述这类任务的核心内容\n"
            "3. workflow: 这类任务的标准工作流程步骤（数组，按顺序）\n"
            "   - 如果已有卡片，只修改需要调整的步骤，保持不变的步骤原样保留\n"
            "4. tools: 使用的工具及其使用要点（对象，key=工具名，value=使用方法/注意事项）\n"
            "5. pitfalls: 遇到的坑和需要避免的错误（数组，去重，合并已有的）\n"
            "6. success_patterns: 验证有效的成功方法（数组，去重，合并已有的）\n"
            "7. has_lessons: 是否有可复用的经验（bool）\n\n"
            "重要规则：\n"
            "- 如果已有卡片，保留仍然有效的内容，只补充新的或修正错误的\n"
            "- pitfalls和success_patterns要去重，相似的合并为一条\n"
            "- 如果只是普通闲聊/简单问答/查信息，设 has_lessons: false\n\n"
            "输出JSON，不要解释。"
        )

        try:
            from hubos.core.llm.runtime import get_llm_runtime

            runtime = get_llm_runtime()
            if not runtime.is_available():
                return None

            result = runtime.generate_direct(
                prompt=prompt,
                system_prompt="You are a work experience extraction assistant. Output only valid JSON.",
                temperature=0.2,
                max_tokens=800,
            )
            if not result.success or not result.text:
                return None

            # Parse JSON
            text = result.text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(
                    line for line in lines if not line.strip().startswith("```")
                )

            parsed = json.loads(text)

            if not parsed.get("has_lessons", True):
                return None

            return parsed

        except json.JSONDecodeError:
            logger.warning("LLM reflection returned invalid JSON")
            return None
        except Exception as exc:
            logger.warning("LLM reflection failed: %s", exc)
            return None

    # ====================================================================
    # Merge reflection into existing card
    # ====================================================================

    @staticmethod
    def _merge_reflection_into_card(
        card: WorkflowCard,
        reflection: dict,
    ) -> WorkflowCard:
        """
        Merge LLM reflection into an existing card.

        Strategy: keep existing + add new, deduplicate.
        """
        # Update description if provided and different
        new_desc = reflection.get("description", "")
        if new_desc and len(new_desc) > len(card.description):
            card.description = new_desc

        # Update workflow: prefer LLM's version if provided
        new_workflow = reflection.get("workflow", [])
        if new_workflow:
            card.workflow = new_workflow

        # Merge tools: new tools override, existing preserved
        new_tools = reflection.get("tools", {})
        if new_tools:
            card.tools.update(new_tools)

        # Merge pitfalls: deduplicate by similarity
        new_pitfalls = reflection.get("pitfalls", [])
        if new_pitfalls:
            card.pitfalls = _merge_deduplicate(card.pitfalls, new_pitfalls)

        # Merge success patterns: deduplicate by similarity
        new_success = reflection.get("success_patterns", [])
        if new_success:
            card.success_patterns = _merge_deduplicate(
                card.success_patterns, new_success,
            )

        # Update metadata
        card.executions += 1
        card.last_executed_at = _utcnow()
        card.updated_at = _utcnow()

        return card

    @staticmethod
    def _create_card_from_reflection(
        reflection: dict,
        session_id: str = "",
    ) -> Optional[WorkflowCard]:
        """Create a new WorkflowCard from LLM reflection."""
        task_type = reflection.get("task_type", "")
        if not task_type:
            return None

        card = WorkflowCard(
            task_type=task_type,
            description=reflection.get("description", ""),
            workflow=reflection.get("workflow", []),
            tools=reflection.get("tools", {}),
            pitfalls=reflection.get("pitfalls", []),
            success_patterns=reflection.get("success_patterns", []),
            executions=1,
            last_executed_at=_utcnow(),
            source_sessions=[session_id] if session_id else [],
        )
        return card

    # ====================================================================
    # Backward compat: record_effective_uses (no-op in v4)
    # ====================================================================

    def record_effective_uses(self, cards: Any) -> None:
        """No-op in v4. Card quality is tracked via executions + content."""
        pass

    def post_execute(self, task: Any = None) -> None:
        """No-op in v4. Use post_chat_turn instead."""
        pass


# ======================================================================
# Helpers
# ======================================================================

def _merge_deduplicate(
    existing: list[str],
    new_items: list[str],
) -> list[str]:
    """
    Merge two string lists, deduplicating by prefix similarity.

    Two items are "similar" if one starts with the other (after
    stripping whitespace and lowercasing the first 20 chars).
    When similar, keep the LONGER (more detailed) version.
    """
    result = list(existing)

    for item in new_items:
        item_clean = item.strip()
        if not item_clean:
            continue

        # Check if similar to any existing
        item_prefix = item_clean.lower()[:20]
        replaced = False
        for i, existing_item in enumerate(result):
            ex_prefix = existing_item.strip().lower()[:20]
            # If one is a prefix of the other, they're "similar"
            if (
                item_prefix.startswith(ex_prefix)
                or ex_prefix.startswith(item_prefix)
            ):
                # Keep the longer one (more detail)
                if len(item_clean) > len(existing_item.strip()):
                    result[i] = item_clean
                replaced = True
                break

        if not replaced:
            result.append(item_clean)

    return result


# ======================================================================
# Singleton accessor (backward compat with old integration.py)
# ======================================================================

_interceptor: Optional[WorkExperienceInterceptor] = None


def get_work_experience_interceptor() -> WorkExperienceInterceptor:
    """Get or create the singleton interceptor."""
    global _interceptor
    if _interceptor is None:
        _interceptor = WorkExperienceInterceptor()
    return _interceptor
