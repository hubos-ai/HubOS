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
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from .retriever_v4 import CardRetriever, MatchResult
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


@dataclass
class PreExecuteResult:
    """Result of pre_execute: card + match status for display."""

    card: Optional[WorkflowCard] = None
    status: str = ""  # matched / no_match / model_unavailable / model_call_failed / invalid_output
    task_type: str = ""
    elapsed_ms: int = 0


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
    ) -> PreExecuteResult:
        """
        Called before task execution. Classify task, find matching card.

        Returns PreExecuteResult with card, status, task_type, elapsed_ms.
        """
        try:
            result = self._retriever.get_or_suggest(user_message)

            if result.card:
                self._session_card_id = result.card.card_id
                self._session_task_type = result.card.task_type
                logger.info(
                    "WorkExperience v4: matched card",
                    extra={
                        "card_id": result.card.card_id,
                        "task_type": result.card.task_type,
                        "executions": result.card.executions,
                        "session_id": session_id,
                    },
                )
                return PreExecuteResult(
                    card=result.card,
                    status=result.status,
                    task_type=result.task_type,
                    elapsed_ms=result.elapsed_ms,
                )

            # No match — store suggestion for later card creation
            if result.suggestion:
                self._session_task_type = result.suggestion.get("new_type", "")
                self._session_card_id = None
                logger.info(
                    "WorkExperience v4: no match, new type suggested",
                    extra={"new_type": self._session_task_type},
                )
            return PreExecuteResult(
                status=result.status,
                task_type=result.task_type or self._session_task_type,
                elapsed_ms=result.elapsed_ms,
            )

        except Exception as exc:
            logger.warning("WorkExperience v4 pre_execute failed: %s", exc)
            return PreExecuteResult(status="model_call_failed")

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
            existing_card = self._store.get_by_task_type(
                self._session_task_type,
            )

        # LLM reflection
        reflection = self._reflect_with_llm(turns, existing_card)

        if reflection is None:
            logger.debug(
                "No reflection extracted from %d turns",
                buf_count,
            )
            return None

        knowledge_candidate_count = self._write_knowledge_candidates(
            reflection,
            session_id=session_id,
            agent_id=agent_id,
        )

        # Update or create card
        if existing_card:
            card = self._merge_reflection_into_card(
                existing_card,
                reflection,
                session_id=session_id,
                agent_id=agent_id,
                turn_count=buf_count,
            )
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
                "knowledge_candidates": knowledge_candidate_count,
            }
        else:
            # Create new card
            card = self._create_card_from_reflection(
                reflection,
                session_id=session_id,
                agent_id=agent_id,
                turn_count=buf_count,
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
                    "knowledge_candidates": knowledge_candidate_count,
                }

        return None

    @staticmethod
    def _write_knowledge_candidates(
        reflection: dict,
        *,
        session_id: str = "",
        agent_id: str = "default",
    ) -> int:
        """Write factual knowledge candidates to memory/knowledge_pending."""
        candidates = reflection.get("knowledge_candidates", [])
        if not isinstance(candidates, list) or not candidates:
            return 0
        try:
            from hubos.core.knowledge_maintenance import (
                write_pending_candidates,
            )

            workspace_dir = os.environ.get("HUBOS_WORKING_DIR")
            if not workspace_dir:
                workspace_dir = str(
                    Path.home() / ".hubos" / "workspaces" / "default",
                )
            written = write_pending_candidates(
                candidates,
                workspace_dir=workspace_dir,
                session_id=session_id,
                agent_id=agent_id,
            )
            return len(written)
        except Exception:
            logger.warning(
                "Knowledge candidate writing failed; continuing",
                exc_info=True,
            )
            return 0

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
            context_parts.append(
                f"Round {i + 1}:\nUser: {q}\nAssistant: {r}\n",
            )
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
            "分析以下工作过程，提取可复用的实战经验。\n\n"
            f"{context_text}\n"
            f"{existing_info}\n\n"
            "## 质量标准（最重要）\n\n"
            "每一条 pitfalls 和 success_patterns 必须满足：\n"
            "- 包含具体的技术细节（文件路径、命令、参数值、错误信息、API端点）\n"
            "- 用自然语言写清楚因果关系和解决方法\n"
            "- 下次遇到同类任务可以直接照做\n"
            "- pitfalls 和 success_patterns 必须是**纯字符串**，不要用 dict/object 格式\n\n"
            "### 好的例子（必须达到这个水平）：\n"
            '- pitfalls: "MiniMax用Anthropic格式base_url会404，必须用OpenAI格式api.minimax.chat/v1"\n'
            '- pitfalls: "session_id可能不一致，console内部ID和Chat URL的chatId不匹配，API过滤后无数据"\n'
            '- success_patterns: "批量数据导入后必须抽样读回验证，API返回code=0不代表数据正确"\n'
            '- success_patterns: "CSS dark mode问题涉及多个类，需逐一补全颜色定义"\n\n'
            "### 坏的例子（禁止输出这类内容）：\n"
            '- pitfalls: "未检查命令参数可能导致错误" ← 太泛，没有具体细节\n'
            '- pitfalls: "忽略配置问题" ← 废话，没有因果\n'
            '- pitfalls: "因为X → 导致Y → 解决方法是Z" ← 禁止用模板填空格式！用自然语言写\n'
            '- pitfalls: {"问题现象": "...", "根因": "..."} ← 禁止用dict格式！必须是纯字符串\n'
            '- success_patterns: "仔细检查确保全覆盖" ← 不是经验，是常识\n'
            '- success_patterns: "逐项验证结果" ← 太抽象\n\n'
            "## 数量限制\n\n"
            "- pitfalls: 最多 8 条（只保留最重要的）\n"
            "- success_patterns: 最多 8 条\n"
            "- workflow: 最多 10 步\n"
            "- 宁可少写也不要写废话。2条高质量 > 10条泛泛而谈\n\n"
            "## 输出格式\n\n"
            "1. task_type: 任务类型（简洁中文名，10字内。已有卡片则用已有的类型名）\n"
            "2. description: 一句话描述核心内容\n"
            "3. workflow: 标准工作流程步骤（字符串数组，按顺序。已有卡片则只改需要调整的步骤）\n"
            "4. tools: 工具及使用要点（对象，值是字符串。必须写具体用法：参数值、命令示例）\n"
            "5. pitfalls: 踩过的坑（字符串数组。用自然语言写，不要用模板。去重，合并已有的）\n"
            "6. success_patterns: 验证有效的方法（字符串数组。用自然语言写，不要用模板。去重，合并已有的）\n"
            "7. has_lessons: 是否有符合上述质量标准的经验（bool）\n"
            "8. experience_type: 从以下分类中选一个：\n"
            "   general, customer_development, web_search, code_fix, ui_design,\n"
            "   deployment, data_import, tool_usage, system_debug,\n"
            "   agent_workflow, knowledge_memory\n"
            "9. entities: 提取3-5个关键实体（保留原文形式）；包括国家、工具、API、平台名、项目名、业务对象（字符串数组）\n"
            "10. knowledge_candidates: 事实型知识候选（数组，可为空）。只提取稳定事实/API/配置/工具状态/系统结构，"
            "不要提取方法论经验（方法论归经验卡片）。每项格式：\n"
            "    {\n"
            '      "title": "原子事实标题",\n'
            '      "domain": "business|tools|dev|ui|system|general",\n'
            '      "entities": ["关键实体"],\n'
            '      "tags": ["标签"],\n'
            '      "links": ["[[相关知识标题]]"],\n'
            '      "confidence": "high|medium|low",\n'
            '      "summary": "一句话事实",\n'
            '      "evidence": ["证据或来源"],\n'
            '      "use_when": ["什么时候使用"],\n'
            '      "details": ["补充细节"]\n'
            "    }\n\n"
            "## 关键规则\n"
            "- 已有卡片：保留仍然有效的内容，只补充新的或修正错误的\n"
            "- 达不到质量标准的内容不要写。宁可少写也不要写废话\n"
            "- 闲聊/简单问答/纯查信息：has_lessons: false，其他字段留空\n"
            "- knowledge_candidates 只放事实，不放流程经验；包含 token/api_key/password/cookie 等敏感信息时不要输出\n"
            "- 所有数组的元素必须是纯字符串，禁止嵌套对象或模板格式\n\n"
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
                    line
                    for line in lines
                    if not line.strip().startswith("```")
                )

            parsed = json.loads(text)

            # Sanitize: ensure all list fields are strings (not dicts)
            for key in ("pitfalls", "success_patterns", "workflow"):
                items = parsed.get(key, [])
                if isinstance(items, list):
                    sanitized = []
                    for item in items:
                        if isinstance(item, dict):
                            # Flatten dict to readable string
                            sanitized.append(
                                ". ".join(str(v) for v in item.values() if v),
                            )
                        elif isinstance(item, str):
                            sanitized.append(item)
                    parsed[key] = sanitized

            # Cap list sizes to prevent card bloat
            for key in ("pitfalls", "success_patterns"):
                if len(parsed.get(key, [])) > 8:
                    parsed[key] = parsed[key][:8]
            if len(parsed.get("workflow", [])) > 10:
                parsed["workflow"] = parsed["workflow"][:10]

            # Sanitize experience_type
            _valid_types = {
                "general",
                "customer_development",
                "web_search",
                "code_fix",
                "ui_design",
                "deployment",
                "data_import",
                "tool_usage",
                "system_debug",
                "agent_workflow",
                "knowledge_memory",
            }
            et = parsed.get("experience_type", "general")
            if not isinstance(et, str) or et not in _valid_types:
                parsed["experience_type"] = "general"

            # Sanitize entities
            raw_ent = parsed.get("entities", [])
            clean_ent: list[str] = []
            if isinstance(raw_ent, list):
                for e in raw_ent:
                    if isinstance(e, str) and e.strip():
                        if e.strip() not in clean_ent:
                            clean_ent.append(e.strip())
            parsed["entities"] = clean_ent[:8]

            # Sanitize factual knowledge candidates. These are written to a
            # pending inbox later; never directly merged into formal knowledge.
            raw_candidates = parsed.get("knowledge_candidates", [])
            clean_candidates: list[dict[str, Any]] = []
            if isinstance(raw_candidates, list):
                for item in raw_candidates[:5]:
                    if not isinstance(item, dict):
                        continue
                    title = item.get("title", "")
                    summary = item.get("summary", "")
                    if isinstance(title, str) and isinstance(summary, str):
                        if title.strip() and summary.strip():
                            clean_candidates.append(item)
            parsed["knowledge_candidates"] = clean_candidates

            if not parsed.get("has_lessons", True) and not clean_candidates:
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
        session_id: str = "",
        agent_id: str = "default",
        turn_count: int = 0,
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
                card.success_patterns,
                new_success,
            )

        # Merge experience_type: overwrite when new is specific (not general)
        new_et = reflection.get("experience_type", "")
        if new_et and new_et != "general":
            card.experience_type = new_et
        elif not card.experience_type:
            card.experience_type = "general"

        # Merge entities: union, deduplicate, max 8
        new_entities = reflection.get("entities", [])
        if new_entities:
            merged = list(card.entities)
            for e in new_entities:
                if (
                    isinstance(e, str)
                    and e.strip()
                    and e.strip() not in merged
                ):
                    merged.append(e.strip())
            card.entities = merged[:8]

        # Update metadata
        card.executions += 1
        card.last_executed_at = _utcnow()
        card.updated_at = _utcnow()

        # Traceability: preserve first ref, update last ref
        if session_id:
            card.last_ref_session_id = session_id
            card.source_turn_count = turn_count
            if session_id not in card.source_sessions:
                card.source_sessions.append(session_id)

        return card

    @staticmethod
    def _create_card_from_reflection(
        reflection: dict,
        session_id: str = "",
        agent_id: str = "default",
        turn_count: int = 0,
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
            experience_type=reflection.get("experience_type", "general"),
            entities=reflection.get("entities", []),
            ref_session_id=session_id,
            ref_agent_id=agent_id,
            last_ref_session_id=session_id,
            source_turn_count=turn_count,
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
            if item_prefix.startswith(ex_prefix) or ex_prefix.startswith(
                item_prefix,
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
