# -*- coding: utf-8 -*-
"""Work Experience Layer — Governance Service.

Provides administrative operations for work experience cards:
- Experience level transitions (new -> observed -> mature -> deprecated)
- Legacy status transitions (candidate -> approved -> rejected -> archived) — backward compatible
- Listing by level or status
- Deduplication and merging
- Maturity-based ranking

Does NOT auto-publish skills or change tool permissions.
"""

import logging
from typing import Optional

from hubos.core.work_experience.schemas import (
    ExperienceLevel,
    WorkExperience,
    WorkExperienceScope,
    WorkExperienceStatus,
    WorkExperienceStore,
)

logger = logging.getLogger(__name__)


class WorkExperienceService:
    """
    Administrative service for work experience governance.

    All methods that modify cards go through this service to maintain
    governance invariants and audit logging.
    """

    def __init__(self, store: WorkExperienceStore) -> None:
        self._store = store

    # ---- Experience Level transitions (new maturity model) ----

    def promote_to_observed(self, experience_id) -> bool:
        """Promote a new card to observed level."""
        return self._store.update_experience_level(
            experience_id,
            ExperienceLevel.OBSERVED,
        )

    def promote_to_mature(self, experience_id) -> bool:
        """Promote an observed card to mature level."""
        return self._store.update_experience_level(
            experience_id,
            ExperienceLevel.MATURE,
        )

    def demote_to_observed(self, experience_id) -> bool:
        """Demote a mature card to observed level."""
        return self._store.update_experience_level(
            experience_id,
            ExperienceLevel.OBSERVED,
        )

    def demote_to_new(self, experience_id) -> bool:
        """Demote a card to new level."""
        return self._store.update_experience_level(
            experience_id,
            ExperienceLevel.NEW,
        )

    def mark_deprecated(self, experience_id) -> bool:
        """Mark a card as deprecated (excluded from retrieval)."""
        return self._store.update_experience_level(
            experience_id,
            ExperienceLevel.DEPRECATED,
        )

    def update_maturity_score(self, experience_id, score: float) -> bool:
        """Update a card's maturity score."""
        return self._store.update_maturity_score(experience_id, score)

    # ---- Legacy Status transitions (backward compatible) ----

    def approve(self, experience_id) -> bool:
        """Approve a candidate card. Also promotes to observed if new."""
        card = self._store.get(experience_id)
        if card and card.experience_level == ExperienceLevel.NEW:
            self._store.update_experience_level(
                experience_id,
                ExperienceLevel.OBSERVED,
            )
        return self._store.update_status(
            experience_id,
            WorkExperienceStatus.APPROVED,
        )

    def reject(self, experience_id) -> bool:
        """Reject a candidate or approved card. Marks as deprecated."""
        self._store.update_experience_level(
            experience_id,
            ExperienceLevel.DEPRECATED,
        )
        return self._store.update_status(
            experience_id,
            WorkExperienceStatus.REJECTED,
        )

    def archive(self, experience_id) -> bool:
        """Archive a card (any non-terminal status)."""
        return self._store.update_status(
            experience_id,
            WorkExperienceStatus.ARCHIVED,
        )

    def reactivate(self, experience_id) -> bool:
        """Reactivate a rejected card back to candidate for re-review."""
        return self._store.update_status(
            experience_id,
            WorkExperienceStatus.CANDIDATE,
        )

    # ---- Listing ----

    def list_by_status(
        self,
        status: WorkExperienceStatus,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all cards with the given governance status."""
        all_cards = self._store.list_all(include_disabled=include_disabled)
        return [c for c in all_cards if c.status == status]

    def list_by_level(
        self,
        level: ExperienceLevel,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all cards with the given experience level."""
        all_cards = self._store.list_all(include_disabled=include_disabled)
        return [c for c in all_cards if c.experience_level == level]

    def list_candidates(
        self,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all candidate (not yet reviewed) cards."""
        return self.list_by_status(
            WorkExperienceStatus.CANDIDATE,
            include_disabled=include_disabled,
        )

    def list_approved(
        self,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all approved cards."""
        return self.list_by_status(
            WorkExperienceStatus.APPROVED,
            include_disabled=include_disabled,
        )

    def list_rejected(
        self,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all rejected cards."""
        return self.list_by_status(
            WorkExperienceStatus.REJECTED,
            include_disabled=include_disabled,
        )

    def list_archived(
        self,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all archived cards."""
        return self.list_by_status(
            WorkExperienceStatus.ARCHIVED,
            include_disabled=include_disabled,
        )

    def list_new(self, include_disabled: bool = False) -> list[WorkExperience]:
        """List all new experience level cards."""
        return self.list_by_level(
            ExperienceLevel.NEW,
            include_disabled=include_disabled,
        )

    def list_observed(
        self,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all observed experience level cards."""
        return self.list_by_level(
            ExperienceLevel.OBSERVED,
            include_disabled=include_disabled,
        )

    def list_mature(
        self,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all mature experience level cards."""
        return self.list_by_level(
            ExperienceLevel.MATURE,
            include_disabled=include_disabled,
        )

    def list_deprecated(
        self,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List all deprecated experience level cards."""
        return self.list_by_level(
            ExperienceLevel.DEPRECATED,
            include_disabled=include_disabled,
        )

    # ---- Similar experience finding ----

    def find_similar(
        self,
        trigger_hint_prefix: str,
        keywords: Optional[list[str]] = None,
        exclude_id: Optional = None,
    ) -> list[WorkExperience]:
        """
        Find similar experiences for potential merging/updating.

        Uses the store's find_similar method for efficient lookup.
        """
        return self._store.find_similar(
            trigger_hint_prefix,
            keywords,
            exclude_id,
        )

    def find_existing_for_update(
        self,
        context,
        keywords: list[str],
    ) -> Optional[WorkExperience]:
        """
        Find an existing experience that should be updated rather than creating new.

        Looks for similar trigger_hint and keyword overlap.
        Returns the most mature matching experience, if found.
        """
        # Build trigger hint from context
        task_input = context.task_input or {}
        if not task_input:
            return None

        first_key = next(iter(task_input.keys()), None)
        if not first_key:
            return None

        first_val = task_input[first_key]
        # Use 8 chars to avoid cutting Chinese words mid-character
        # "思考一下如何让别的局" (10) cuts "局域网" in half
        # "思考一下如何" (8) is a clean semantic boundary
        trigger_hint_prefix = (
            f"{first_key}:{str(first_val)[:8].lower().replace(' ', '_')}"
        )

        similar = self.find_similar(trigger_hint_prefix, keywords)
        if not similar:
            return None

        # Return the most mature one
        return max(
            similar,
            key=lambda c: c.experience_level.retrieval_weight(),
        )

    # ---- Deduplication ----

    def find_duplicates(
        self,
        card: WorkExperience,
        similarity_threshold: float = 0.7,
    ) -> list[WorkExperience]:
        """
        Find potential duplicate cards for a given card.

        Two cards are considered potential duplicates if they share a trigger_hint
        prefix AND have high keyword overlap (Jaccard >= similarity_threshold).

        Args:
            card: The reference card.
            similarity_threshold: Minimum Jaccard similarity for duplicate判定 (0.0–1.0).

        Returns:
            List of candidate duplicate cards (excluding the card itself).
        """
        all_cards = self._store.list_all(include_disabled=False)
        duplicates = []

        card_keywords = set(card.trigger_keywords)
        for other in all_cards:
            if other.experience_id == card.experience_id:
                continue
            # Same trigger_hint prefix (first 10 chars)
            if not other.trigger_hint or not card.trigger_hint:
                continue
            if not other.trigger_hint.startswith(
                card.trigger_hint[:10],
            ) and not card.trigger_hint.startswith(other.trigger_hint[:10]):
                continue

            # Keyword overlap check
            other_keywords = set(other.trigger_keywords)
            if not card_keywords or not other_keywords:
                continue

            intersection = len(card_keywords & other_keywords)
            union = len(card_keywords | other_keywords)
            jaccard = intersection / union if union > 0 else 0.0

            if jaccard >= similarity_threshold:
                duplicates.append(other)

        return duplicates

    def merge_into(
        self,
        source_id,
        target_id,
    ) -> bool:
        """
        Merge source card into target card and archive the source.

        Merging combines:
        - what_worked: union of both cards' what_worked
        - what_failed: union of both cards' what_failed
        - guidance: longer guidance wins
        - avoidance: longer avoidance wins
        - trigger_keywords: union of both sets
        - applicability_tags: union of both sets
        - hit_count: sum of both counts
        - effective_count: sum of both counts
        - recommended_tool_order: union preserving order
        - recommended_workflow: union preserving order
        - applicable_task_types: union of both sets

        Args:
            source_id: ID of the card to merge FROM (will be archived)
            target_id: ID of the card to merge INTO

        Returns:
            True if merge was successful, False otherwise.
        """
        source = self._store.get(source_id)
        target = self._store.get(target_id)
        if source is None or target is None:
            return False

        # Combine fields
        combined_what_worked = list(
            set(target.what_worked) | set(source.what_worked),
        )
        combined_what_failed = list(
            set(target.what_failed) | set(source.what_failed),
        )
        combined_keywords = list(
            set(target.trigger_keywords) | set(source.trigger_keywords),
        )
        combined_tags = list(
            set(target.applicability_tags) | set(source.applicability_tags),
        )

        # Longer guidance/avoidance wins
        guidance = (
            target.guidance
            if len(target.guidance) >= len(source.guidance)
            else source.guidance
        )
        avoidance = (
            target.avoidance
            if len(target.avoidance) >= len(source.avoidance)
            else source.avoidance
        )

        # Combine new fields
        combined_tool_order = list(
            dict.fromkeys(
                target.recommended_tool_order + source.recommended_tool_order,
            ),
        )
        combined_workflow = list(
            dict.fromkeys(
                target.recommended_workflow + source.recommended_workflow,
            ),
        )
        combined_task_types = list(
            set(target.applicable_task_types)
            | set(source.applicable_task_types),
        )
        combined_pattern = (
            target.usage_pattern_summary
            if len(target.usage_pattern_summary)
            >= len(source.usage_pattern_summary)
            else source.usage_pattern_summary
        )

        # Update target
        target.what_worked = combined_what_worked
        target.what_failed = combined_what_failed
        target.guidance = guidance
        target.avoidance = avoidance
        target.trigger_keywords = combined_keywords
        target.applicability_tags = combined_tags
        target.hit_count = target.hit_count + source.hit_count
        target.effective_count = (
            target.effective_count + source.effective_count
        )
        # New fields
        target.recommended_tool_order = combined_tool_order
        target.recommended_workflow = combined_workflow
        target.applicable_task_types = combined_task_types
        target.usage_pattern_summary = combined_pattern
        # Mark that target supersedes source
        target.supersedes_experience_id = source.experience_id

        self._store.save(target)
        self.archive(source_id)

        logger.info(
            "Merged work experience card %s into %s",
            source_id,
            target_id,
        )
        return True

    # ---- List compression helpers ----

    # Generic phrases to filter from what_worked (too vague to be actionable).
    # These are matched at the START of the string to catch "Handled chat request: X" patterns.
    # An item is removed ONLY if it starts with a generic prefix AND is short enough
    # that the "specific" part is just minor variation.
    _GENERIC_WORKED_PREFIXES = (
        (
            "handled chat request",
            20,
        ),  # "Handled chat request" + ~20 chars of specific content
        ("delivered a response", 30),
        ("response summary", 30),
    )

    def _is_generic_phrase(self, item: str) -> bool:
        """Check if an item is a generic chat phrase (too vague to be actionable).

        Returns True for items like:
        - "Handled chat request" (no specific content)
        - "Delivered a response in console via agent default" (generic wrapper)
        - "Response summary: ..." (just a label)

        Returns False for items that START with a generic prefix but have
        significant specific content after it, like:
        - "Handled chat request: 思考一下如何让别的局域网电脑使用你" → False (has specific content)
        """
        lowered = item.lower()
        for prefix, min_len in self._GENERIC_WORKED_PREFIXES:
            if lowered.startswith(prefix):
                # Keep if the item is longer than prefix + meaningful content threshold
                if len(item) > min_len:
                    return False  # Has specific content, keep it
                return True  # Just the generic phrase, remove it
        # Also remove items that are pure generic wrappers (via agent / in console pattern)
        # but only if they have no specific content after the generic label
        if len(item) < 50 and any(
            g in lowered
            for g in [
                "via agent default",
                "in console via agent",
                "response summary:",
            ]
        ):
            return True
        return False

    # Generic continuation patterns that indicate the prefix had no real content
    _GENERIC_CONTINUATION_PREFIXES = frozenset(
        [
            "in ",
            "via ",
            "on ",
            "at ",
            "with ",
            "to ",
            "for ",
            "in console",
            "via agent",
            "in channel",
        ],
    )

    def _strip_generic_prefix(self, item: str) -> str:
        """Strip generic chat prefixes from a worked item to get the specific content.

        Examples:
        - "Handled chat request: 分析这个CSV文件" → "分析这个CSV文件"
        - "Delivered a response in console via agent default" → "" (empty = generic continuation)
        - "Used pandas read_csv with encoding detection" → "pandas read_csv"
        """
        lowered = item.lower()
        for prefix, _ in self._GENERIC_WORKED_PREFIXES:
            if lowered.startswith(prefix):
                stripped = item[len(prefix) :].strip()
                # Strip leading punctuation (colon, dash, etc.) after the prefix
                while stripped and stripped[0] in ":-,;. ":
                    stripped = stripped[1:].strip()
                # If starts with a generic continuation (not real content), discard
                if any(
                    stripped.lower().startswith(cp)
                    for cp in self._GENERIC_CONTINUATION_PREFIXES
                ):
                    return ""
                # If nothing meaningful left after prefix, return empty string
                if len(stripped) < 3:
                    return ""
                return stripped
        # Handle other generic patterns
        for pattern in ["response summary:", "via agent", "in console"]:
            idx = lowered.find(pattern)
            if idx >= 0:
                result = item[:idx].strip()
                if len(result) < 3:
                    return ""
                return result
        return item

    def _merge_and_compress_list(
        self,
        existing: list[str],
        new_items: list[str],
        max_items: int = 5,
        filter_generic: bool = False,
    ) -> list[str]:
        """
        Merge and compress a list with bounded growth.

        Strategy:
        1. Combine existing + new
        2. If filter_generic=True, strip generic prefixes and remove empty results
        3. Deduplicate preferring specific (shorter) over generic (longer)
        4. Sort by length ascending (shorter = more specific = more valuable)
        5. Cap at max_items

        Args:
            existing: Current list items
            new_items: New items to merge in
            max_items: Maximum items to keep
            filter_generic: Whether to filter/strip generic chat phrases

        Returns:
            Compressed, deduplicated list
        """
        # Step 1: combine
        combined = list(existing) + list(new_items)

        # Step 2: strip generic prefixes if requested, keeping the specific content
        if filter_generic:
            stripped: list[str] = []
            for item in combined:
                clean = self._strip_generic_prefix(item)
                if clean:  # Only keep items that have meaningful content left
                    stripped.append(clean)
            combined = stripped

        # Step 3: deduplicate — prefer specific (shorter) over generic (longer).
        # Sort by length ASC so shorter/specific items come first.
        # When checking A vs B: if A is in B, keep A (A is the specific one).
        combined.sort(key=lambda x: len(x))
        result: list[str] = []
        for item in combined:
            # Only add if no existing result item contains this item as substring
            # (meaning this item is not a less-specific version of something already kept)
            if not any(item in kept for kept in result):
                result.append(item)

        # Step 4: cap at max_items
        return result[:max_items]

    def _distill_guidance(
        self,
        existing_guidance: str,
        new_strategy: str,
        what_worked: list[str],
        what_failed: list[str],
        max_len: int = 120,
    ) -> str:
        """
        Distill a concise, imperative guidance string.

        Strategy:
        - Prefer derived imperative from what_worked (concise, actionable)
        - Only use new_strategy if it's very concise (under 60 chars) and actionable
        - Cap at max_len; never exceed even if both sources suggest longer
        """
        candidates: list[str] = []

        # Prefer the imperative derived from what_worked (most reliable signal)
        concrete_worked = [
            w for w in what_worked if not self._is_generic_phrase(w)
        ]
        if concrete_worked:
            concrete_worked.sort(key=lambda x: -len(x))
            # Build imperative: "Use X → Y → Z"
            tools = []
            for w in concrete_worked[:3]:
                tool = self._extract_action_noun(w)
                if tool:
                    tools.append(tool)
            if tools:
                imperative = " → ".join(tools)
                # Only add if it actually fits in guidance budget
                if len(imperative) <= max_len:
                    candidates.append(imperative)

        # Only use new_strategy if it's concise and actionable (under 60 chars).
        # This prevents long chat reflections from bloating the guidance.
        if new_strategy and 15 <= len(new_strategy) <= 60:
            cleaned = new_strategy.strip()
            if any(c in cleaned for c in "→:;,."):
                candidates.append(cleaned)
            elif len(cleaned) <= max_len:
                candidates.append(cleaned)

        # If no candidates, keep existing if short enough
        if not candidates:
            if existing_guidance and len(existing_guidance) <= max_len:
                return existing_guidance
            return ""

        # Return shortest candidate (most imperative/direct), strictly capped
        candidates.sort(key=lambda x: len(x))
        return candidates[0][:max_len]

    def _extract_action_noun(self, phrase: str) -> str:
        """
        Extract the core action/noun from a worked item phrase.

        Examples:
        - "pandas read_csv with encoding detection" → "pandas read_csv"
        - "Used chardet for encoding" → "chardet"
        - "Delivered a response in console" → "response"
        """
        phrase = phrase.strip()
        # Remove common prefixes
        for prefix in [
            "Used ",
            "Used ",
            "Used ",
            "Handled ",
            "Delivered ",
            "Completed ",
        ]:
            if phrase.startswith(prefix):
                phrase = phrase[len(prefix) :]
        # Truncate at "with", "for", "in", "via" (secondary clauses)
        for delim in [" with ", " for ", " in ", " via "]:
            if delim in phrase:
                phrase = phrase.split(delim)[0].strip()
        # Keep first 40 chars max
        if len(phrase) > 40:
            phrase = phrase[:37] + "..."
        return phrase

    def _merge_avoidance(
        self,
        existing_avoidance: str,
        root_cause: str,
        what_failed: list[str],
        max_items: int = 3,
    ) -> str:
        """
        Merge and compress avoidance guidance.

        Builds "Avoid: X | Avoid: Y | Avoid: Z" format, capped at max_items.
        Handles embedded "Don't:" patterns in what_failed by splitting them.
        """
        avoidance_parts: list[str] = []

        # Add root cause first (most important)
        if root_cause and len(root_cause) >= 3:
            avoidance_parts.append(f"Avoid: {root_cause}")

        # Add what_failed items, splitting on embedded "Don't:" patterns
        for item in what_failed[:max_items]:
            # Split on embedded "Don't:" patterns to avoid malformed output
            parts = item.split("Don't:")
            for i, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                # First part gets "Avoid:" prefix, rest get "Avoid: Don't:"
                prefix = "Avoid:" if i == 0 else "Avoid: Don't: "
                if not any(part in ap for ap in avoidance_parts):
                    avoidance_parts.append(f"{prefix}{part}")

        # If we have an existing avoidance, parse and include it (avoid duplicates)
        if existing_avoidance:
            # Split on " | " to get individual parts
            for part in existing_avoidance.split(" | "):
                part = part.strip()
                if part.startswith("Avoid:"):
                    # Deduplicate
                    content = part[6:].strip()
                    if not any(content in ap for ap in avoidance_parts):
                        avoidance_parts.append(part)

        return " | ".join(avoidance_parts[:max_items])

    def _merge_tool_order(
        self,
        existing_tools: list[str],
        execution_trace: list[dict],
        max_items: int = 5,
    ) -> list[str]:
        """Merge tool order, dedup, preserve order, cap at max_items."""
        seen = set(existing_tools)
        result = list(existing_tools)
        for step in execution_trace:
            tool = step.get("tool") or step.get("worker") or ""
            if tool and tool not in seen:
                seen.add(tool)
                result.append(tool)
        return result[:max_items]

    def _merge_workflow(
        self,
        existing_steps: list[str],
        execution_trace: list[dict],
        max_items: int = 5,
    ) -> list[str]:
        """Merge workflow steps, dedup, preserve order, cap at max_items."""
        seen = set(existing_steps)
        result = list(existing_steps)
        for i, step in enumerate(execution_trace[:10]):
            tool = step.get("tool") or step.get("worker") or f"step_{i}"
            success = step.get("success", True)
            step_desc = (
                f"{len(result)+1}. {tool}"
                if success
                else f"{len(result)+1}. {tool} (failed)"
            )
            if step_desc not in seen:
                seen.add(step_desc)
                result.append(step_desc)
        return result[:max_items]

    def update_existing_experience(
        self,
        existing_id,
        report,
        context,
    ) -> bool:
        """
        Update an existing experience with new observations from a completed task.

        Instead of creating a new card, updates the existing one with compression
        to prevent unbounded growth of what_worked / what_failed / guidance.
        Merging strategy:
        - Deduplicate: remove items that are substrings of others
        - Filter generic chat phrases (e.g. "Handled chat request")
        - Keep most specific (longest) items within size limits
        - guidance is distilled into a concise imperative directive

        Args:
            existing_id: ID of the experience to update
            report: ReflectionReport from the completed task
            context: TaskContext for the task

        Returns:
            True if update was successful, False otherwise.
        """
        existing = self._store.get(existing_id)
        if existing is None:
            return False

        # Merge what_worked with compression
        merged_worked = self._merge_and_compress_list(
            existing.what_worked,
            report.what_worked,
            max_items=5,
            filter_generic=True,
        )

        # Merge what_failed with compression
        merged_failed = self._merge_and_compress_list(
            existing.what_failed,
            report.what_failed,
            max_items=3,
            filter_generic=False,
        )

        # Distill guidance: build a concise imperative from merged data
        new_guidance = self._distill_guidance(
            existing.guidance,
            report.next_time_strategy or "",
            merged_worked,
            merged_failed,
        )

        # Merge avoidance
        new_avoidance = self._merge_avoidance(
            existing.avoidance,
            report.root_cause,
            report.what_failed,
        )

        # Merge tool order (dedup, max 5)
        new_tool_order = self._merge_tool_order(
            existing.recommended_tool_order,
            context.execution_trace or [],
            max_items=5,
        )

        # Merge workflow (dedup, max 5)
        new_workflow = self._merge_workflow(
            existing.recommended_workflow,
            context.execution_trace or [],
            max_items=5,
        )

        # Update maturity score (increase based on effective use)
        new_maturity = existing.maturity_score
        if report.confidence >= 0.7:
            new_maturity = min(100.0, existing.maturity_score + 10.0)
        elif report.confidence >= 0.5:
            new_maturity = min(100.0, existing.maturity_score + 5.0)
        else:
            new_maturity = max(0.0, existing.maturity_score - 2.0)

        # Apply updates
        existing.what_worked = merged_worked
        existing.what_failed = merged_failed
        existing.guidance = new_guidance
        existing.avoidance = new_avoidance
        existing.recommended_tool_order = new_tool_order
        existing.recommended_workflow = new_workflow
        existing.maturity_score = new_maturity

        # Update success rate estimate
        total_uses = existing.hit_count + 1
        if total_uses > 0:
            existing.success_rate_estimate = (
                existing.effective_count / total_uses
            )

        # Update experience level if maturity is high enough
        if (
            new_maturity >= 80.0
            and existing.experience_level == ExperienceLevel.OBSERVED
        ):
            existing.experience_level = ExperienceLevel.MATURE
        elif (
            new_maturity >= 40.0
            and existing.experience_level == ExperienceLevel.NEW
        ):
            existing.experience_level = ExperienceLevel.OBSERVED

        self._store.save(existing)

        logger.info(
            "Updated existing experience %s, new_maturity=%.1f, level=%s",
            existing_id,
            new_maturity,
            existing.experience_level.value,
        )
        return True

    # ---- Quality Score (legacy compatibility) ----

    def quality_score(self, card: WorkExperience) -> float:
        """
        Compute the legacy quality score for a card.

        Formula: confidence * (1 + hit_count/10 + effective_count/5)

        This is the score shown in the admin UI quality-score field
        and used by GET /cards/{id}/quality-score and GET /stats.
        """
        return card.confidence * (
            1.0 + card.hit_count / 10.0 + card.effective_count / 5.0
        )

    # ---- Maturity ranking ----

    def maturity_score_calc(self, card: WorkExperience) -> float:
        """
        Compute maturity score for ranking.

        Combines:
        - confidence (0-1)
        - hit_count / effective_count ratio
        - maturity_score field
        - experience_level weight
        """
        level_weight = card.experience_level.retrieval_weight()
        maturity_norm = card.maturity_score / 100.0
        effective_ratio = card.effective_ratio()

        return (
            level_weight * 0.4
            + maturity_norm * 0.3
            + effective_ratio * 0.2
            + card.confidence * 0.1
        )

    def top_cards(
        self,
        cards: list[WorkExperience],
        top_k: int = 5,
    ) -> list[WorkExperience]:
        """Return the top-k cards sorted by maturity score descending."""
        scored = [(self.maturity_score_calc(c), c) for c in cards]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:top_k]]

    # ---- Bulk operations ----

    def auto_promote_observed(
        self,
        min_maturity_score: float = 60.0,
    ) -> int:
        """
        Auto-promote observed cards to mature if they meet maturity threshold.

        Returns the number of cards promoted.
        """
        observed = self.list_observed()
        promoted = 0
        for card in observed:
            if card.maturity_score >= min_maturity_score:
                if self.promote_to_mature(card.experience_id):
                    promoted += 1
        return promoted

    def auto_approve_high_quality(
        self,
        min_confidence: float = 0.75,
        min_effective_uses: int = 3,
    ) -> int:
        """
        Auto-approve candidate cards that meet quality thresholds.

        Candidates are promoted to APPROVED if:
        - confidence >= min_confidence
        - effective_count >= min_effective_uses

        Returns the number of cards approved.
        """
        candidates = self.list_candidates()
        approved_count = 0
        for card in candidates:
            if (
                card.confidence >= min_confidence
                and card.effective_count >= min_effective_uses
            ):
                if self.approve(card.experience_id):
                    approved_count += 1
        return approved_count
