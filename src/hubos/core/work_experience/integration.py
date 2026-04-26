"""Work Experience Layer integration with ExecutionOrchestrator.

Bypass-read integration: retrieves experience cards before task execution and
attaches them to the Task context, without modifying prompt, tools, or skills.

Usage::

    from hubos.core.work_experience.integration import get_work_experience_interceptor

    interceptor = get_work_experience_interceptor()
    cards = interceptor.pre_execute(task)
    # cards are now attached to task.work_experience_cards

================================================================================
trigger_hint Naming Standard
================================================================================

trigger_hint is built by the retriever from the task_input dict using:

    f"{first_key}:{str(first_value)[:10].lower().replace(' ', '_')}"

Task input dict keys (defined in _build_task_input):

    input_text      User's raw input text  ← FIRST KEY (primary trigger source)
    workflow        Workflow name (e.g. "one_person_default")
    session_id      Session identifier
    channel         Channel name (e.g. "api", "slack")

Therefore, the primary trigger_hint for real tasks will always start with
"input_text:" followed by the first 10 chars of the user's input.

Example:
    User input: "send a message to the #general channel saying..."
    task_input first key: "input_text"
    first 10 chars of value: "send a mes"
    task_trigger_hint: "input_text:send_a_mes"

Card trigger_hint design rule:
    Cards created from real task runs should use "input_text:" as the key prefix.
    The value should be a short semantic prefix of the action/verb (e.g. "input_text:send",
    "input_text:crawl", "input_text:schedule"). The card hint must be == or shorter than
    the task hint for prefix matching to succeed (CARD.startswith(TASK_HINT)).

Prefix matching:
    "input_text:send".startswith("input_text:send")         → True  (card == task prefix)
    "input_text:send_message".startswith("input_text:send")  → True  (card longer)
    "input_text:send".startswith("input_text:schedule")      → False (different prefix)

When to use keyword-only fallback:
    If no card has a matching trigger_hint prefix, retrieve_for_task falls back
    to keyword-only retrieval. This is intentional — the system degrades gracefully
    rather than returning zero results for valid but non-prefix-matching tasks.
"""

import logging
import re
from typing import Any, Optional
from uuid import uuid4

from hubos.core.execution.task_store import Task
from hubos.core.infra.feature_flags import get_feature_flags
from hubos.core.work_experience.keyword_utils import extract_semantic_keywords

logger = logging.getLogger(__name__)

# ============================================================================
# Lesson extraction patterns for chat-turn reflection enrichment
# ============================================================================

# HubOS tool names — used to detect tool mentions in response text.
# Keep this ordered so recommended_tool_order is deterministic.
_HUBOS_TOOL_NAMES = (
    "execute_shell_command", "read_file", "write_file", "edit_file",
    "grep_search", "glob_search", "browser_use", "webReader",
    "web_search_prime", "web_crawl", "find_customer_leads",
    "delegate_task", "spawn_subagents", "coordinate_workflow",
    "memory_search", "recall_long_term", "recall_session",
    "tavily_search", "desktop_screenshot", "himalaya",
    "super_crawler", "xcrawl", "send_file_to_user", "get_current_time",
)

# Chinese keywords that signal universal/always rules → scope = GLOBAL
_GLOBAL_SCOPE_INDICATORS_ZH = frozenset({
    "每次", "总是", "所有", "永远", "通用", "标准", "任何", "一律",
    "方法论", "最佳实践", "核心策略", "通用方法",
})

# English keywords → scope = GLOBAL
_GLOBAL_SCOPE_INDICATORS_EN = frozenset({
    "always", "every time", "all", "universal", "standard", "any",
    "best practice", "methodology",
})


def _truncate_lesson(text: str, max_len: int = 80) -> str:
    """Truncate a lesson string, preserving meaning."""
    text = text.strip()
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _detect_scope_from_lessons(what_worked: list[str], what_failed: list[str]) -> str:
    """
    Determine the appropriate scope for a card based on its lesson content.

    Returns one of: "global", "user", "session".

    Rules:
    - If lessons contain universal/always patterns → "global"
    - If lessons contain domain-specific reusable knowledge → "user"
    - Otherwise → "session"
    """
    all_lessons = " ".join(what_worked + what_failed).lower()

    # Check for global scope indicators
    for kw in _GLOBAL_SCOPE_INDICATORS_ZH:
        if kw in all_lessons:
            return "global"
    for kw in _GLOBAL_SCOPE_INDICATORS_EN:
        if kw in all_lessons:
            return "global"

    # If we have concrete, actionable lessons (not session-specific), use USER
    if what_worked or what_failed:
        return "user"

    return "session"


def _extract_tools_from_response(response: str) -> list[str]:
    """Extract HubOS tool names mentioned in the response text."""
    found = []
    for tool in _HUBOS_TOOL_NAMES:
        if tool in response:
            found.append(tool)
    return found


def _extract_lessons_from_response(response: str) -> dict[str, list[str]]:
    """
    Extract structured, actionable lessons from an assistant response.

    Scans for:
    - ✅ success indicators → what_worked
    - ❌ failure indicators → what_failed
    - ⚠️ warning indicators → what_failed
    - Chinese rule patterns (必须/务必/记得 vs 不要/避免/切记)
    - English rule patterns (must/always vs avoid/don't/never)
    - Bold text containing key rules

    Returns:
        {"what_worked": [...], "what_failed": [...], "tools": [...]}
    """
    what_worked: list[str] = []
    what_failed: list[str] = []
    seen_worked: set[str] = set()
    seen_failed: set[str] = set()

    # Regex to strip leading list prefixes: "1. ", "- ", "* ", "→ "
    _RE_LIST_PREFIX = re.compile(r"^[\d]+[.)]\s*|^[-*•→]\s*")

    lines = response.split("\n")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Strip leading list/bullet prefix for emoji detection
        # "1. ✅ Foo" → "✅ Foo", "- ⚠️ Bar" → "⚠️ Bar"
        cleaned = _RE_LIST_PREFIX.sub("", stripped).strip()
        lower = cleaned.lower()

        # --- Emoji-prefixed lines ---
        if cleaned.startswith("✅") or cleaned.startswith("✔"):
            content = cleaned.lstrip("✅✔•→ ").strip()
            if len(content) >= 3 and content not in seen_worked:
                what_worked.append(_truncate_lesson(content))
                seen_worked.add(content)
            continue

        if cleaned.startswith("❌"):
            content = cleaned.lstrip("❌•→ ").strip()
            if len(content) >= 3 and content not in seen_failed:
                what_failed.append(_truncate_lesson(content))
                seen_failed.add(content)
            continue

        if cleaned.startswith("⚠️") or cleaned.startswith("⚠"):
            content = cleaned.lstrip("⚠️⚠•→ ").strip()
            if len(content) >= 3 and content not in seen_failed:
                what_failed.append(_truncate_lesson(content))
                seen_failed.add(content)
            continue

        # --- Chinese rule patterns (on cleaned line) ---
        # Positive rules (must do / remember)
        for kw in ("必须", "务必", "记得", "每次", "记得要"):
            if kw in lower:
                clean = cleaned.lstrip("- •*→ ").strip()
                if len(clean) >= 3 and clean not in seen_worked:
                    what_worked.append(_truncate_lesson(clean))
                    seen_worked.add(clean)
                break

        # Negative rules (avoid / don't)
        for kw in ("不要", "避免", "切记不", "务必不", "不能", "无法"):
            if kw in lower:
                clean = cleaned.lstrip("- •*→ ").strip()
                # Skip if already classified as what_worked (line has both positive & negative)
                if len(clean) >= 3 and clean not in seen_failed and clean not in seen_worked:
                    what_failed.append(_truncate_lesson(clean))
                    seen_failed.add(clean)
                break

        # --- English rule patterns (on cleaned line) ---
        for kw in ("must ", "always ", "ensure "):
            if lower.startswith(kw):
                clean = cleaned.strip()
                if len(clean) >= 5 and clean not in seen_worked:
                    what_worked.append(_truncate_lesson(clean))
                    seen_worked.add(clean)
                break

        for kw in ("avoid ", "don't ", "never ", "do not "):
            if lower.startswith(kw):
                clean = cleaned.strip()
                if len(clean) >= 5 and clean not in seen_failed:
                    what_failed.append(_truncate_lesson(clean))
                    seen_failed.add(clean)
                break

    # --- Bold text rule extraction ---
    for match in re.finditer(r"\*\*(.{5,80}?)\*\*", response):
        bold_text = match.group(1).strip()
        bold_lower = bold_text.lower()
        # Only capture bold text that contains rule-like keywords
        has_rule_keyword = any(
            kw in bold_lower
            for kw in (
                "必须", "不要", "避免", "切记", "重要", "关键", "注意",
                "务必", "核心", "最佳", "坑", "不在",
                "must", "avoid", "never", "important", "key", "critical",
                "best", "pitfall", "gotcha",
            )
        )
        if not has_rule_keyword:
            continue

        is_negative = any(
            kw in bold_lower
            for kw in ("不要", "避免", "不可", "不能", "不在", "avoid", "don't", "never")
        )
        if is_negative:
            if bold_text not in seen_failed:
                what_failed.append(_truncate_lesson(bold_text))
                seen_failed.add(bold_text)
        else:
            if bold_text not in seen_worked:
                what_worked.append(_truncate_lesson(bold_text))
                seen_worked.add(bold_text)

    # --- Tool mention extraction ---
    tools = _extract_tools_from_response(response)

    return {"what_worked": what_worked, "what_failed": what_failed, "tools": tools}


# Lazy singleton
_interceptor: Optional["WorkExperienceInterceptor"] = None


class WorkExperienceInterceptor:
    """
    Bypass-read interceptor for work experience retrieval.

    Called before task execution in ExecutionOrchestrator.execute_task().
    Retrieves relevant WorkExperience cards and attaches them to the Task.

    Hard constraints:
    - Does NOT modify prompts, tools, or skills
    - Does NOT participate in execution decisions
    - Results are read-only context attached to the task
    - Entirely controlled by ENABLE_WORK_EXPERIENCE_LAYER flag
    """

    def __init__(self) -> None:
        """Initialize the interceptor with a store, retriever, and reflection engine."""
        from hubos.core.work_experience.store import LocalWorkExperienceStore
        from hubos.core.work_experience.retriever import WorkExperienceRetriever
        from hubos.core.work_experience.service import WorkExperienceService
        from hubos.core.orchestrator.reflection_engine import ReflectionEngine

        self._store = LocalWorkExperienceStore()
        self._retriever = WorkExperienceRetriever(store=self._store, max_results=5)
        self._service = WorkExperienceService(store=self._store)
        self._reflection_engine = ReflectionEngine()

    def pre_execute(self, task: Task) -> list[dict]:
        """
        Retrieve work experience cards before task execution.

        This is the main entry point called by ExecutionOrchestrator.execute_task().

        Behaviour:
        - If ENABLE_WORK_EXPERIENCE_LAYER=false: returns [] immediately (no-op)
        - If no cards found: returns [] (normal)
        - On error: logs warning and returns []

        Args:
            task: The Task to retrieve experiences for.

        Returns:
            List of experience card dicts (same as WorkExperience.model_dump() format).
            Empty list if flag disabled, no matches, or error.
        """
        flags = get_feature_flags()
        if not flags.enable_work_experience_layer:
            logger.debug(
                "Work Experience Layer disabled, skipping retrieval",
                extra={"task_id": task.task_id},
            )
            return []

        try:
            task_input = self._build_task_input(task)
            cards = self._retriever.retrieve_for_task(task_input)

            # Attach to task
            card_dicts = [self._card_to_dict(c) for c in cards]
            task.work_experience_cards = card_dicts

            if cards:
                # Structured debug log with retrieval rationale
                for i, card in enumerate(cards):
                    logger.info(
                        "WorkExperience retrieved for task",
                        extra={
                            "task_id": task.task_id,
                            "card_index": i,
                            "card_id": str(card.experience_id),
                            "card_title": card.title[:80],
                            "card_scope": card.scope.value,
                            "keyword_overlap": self._compute_overlap(task_input, card),
                            "sort_rank": i,
                            "total_retrieved": len(cards),
                            "retrieval_rationale": (
                                f"scope={card.scope.value} "
                                f"keywords_match={self._keywords_match(task_input, card)} "
                                f"trigger_hint={card.trigger_hint}"
                            ),
                        },
                    )
            else:
                logger.debug(
                    "No WorkExperience cards retrieved for task",
                    extra={
                        "task_id": task.task_id,
                        "input_preview": task.input_text[:100],
                    },
                )

            return card_dicts

        except Exception as exc:
            logger.warning(
                "WorkExperience retrieval failed, continuing without cards",
                extra={
                    "task_id": task.task_id,
                    "error": str(exc),
                },
            )
            return []

    @staticmethod
    def _build_task_input(task: Task) -> dict:
        """
        Build the task_input dict passed to retrieve_for_task.

        Key ordering matters: the FIRST key in the dict is used to build the
        trigger_hint. Keys are deliberately ordered so that "input_text" is first
        (see module-level trigger_hint naming standard documentation).

        Standard keys:
            input_text   — user's raw input (first key, primary trigger source)
            workflow     — workflow preset name
            session_id   — session identifier
            channel      — channel name (e.g. "api", "slack")

        Returns:
            dict with keys in the above order.
        """
        return {
            "input_text": task.input_text,
            "workflow": task.requested_workflow,
            "session_id": task.session_id or "",
            "channel": task.channel or "",
        }

    @staticmethod
    def _card_to_dict(card) -> dict:
        """Convert a WorkExperience to a JSON-serializable dict."""
        import dataclasses
        import uuid as uuid_mod
        from datetime import datetime

        def _serialize(v):
            if isinstance(v, uuid_mod.UUID):
                return str(v)
            if isinstance(v, datetime):
                return v.isoformat()
            if isinstance(v, (list, tuple)):
                return [_serialize(i) for i in v]
            if isinstance(v, dict):
                return {kk: _serialize(vv) for kk, vv in v.items()}
            return v

        data = dataclasses.asdict(card)
        return _serialize(data)

    @staticmethod
    def _keywords_match(task_input: dict, card) -> int:
        """Count how many task input keywords overlap with card keywords."""
        kw_set = set(extract_semantic_keywords(task_input.values()))
        card_kw = {k.lower() for k in card.trigger_keywords}
        return len(kw_set & card_kw)

    @staticmethod
    def _compute_overlap(task_input: dict, card) -> int:
        return WorkExperienceInterceptor._keywords_match(task_input, card)

    @staticmethod
    def _extract_keywords(task_input: dict) -> list[str]:
        """Extract keywords from task_input for similarity matching."""
        return extract_semantic_keywords(task_input.values())

    def record_effective_uses(self, cards: list[dict]) -> None:
        """
        Record that the given cards were effectively used in a prompt injection.

        Called by the orchestrator after successful LLM generation with injected cards.

        Args:
            cards: List of experience card dicts that were injected.
        """
        card_ids = [c.get("experience_id") for c in cards if c.get("experience_id")]
        logger.debug(
            "WE_EFFECTIVE_USE_BATCH",
            extra={
                "card_count": len(card_ids),
                "card_ids": card_ids,
                "card_titles": [c.get("title", "")[:60] for c in cards if c.get("title")],
            },
        )
        for card in cards:
            exp_id = card.get("experience_id")
            if not exp_id:
                continue
            try:
                self._store.record_effective_use(exp_id)
            except Exception as exc:
                logger.warning(
                    "Failed to record effective use for card %s: %s",
                    exp_id,
                    exc,
                )

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
        Reflect on a completed chat turn and persist a candidate card.

        This bridges the real desktop/web chat runtime to the Work Experience
        layer. The user-facing chat path does not flow through the core
        execution orchestrator, so we synthesize a minimal TaskContext here.
        """
        flags = get_feature_flags()
        if not flags.enable_work_experience_layer:
            logger.debug(
                "Work Experience Layer disabled, skipping chat extraction",
                extra={"session_id": session_id, "agent_id": agent_id},
            )
            return None

        query = (user_input or "").strip()
        response = (assistant_response or "").strip()
        if not query or not response:
            logger.debug(
                "Skipping chat extraction because query/response is empty",
                extra={"session_id": session_id, "agent_id": agent_id},
            )
            return None

        trace_id = f"chat-trace-{uuid4()}"
        task_id = f"chat-turn-{uuid4()}"

        from hubos.core.orchestrator.reflection_engine import TaskContext
        from hubos.core.schemas.tasks import TaskResult, TaskStatus as WorkerTaskStatus

        context = TaskContext(
            task_id=task_id,
            session_id=session_id or "",
            trace_id=trace_id,
            task_input={
                "input_text": query,
                "channel": channel or "console",
                "agent_id": agent_id or "default",
                "type": "chat_turn",
            },
            execution_trace=[
                {
                    "stage": "chat",
                    "worker": agent_id or "default",
                    "tool": "chat_reply",
                    "success": True,
                    "status": "completed",
                    "content": response,
                    "confidence": 0.8,
                    "error": None,
                }
            ],
            task_result=TaskResult(
                task_id=task_id,
                status=WorkerTaskStatus.SUCCESS,
                confidence=0.8,
                output_data={"response_text": response},
                artifacts=[],
                error_message=None,
                retry_count=0,
                trace_id=trace_id,
            ),
            execution_time_ms=max(execution_time_ms, 0),
        )

        try:
            report = self._reflection_engine.reflect(context)
            try:
                self._enrich_chat_reflection_report(
                    report=report,
                    user_input=query,
                    assistant_response=response,
                    channel=channel,
                    agent_id=agent_id,
                )
            except Exception as exc:
                logger.warning(
                    "Chat reflection enrichment failed, continuing with raw report",
                    extra={"session_id": session_id, "error": str(exc)},
                )

            # ---- Scope detection: set scope based on lesson content ----
            # Only if enrichment produced lessons (confidence above threshold)
            if report.confidence >= 0.5 and (report.what_worked or report.what_failed):
                scope_hint = _detect_scope_from_lessons(
                    report.what_worked, report.what_failed,
                )
                context.task_input["scope"] = scope_hint

                # Also store detected tools for the extractor
                lessons = _extract_lessons_from_response(response)
                if lessons["tools"]:
                    context.task_input["tools_used"] = lessons["tools"]

            # Build keywords for similarity matching (same logic as retriever)
            keywords = self._extract_keywords(context.task_input)

            # Try to find an existing experience to update
            existing = self._service.find_existing_for_update(context, keywords)

            if existing is not None:
                # Update existing experience instead of creating a new card
                updated = self._service.update_existing_experience(
                    existing.experience_id,
                    report,
                    context,
                )
                logger.info(
                    "WorkExperience card updated from chat turn (found similar)",
                    extra={
                        "session_id": session_id,
                        "experience_id": str(existing.experience_id),
                        "title": existing.title[:80],
                        "updated": updated,
                    },
                )
                return {
                    "experience_id": str(existing.experience_id),
                    "title": existing.title,
                    "status": existing.status.value,
                    "scope": existing.scope.value,
                    "updated": updated,
                }

            # No similar experience found — create a new card
            from hubos.core.work_experience.extractor import WorkExperienceExtractor

            extractor = WorkExperienceExtractor(store=self._store)
            card = extractor.extract(report, context)
            if card is None:
                logger.debug(
                    "No WorkExperience card extracted from chat turn",
                    extra={"session_id": session_id, "task_id": task_id},
                )
                return None

            # Apply compression to new card fields to prevent unbounded growth.
            # This mirrors what update_existing_experience does, but for first creation.
            card.what_worked = self._service._merge_and_compress_list(
                [],
                card.what_worked,
                max_items=5,
                filter_generic=True,
            )
            card.what_failed = self._service._merge_and_compress_list(
                [],
                card.what_failed,
                max_items=3,
                filter_generic=False,
            )
            card.guidance = self._service._distill_guidance(
                "",
                report.next_time_strategy or "",
                card.what_worked,
                card.what_failed,
            )
            card.avoidance = self._service._merge_avoidance(
                "",
                report.root_cause or "",
                report.what_failed or [],
            )

            self._store.save(card)
            logger.info(
                "WorkExperience card saved from chat turn",
                extra={
                    "session_id": session_id,
                    "experience_id": str(card.experience_id),
                    "title": card.title[:80],
                    "status": card.status.value,
                    "scope": card.scope.value,
                },
            )
            return {
                "experience_id": str(card.experience_id),
                "title": card.title,
                "status": card.status.value,
                "scope": card.scope.value,
            }
        except Exception as exc:
            logger.warning(
                "WorkExperience chat extraction failed, continuing",
                extra={
                    "session_id": session_id,
                    "agent_id": agent_id,
                    "error": str(exc),
                },
            )
            return None

    def post_execute(self, task: Task) -> Optional[dict[str, Any]]:
        """
        Reflect on a completed task and persist a candidate WorkExperience card.

        This is the Phase 4 accumulation path described in the rollout plan:
        once a task reaches a terminal state, generate a ReflectionReport and
        extract a durable experience card from it.
        """
        flags = get_feature_flags()
        if not flags.enable_work_experience_layer:
            logger.debug(
                "Work Experience Layer disabled, skipping post-execute extraction",
                extra={"task_id": task.task_id},
            )
            return None

        try:
            context = self._build_reflection_context(task)
            report = self._reflection_engine.reflect(context)

            from hubos.core.work_experience.extractor import WorkExperienceExtractor

            extractor = WorkExperienceExtractor(store=self._store)
            card = extractor.extract(report, context)
            if card is None:
                logger.debug(
                    "No WorkExperience card extracted from reflection",
                    extra={
                        "task_id": task.task_id,
                        "reflection_confidence": report.confidence,
                    },
                )
                return None

            self._store.save(card)
            logger.info(
                "WorkExperience card saved from completed task",
                extra={
                    "task_id": task.task_id,
                    "experience_id": str(card.experience_id),
                    "title": card.title[:80],
                    "status": card.status.value,
                    "scope": card.scope.value,
                },
            )
            return {
                "experience_id": str(card.experience_id),
                "title": card.title,
                "status": card.status.value,
                "scope": card.scope.value,
            }
        except Exception as exc:
            logger.warning(
                "WorkExperience post-execute extraction failed, continuing",
                extra={"task_id": task.task_id, "error": str(exc)},
            )
            return None

    @staticmethod
    def _enrich_chat_reflection_report(
        *,
        report,
        user_input: str,
        assistant_response: str,
        channel: str,
        agent_id: str,
    ) -> None:
        """
        Extract structured, reusable rules from a chat turn.

        Instead of recording "this chat happened", extracts:
        - what_worked: strategies/tools/approaches that succeeded
        - what_failed: pitfalls, errors, things to avoid
        - root_cause: why something failed (if applicable)
        - next_time_strategy: concrete guidance for similar future tasks

        If the response contains no actionable lessons, sets confidence
        below the extraction threshold (0.5) so no card is created.
        """
        response = assistant_response.strip()
        if not response:
            report.confidence = 0.2
            return

        # ---- Extract structured lessons from response ----
        lessons = _extract_lessons_from_response(response)
        worked = lessons["what_worked"]
        failed = lessons["what_failed"]
        tools = lessons["tools"]

        # ---- Quality gate: only create cards with actionable lessons ----
        if not worked and not failed:
            # No actionable lessons → suppress card creation
            report.confidence = 0.3  # Below DEFAULT_MIN_CONFIDENCE=0.5
            return

        # ---- Populate structured fields ----
        report.what_worked = worked[:5]
        report.what_failed = failed[:5]

        if failed:
            report.root_cause = failed[0][:80]

        # Build concrete next_time_strategy
        strategy_parts: list[str] = []
        if worked:
            strategy_parts.append("Do: " + "; ".join(w[:40] for w in worked[:3]))
        if failed:
            strategy_parts.append("Avoid: " + "; ".join(f[:40] for f in failed[:2]))
        report.next_time_strategy = " | ".join(strategy_parts)

        # Boost confidence for lesson-rich responses
        report.confidence = 0.85

        # Store detected tools in task_input for extractor to use
        # (accessed via context.task_input by the caller)

    @staticmethod
    def _build_reflection_context(task: Task):
        """Build ReflectionEngine TaskContext from a terminal Task."""
        from hubos.core.orchestrator.reflection_engine import TaskContext
        from hubos.core.schemas.tasks import TaskResult, TaskStatus as WorkerTaskStatus

        execution_trace = []
        for stage_name, stage_status in task.stage_statuses.items():
            output = stage_status.output or {}
            execution_trace.append(
                {
                    "stage": stage_name,
                    "worker": stage_name,
                    "success": stage_status.status == "completed",
                    "status": stage_status.status,
                    "content": output.get("content", ""),
                    "confidence": output.get("confidence"),
                    "error": stage_status.error,
                }
            )

        task_result = TaskResult(
            task_id=task.task_id,
            status=(
                WorkerTaskStatus.SUCCESS
                if str(task.current_status.value) == "done"
                else WorkerTaskStatus.FAILURE
            ),
            confidence=(task.final_response or {}).get("confidence", 0.0),
            output_data=task.final_response or {},
            artifacts=[],
            error_message=task.failure_reason,
            trace_id=task.trace_id,
        )

        execution_time_ms = 0
        if task.started_at and task.completed_at:
            execution_time_ms = int(
                (task.completed_at - task.started_at).total_seconds() * 1000
            )

        return TaskContext(
            task_id=task.task_id,
            session_id=task.session_id or "",
            trace_id=task.trace_id,
            task_input=WorkExperienceInterceptor._build_task_input(task),
            execution_trace=execution_trace,
            task_result=task_result,
            execution_time_ms=execution_time_ms,
        )


def get_work_experience_interceptor() -> WorkExperienceInterceptor:
    """Get the lazy singleton WorkExperienceInterceptor instance."""
    global _interceptor
    if _interceptor is None:
        _interceptor = WorkExperienceInterceptor()
    return _interceptor
