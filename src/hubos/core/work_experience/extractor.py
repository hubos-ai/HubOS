"""WorkExperienceExtractor — converts ReflectionReport + TaskContext into WorkExperience cards."""

import logging
import re
from typing import Optional

from hubos.core.orchestrator.reflection_engine import TaskContext
from hubos.core.schemas.memory import ReflectionReport
from hubos.core.work_experience.schemas import (
    ExperienceLevel,
    WorkExperience,
    WorkExperienceScope,
)

logger = logging.getLogger(__name__)

# Minimum confidence to emit a card
DEFAULT_MIN_CONFIDENCE = 0.5

# Stopwords for keyword extraction
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "should",
    "could", "may", "might", "must", "can", "this", "that", "these", "those",
    "it", "its", "they", "them", "their", "we", "our", "us", "i", "my",
}


class WorkExperienceExtractor:
    """
    Extracts a WorkExperience card from a completed task's reflection data.

    The extractor applies heuristic rules (no LLM required) to produce a
    structured, retrievable card. It does NOT modify any existing systems.

    Usage::

        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(report, context)
        if card:
            store.save(card)
    """

    def __init__(
        self,
        store,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        """
        Initialize the extractor.

        Args:
            store: A WorkExperienceStore (used only for potential future deduplication).
            min_confidence: Minimum reflection confidence to emit a card.
        """
        self._store = store
        self._min_confidence = min_confidence

    def extract(
        self,
        report: ReflectionReport,
        context: TaskContext,
    ) -> Optional[WorkExperience]:
        """
        Convert a ReflectionReport + TaskContext into a WorkExperience card.

        Returns None if:
        - Reflection confidence is below min_confidence
        - Both what_worked and what_failed are empty

        Args:
            report: ReflectionReport from ReflectionEngine.
            context: TaskContext for the completed task.

        Returns:
            A WorkExperience card, or None.
        """
        # Skip low-confidence reflections
        if report.confidence < self._min_confidence:
            logger.debug(
                "Skipping card: confidence %.2f < threshold %.2f",
                report.confidence,
                self._min_confidence,
            )
            return None

        # Need at least one data point
        if not report.what_worked and not report.what_failed:
            logger.debug("Skipping card: no what_worked or what_failed data")
            return None

        # Determine scope
        scope = self._infer_scope(context)

        # Extract keywords
        keywords = self._extract_keywords(report, context)

        # Build trigger hint
        trigger_hint = self._build_trigger_hint(context)

        # Build title
        title = self._build_title(report, context)

        # Build narrative
        what_happened = self._build_narrative(report)

        # Build guidance
        guidance = self._build_guidance(report)

        # Build avoidance
        avoidance = self._build_avoidance(report)

        # Extract applicability tags
        applicability_tags = self._extract_tags(context)

        # ---- New fields for work guidance model ----

        # Usage pattern summary
        usage_pattern_summary = self._build_usage_pattern_summary(context, report)

        # Recommended tool order (from execution trace)
        recommended_tool_order = self._extract_tool_order(context)

        # Recommended workflow (from execution trace steps)
        recommended_workflow = self._extract_workflow(context)

        # Applicable task types
        applicable_task_types = self._extract_task_types(context)

        # Success rate estimate (from confidence)
        success_rate_estimate = report.confidence

        # Initial maturity score (based on confidence * 100)
        maturity_score = report.confidence * 50.0  # Start at 0-50 range

        card = WorkExperience(
            scope=scope,
            trigger_keywords=keywords,
            trigger_hint=trigger_hint,
            title=title,
            what_happened=what_happened,
            what_worked=list(report.what_worked),
            what_failed=list(report.what_failed),
            guidance=guidance,
            avoidance=avoidance,
            # New work guidance fields
            usage_pattern_summary=usage_pattern_summary,
            recommended_tool_order=recommended_tool_order,
            recommended_workflow=recommended_workflow,
            applicable_task_types=applicable_task_types,
            success_rate_estimate=success_rate_estimate,
            # Metadata
            confidence=report.confidence,
            source_task_id=context.task_id,
            source_session_id=context.session_id,
            source_trace_id=context.trace_id,
            applicability_tags=applicability_tags,
            # Initial maturity model values
            experience_level=ExperienceLevel.NEW,
            maturity_score=maturity_score,
        )

        logger.info(
            "Extracted WorkExperience card",
            extra={
                "experience_id": str(card.experience_id),
                "title": card.title[:60],
                "confidence": card.confidence,
                "scope": card.scope.value,
                "experience_level": card.experience_level.value,
                "keyword_count": len(card.trigger_keywords),
                "tool_order": recommended_tool_order[:3] if recommended_tool_order else [],
            },
        )

        return card

    # ---- Extraction heuristics ----

    def _infer_scope(self, context: TaskContext) -> WorkExperienceScope:
        """Infer the experience scope from task context."""
        # If we have a user_id or project_id in task_input, use it
        task_input = context.task_input or {}
        if task_input.get("scope") in WorkExperienceScope._value2member_map_:
            return WorkExperienceScope(task_input["scope"])
        if task_input.get("user_id"):
            return WorkExperienceScope.USER
        if task_input.get("project_id"):
            return WorkExperienceScope.PROJECT
        if context.session_id:
            return WorkExperienceScope.SESSION
        return WorkExperienceScope.GLOBAL

    def _extract_keywords(
        self, report: ReflectionReport, context: TaskContext
    ) -> list[str]:
        """Extract retrieval keywords from report and context."""
        words: set[str] = set()
        # From task_input values (strings only)
        for value in (context.task_input or {}).values():
            if isinstance(value, str):
                words.update(self._tokenize(value))
        # From what_worked
        for item in report.what_worked:
            words.update(self._tokenize(item))
        # From what_failed
        for item in report.what_failed:
            words.update(self._tokenize(item))
        # Filter stopwords and short tokens
        filtered = {w for w in words if w not in _STOPWORDS and len(w) >= 3}
        # Limit to top 20 by length (prefer longer, more specific tokens)
        sorted_words = sorted(filtered, key=len, reverse=True)
        return sorted_words[:20]

    def _tokenize(self, text: str) -> list[str]:
        """Simple whitespace + punctuation tokenizer."""
        return re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]*", text.lower())

    def _build_trigger_hint(self, context: TaskContext) -> str:
        """Build a compact trigger hint from task_input keys and values."""
        task_input = context.task_input or {}
        keys = list(task_input.keys())
        if not keys:
            return f"task:{context.task_id[:8]}"

        first_key = keys[0]
        first_val = task_input[first_key]
        val_str = str(first_val)[:10] if first_val else ""
        # Sanitize: no spaces, lowercase
        val_str = re.sub(r"\s+", "_", val_str).lower()
        return f"{first_key}:{val_str}"

    def _build_title(
        self, report: ReflectionReport, context: TaskContext
    ) -> str:
        """Build a one-line title from what_worked or task type."""
        if report.what_worked:
            first = report.what_worked[0]
            # Truncate at 80 chars
            if len(first) > 80:
                first = first[:77] + "..."
            return first
        # Fall back to task type from input
        task_type = str(context.task_input.get("type", context.task_input.get("query", "task"))[:80])
        return f"Task: {task_type}"

    def _build_narrative(self, report: ReflectionReport) -> str:
        """Build a short narrative description from the reflection."""
        parts: list[str] = []
        if report.what_worked:
            parts.append("Worked: " + "; ".join(report.what_worked[:3]))
        if report.what_failed:
            parts.append("Failed: " + "; ".join(report.what_failed[:3]))
        if report.root_cause:
            parts.append("Root cause: " + report.root_cause)
        return " | ".join(parts)

    def _build_guidance(self, report: ReflectionReport) -> str:
        """Build actionable guidance from next_time_strategy."""
        guidance_parts: list[str] = []
        if report.next_time_strategy:
            guidance_parts.append(report.next_time_strategy)
        # Add top what_worked items as positive guidance
        if report.what_worked:
            guidance_parts.append("Do: " + "; ".join(report.what_worked[:2]))
        return " ".join(guidance_parts)

    def _build_avoidance(self, report: ReflectionReport) -> str:
        """Build avoidance guidance from what_failed and root_cause."""
        if not report.what_failed:
            return ""
        avoidance_parts: list[str] = []
        if report.root_cause:
            avoidance_parts.append(f"Avoid: {report.root_cause}")
        avoidance_parts.append("Don't: " + "; ".join(report.what_failed[:2]))
        return " ".join(avoidance_parts)

    def _extract_tags(self, context: TaskContext) -> list[str]:
        """Extract applicability tags from execution trace tool names and task_input keys."""
        tags: set[str] = set()
        # From execution_trace tool names
        for step in (context.execution_trace or []):
            tool = step.get("tool") or step.get("worker") or ""
            if tool:
                tags.add(tool)
        # From task_input keys
        tags.update(str(k) for k in (context.task_input or {}).keys())
        # Limit to 15 tags
        return sorted(tags)[:15]

    # ---- New field extraction methods ----

    def _build_usage_pattern_summary(
        self, context: TaskContext, report: ReflectionReport
    ) -> str:
        """
        Build a concise summary of the task pattern this experience applies to.

        Uses the task type from input and the main action from what_worked.
        """
        task_input = context.task_input or {}
        task_type = task_input.get("type", "")

        if not task_type and report.what_worked:
            # Derive from what_worked
            task_type = report.what_worked[0][:50]

        if not task_type:
            task_type = task_input.get("input_text", "")[:50] if task_input.get("input_text") else "general"

        return task_type.strip()

    def _extract_tool_order(self, context: TaskContext) -> list[str]:
        """
        Extract the recommended tool order from execution trace.

        Returns tools in the order they were successfully used.
        """
        tools: list[str] = []
        seen = set()
        for step in (context.execution_trace or []):
            tool = step.get("tool") or step.get("worker") or ""
            if tool and tool not in seen:
                tools.append(tool)
                seen.add(tool)
        return tools[:10]  # Limit to 10 tools

    def _extract_workflow(self, context: TaskContext) -> list[str]:
        """
        Extract the recommended workflow steps from execution trace.

        Returns step descriptions in execution order.
        """
        steps: list[str] = []
        for i, step in enumerate((context.execution_trace or [])[:10]):  # Limit to 10 steps
            tool = step.get("tool") or step.get("worker") or f"step_{i}"
            success = step.get("success", True)
            status = step.get("status", "completed")

            if success or status == "completed":
                steps.append(f"{i+1}. {tool}")
            else:
                steps.append(f"{i+1}. {tool} (failed)")

        return steps

    def _extract_task_types(self, context: TaskContext) -> list[str]:
        """
        Extract applicable task types from task_input.

        Returns the 'type' field if present, plus inferred types from input.
        """
        task_input = context.task_input or {}
        task_types: list[str] = []

        # Explicit type
        if task_input.get("type"):
            task_types.append(str(task_input["type"]))

        # Channel
        if task_input.get("channel"):
            task_types.append(f"channel:{task_input['channel']}")

        # Workflow
        if task_input.get("workflow"):
            task_types.append(f"workflow:{task_input['workflow']}")

        # Extract key verbs from input_text for task type inference
        input_text = task_input.get("input_text", "")
        if input_text:
            # Simple verb extraction for task type
            verbs = ["send", "read", "write", "process", "crawl", "fetch", "extract", "generate", "analyze"]
            input_lower = input_text.lower()
            for verb in verbs:
                if verb in input_lower:
                    task_types.append(f"action:{verb}")

        return list(dict.fromkeys(task_types))[:5]  # Dedupe, limit to 5
