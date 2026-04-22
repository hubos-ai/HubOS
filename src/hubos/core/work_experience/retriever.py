"""WorkExperienceRetriever — retrieves experience cards by scope, keywords, and trigger hint.

Maturity-based retrieval model:
- All non-deprecated experiences can participate in retrieval (not just approved)
- Scoring considers: relevance, maturity_score, effective_ratio, recent_use
- Experience level determines base weight:
  - new: low weight (0.3)
  - observed: medium weight (0.6)
  - mature: high weight (1.0)
  - deprecated: excluded from retrieval
"""

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from hubos.core.work_experience.schemas import (
    ExperienceLevel,
    WorkExperience,
    WorkExperienceScope,
    WorkExperienceStore,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 5


class WorkExperienceRetriever:
    """
    Retrieves WorkExperience cards matching scope, keywords, and trigger hints.

    Does NOT auto-inject into prompts — callers decide how to use results.

    Matching algorithm (maturity-based):
    1. Filter by scope (if provided)
    2. Filter out disabled and deprecated experiences
    3. Filter by trigger_hint prefix (if provided)
    4. Score each card by multi-dimensional ranking:
       - Relevance: keyword overlap
       - Maturity: experience_level weight * maturity_score
       - Effectiveness: effective_count / hit_count ratio
       - Recency: last_used_at freshness
    5. Return top N results sorted by composite score

    DEPRECATED experiences are excluded from retrieval by default.
    """

    def __init__(
        self,
        store: WorkExperienceStore,
        max_results: int = DEFAULT_MAX_RESULTS,
    ) -> None:
        """
        Initialize the retriever.

        Args:
            store: WorkExperienceStore to query.
            max_results: Maximum number of results to return.
        """
        self._store = store
        self._max_results = max_results

    def _maturity_composite_score(self, card: WorkExperience) -> float:
        """
        Compute a composite maturity-based score.

        Components:
        - Base weight from experience_level (0.0-1.0)
        - Normalized maturity_score (0-100 -> 0-1)
        - Effective ratio bonus (0-0.5)
        - Recency bonus (0-0.3)
        """
        # Base weight from experience level
        level_weight = card.experience_level.retrieval_weight()

        # Normalized maturity score (0-100 -> 0-1)
        maturity_norm = card.maturity_score / 100.0

        # Effective ratio (0-1)
        effective_ratio = card.effective_ratio()

        # Recency bonus (0-0.3) based on days since last_used_at
        recency_bonus = 0.0
        if card.last_used_at:
            days_since = (datetime.now(timezone.utc) - card.last_used_at).days
            # Exponential decay: 0.3 at 0 days, ~0 at 30+ days
            recency_bonus = 0.3 * max(0.0, (1.0 - days_since / 30.0))
        elif card.created_at:
            days_since = (datetime.now(timezone.utc) - card.created_at).days
            recency_bonus = 0.1 * max(0.0, (1.0 - days_since / 30.0))

        # Composite: weighted sum with level as primary factor
        composite = (
            level_weight * 0.5 +           # Experience level (50%)
            maturity_norm * 0.25 +          # Maturity score (25%)
            effective_ratio * 0.15 +        # Effectiveness (15%)
            recency_bonus * 0.1             # Recency (10%)
        )

        return composite

    def retrieve(
        self,
        scope: Optional[WorkExperienceScope] = None,
        keywords: Optional[list[str]] = None,
        trigger_hint: Optional[str] = None,
        include_disabled: bool = False,
        include_deprecated: bool = False,
    ) -> list[WorkExperience]:
        """
        Retrieve experience cards matching the given criteria.

        Args:
            scope: If provided, only return cards at this exact scope.
            keywords: If provided, score by keyword overlap and sort by score desc.
            trigger_hint: If provided, only return cards whose trigger_hint starts with this prefix.
            include_disabled: If False (default), exclude disabled cards.
            include_deprecated: If False (default), exclude deprecated experiences.

        Returns:
            Sorted list of matching WorkExperience cards (max_results limit applied).
            Deprecated and disabled cards are excluded by default.
        """
        # Start with scope filter (or all cards)
        if scope is not None:
            candidates = self._store.list_by_scope(scope, include_disabled=include_disabled)
        else:
            candidates = self._store.list_all(include_disabled=include_disabled)

        # Filter out disabled cards if not included
        if not include_disabled:
            candidates = [c for c in candidates if not c.disabled]

        # Filter out deprecated experiences by default
        if not include_deprecated:
            candidates = [
                c for c in candidates
                if c.experience_level != ExperienceLevel.DEPRECATED
            ]

        # Apply trigger_hint prefix filter
        if trigger_hint:
            candidates = [
                c for c in candidates
                if c.trigger_hint.startswith(trigger_hint)
            ]

        # Score by keyword overlap, then maturity composite
        if keywords:
            kw_set = {k.lower() for k in keywords}
            scored = []
            for card in candidates:
                card_kw = {k.lower() for k in card.trigger_keywords}
                overlap = len(kw_set & card_kw)
                if overlap > 0:
                    maturity = self._maturity_composite_score(card)
                    scored.append((-overlap, -maturity, card))
            scored.sort(key=lambda x: (x[0], x[1]))
            candidates = [c for _, _, c in scored]
        else:
            # Default sort: maturity composite desc
            candidates = self._sort_by_maturity(candidates)

        # Increment hit counts for returned cards
        results = candidates[: self._max_results]
        for card in results:
            try:
                self._store.increment_hit(card.experience_id)
            except Exception as exc:
                logger.warning("Failed to increment hit count: %s", exc)

        if results:
            logger.info(
                "Retrieved %d experience cards (scope=%s, keywords=%s, trigger=%s, deprecated_included=%s)",
                len(results),
                scope.value if scope else "all",
                keywords,
                trigger_hint,
                include_deprecated,
            )

        return results

    def _sort_by_maturity(self, cards: list[WorkExperience]) -> list[WorkExperience]:
        """Sort by maturity composite score descending, then scope priority."""
        def sort_key(card: WorkExperience) -> tuple:
            return (-self._maturity_composite_score(card), WorkExperienceScope.priority(card.scope))
        return sorted(cards, key=sort_key)

    # ---- Legacy compatibility methods ----

    def retrieve_by_status(
        self,
        status_filter: Optional[str] = None,
        scope: Optional[WorkExperienceScope] = None,
        keywords: Optional[list[str]] = None,
        trigger_hint: Optional[str] = None,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """
        Legacy compatibility method - retrieves by legacy status field.

        For new code, use retrieve() with include_deprecated instead.
        This method maps old status-based filtering to new maturity-based filtering.
        """
        # Map legacy status to experience_level filters
        include_deprecated = True  # Include all for status-based queries

        # For approved status, use mature+observed filtering
        # For candidate/new, include all non-deprecated
        return self.retrieve(
            scope=scope,
            keywords=keywords,
            trigger_hint=trigger_hint,
            include_disabled=include_disabled,
            include_deprecated=include_deprecated,
        )

    # ---- Convenience helpers ----

    def retrieve_for_task(
        self,
        task_input: dict,
        scope: Optional[WorkExperienceScope] = None,
    ) -> list[WorkExperience]:
        """
        Retrieve cards for a task based on its input dict.

        trigger_hint matching (see integration.py trigger_hint naming standard):
          Builds task_trigger_hint = f"{first_key}:{str(first_value)[:10].lower().replace(' ', '_')}"
          Then filters cards: c.trigger_hint.startswith(task_trigger_hint)
          CARD hint must be == or longer than TASK hint for a match.

        Prefix matching examples:
          task_hint = "input_text:send_a_mes"
          "input_text:send".startswith("input_text:send_a_mes")         → False
          "input_text:send_a_mes".startswith("input_text:send_a_mes")   → True
          "input_text:send_message".startswith("input_text:send")        → True

        Keyword scoring: Jaccard overlap between task keywords and card.trigger_keywords.
        Maturity score: experience_level weight * maturity_norm * effective_ratio * recency

        Graceful fallback: if no cards match via trigger_hint prefix, falls back
        to keyword-only retrieval. This ensures non-prefix-matching tasks still get
        relevant cards when keywords overlap.

        Args:
            task_input: Task input dict (see _build_task_input key ordering).
            scope: Optional scope filter.

        Returns:
            Matching WorkExperience cards sorted by maturity composite score descending.
        """
        # Extract keywords from string values in task_input
        keywords: list[str] = []
        for value in task_input.values():
            if isinstance(value, str):
                tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]*", value.lower())
                keywords.extend(tokens)

        # Build trigger hint from first key
        trigger_hint: Optional[str] = None
        if task_input:
            first_key = next(iter(task_input.keys()))
            first_val = str(task_input[first_key])[:10]
            trigger_hint = f"{first_key}:{first_val.lower().replace(' ', '_')}"

        # Try with trigger hint first; if no results, fall back to keyword-only
        if trigger_hint:
            results = self.retrieve(
                scope=scope,
                keywords=keywords if keywords else None,
                trigger_hint=trigger_hint,
            )
            if results:
                return results
            # Fall back: trigger hint was too specific — use keywords only
            return self.retrieve(
                scope=scope,
                keywords=keywords if keywords else None,
            )

        return self.retrieve(scope=scope, keywords=keywords if keywords else None)
