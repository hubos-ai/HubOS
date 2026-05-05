# -*- coding: utf-8 -*-
"""Work Experience v4 — CardRetriever.

Uses LLM (MiniMax M2.7 highspeed) for semantic task-type matching.
No more keyword Jaccard overlap — LLM understands task semantics.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .schemas_v4 import WorkflowCard
from .store_v4 import CardStore

logger = logging.getLogger(__name__)


class CardRetriever:
    """Match user tasks to existing WorkflowCards via LLM classification."""

    def __init__(self, store: CardStore) -> None:
        self._store = store

    def _classify_and_match(
        self,
        user_message: str,
    ) -> tuple[Optional[WorkflowCard], Optional[dict]]:
        """
        Single LLM call: classify task → match card OR suggest new type.

        Returns:
            (matched_card, None) if found
            (None, suggestion_dict) if new type suggested
            (None, None) if LLM unavailable / no useful output
        """
        cards_index = self._store.list_index()

        if not cards_index:
            return None, None

        # Build the classification prompt
        card_list = "\n".join(
            f"  {i + 1}. [{c['task_type']}] {c['description']}"
            for i, c in enumerate(cards_index)
        )

        prompt = (
            "你是一个任务分类器。根据用户的任务描述，判断它属于以下哪个已有任务类型。\n\n"
            f"已有任务类型：\n{card_list}\n\n"
            f"用户任务：{user_message[:500]}\n\n"
            "回复规则：\n"
            '- 如果匹配某个已有类型，输出：{"match": "任务类型名"}\n'
            '- 如果不匹配任何已有类型，输出：{"match": null, "new_type": "建议的新类型名", '
            '"description": "一句话描述"}\n'
            "- 只输出JSON，不要解释"
        )

        try:
            result = self._call_llm(prompt)
            if not result:
                return None, None

            # Parse JSON
            text = result.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(
                    line
                    for line in lines
                    if not line.strip().startswith("```")
                )

            parsed = json.loads(text)
            matched_type = parsed.get("match")

            if matched_type:
                card = self._store.get_by_task_type(matched_type)
                if card:
                    logger.info(
                        "Card matched via LLM",
                        extra={
                            "task_type": matched_type,
                            "card_id": card.card_id,
                            "user_msg_preview": user_message[:80],
                        },
                    )
                    return card, None
                logger.warning(
                    "LLM matched type '%s' but no card found in store",
                    matched_type,
                )

            # No match — LLM suggests a new type (same call, no extra LLM)
            new_type = parsed.get("new_type")
            new_desc = parsed.get("description", "")
            if new_type:
                logger.info(
                    "No card match, LLM suggests new type",
                    extra={
                        "new_type": new_type,
                        "description": new_desc,
                    },
                )
                return None, {
                    "new_type": new_type,
                    "description": new_desc,
                }

            return None, None

        except json.JSONDecodeError:
            logger.warning("LLM returned invalid JSON for card matching")
            return None, None
        except Exception as exc:
            logger.warning("Card matching failed: %s", exc)
            return None, None

    def match(
        self,
        user_message: str,
    ) -> Optional[WorkflowCard]:
        """Convenience wrapper: return matched card only (ignore suggestion)."""
        card, _ = self._classify_and_match(user_message)
        return card

    def get_or_suggest(
        self,
        user_message: str,
    ) -> tuple[Optional[WorkflowCard], Optional[dict]]:
        """
        Match a card OR return a suggestion for a new card.

        Single LLM call — no double invocation.

        Returns:
            (matched_card, None) if found
            (None, suggestion_dict) if new type suggested
            (None, None) if LLM unavailable
        """
        card, suggestion = self._classify_and_match(user_message)

        if card:
            return card, None

        # If we already got a suggestion from the classify call, use it
        if suggestion:
            return None, suggestion

        # No cards in store at all — ask LLM to suggest a new type
        return None, self._suggest_new_type(user_message)

    def _suggest_new_type(self, user_message: str) -> Optional[dict]:
        """Ask LLM to suggest a new task type (only when store is empty)."""
        prompt = (
            "你是一个任务分类器。根据用户任务，建议一个任务类型名和描述。\n\n"
            f"用户任务：{user_message[:500]}\n\n"
            "输出JSON：\n"
            '{"new_type": "简洁的中文类型名(10字内)", '
            '"description": "一句话描述这类任务的核心内容"}\n\n'
            "只输出JSON，不要解释。"
        )
        try:
            result = self._call_llm(prompt)
            if not result:
                return None
            text = result.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(
                    line
                    for line in lines
                    if not line.strip().startswith("```")
                )
            return json.loads(text)
        except Exception:
            return None

    @staticmethod
    def _call_llm(prompt: str) -> Optional[str]:
        """Call MiniMax M2.7 highspeed via LLMRuntime."""
        try:
            from hubos.core.llm.runtime import get_llm_runtime

            runtime = get_llm_runtime()
            if not runtime.is_available():
                return None
            result = runtime.generate_direct(
                prompt=prompt,
                system_prompt="You are a task classifier. Output only valid JSON.",
                temperature=0.1,
                max_tokens=200,
            )
            if result.success and result.text:
                return result.text.strip()
            return None
        except Exception as exc:
            logger.warning("LLM call failed: %s", exc)
            return None
