# -*- coding: utf-8 -*-
"""Phase 6 tests: Work Experience governance — state machine, deduplication, quality ranking.

Tests:
1. Non-deprecated (new/observed/mature) cards participate in retrieval by default
2. DEPRECATED cards are excluded from default retrieval
3. Deduplication: find_duplicates returns similar cards
4. Merge: two cards can be merged, source deprecated, target updated
5. Level transitions: promote/demote/deprecate work correctly
6. Effective use: record_effective_use increments effective_count and sets last_used_at
7. Quality score: higher confidence and usage → higher score
8. Flag off: no impact on existing behavior
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hubos.core.work_experience import (
    LocalWorkExperienceStore,
    WorkExperienceRetriever,
    WorkExperienceService,
)
from hubos.core.work_experience.schemas import (
    ExperienceLevel,
    WorkExperience,
    WorkExperienceScope,
    WorkExperienceStatus,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_root(tmp_path) -> "Path":
    return tmp_path / "we_governance"


@pytest.fixture
def store(tmp_root) -> LocalWorkExperienceStore:
    return LocalWorkExperienceStore(root=tmp_root)


@pytest.fixture
def mature_card(store: LocalWorkExperienceStore) -> WorkExperience:
    card = WorkExperience(
        scope=WorkExperienceScope.PROJECT,
        trigger_keywords=["csv", "file", "pandas"],
        trigger_hint="type:csv",
        title="CSV Processing",
        what_happened="CSV parsed with pandas",
        what_worked=["pandas read_csv with encoding param"],
        what_failed=[],
        guidance="Detect encoding first",
        avoidance="Don't assume UTF-8",
        confidence=0.85,
        source_task_id="task-mature",
        source_session_id="session-1",
        source_trace_id="trace-1",
        applicability_tags=["pandas", "csv"],
        experience_level=ExperienceLevel.MATURE,
        effective_count=2,
    )
    store.save(card)
    return card


@pytest.fixture
def new_card(store: LocalWorkExperienceStore) -> WorkExperience:
    card = WorkExperience(
        scope=WorkExperienceScope.PROJECT,
        trigger_keywords=["csv", "file", "pandas"],
        trigger_hint="type:csv",
        title="CSV Processing New",
        what_happened="CSV parsed",
        what_worked=["pandas"],
        what_failed=[],
        guidance="Detect encoding",
        avoidance="",
        confidence=0.6,
        source_task_id="task-new",
        source_session_id="session-1",
        source_trace_id="trace-1",
        applicability_tags=["pandas", "csv"],
        experience_level=ExperienceLevel.NEW,
    )
    store.save(card)
    return card


@pytest.fixture
def deprecated_card(store: LocalWorkExperienceStore) -> WorkExperience:
    card = WorkExperience(
        scope=WorkExperienceScope.PROJECT,
        trigger_keywords=["csv", "file"],
        trigger_hint="type:csv",
        title="CSV Deprecated",
        what_happened="CSV",
        what_worked=[],
        what_failed=["encoding issue"],
        guidance="",
        avoidance="",
        confidence=0.4,
        source_task_id="task-deprecated",
        source_session_id="session-1",
        source_trace_id="trace-1",
        applicability_tags=[],
        experience_level=ExperienceLevel.DEPRECATED,
    )
    store.save(card)
    return card


# =============================================================================
# State Machine Tests
# =============================================================================


class TestWorkExperienceStatus:
    """Tests for WorkExperienceStatus enum and transitions."""

    def test_candidate_can_transition_to_approved(self) -> None:
        assert WorkExperienceStatus.CANDIDATE.can_transition_to(
            WorkExperienceStatus.APPROVED,
        )

    def test_candidate_can_transition_to_rejected(self) -> None:
        assert WorkExperienceStatus.CANDIDATE.can_transition_to(
            WorkExperienceStatus.REJECTED,
        )

    def test_candidate_can_transition_to_archived(self) -> None:
        assert WorkExperienceStatus.CANDIDATE.can_transition_to(
            WorkExperienceStatus.ARCHIVED,
        )

    def test_approved_cannot_transition_to_candidate(self) -> None:
        assert not WorkExperienceStatus.APPROVED.can_transition_to(
            WorkExperienceStatus.CANDIDATE,
        )

    def test_approved_can_transition_to_rejected(self) -> None:
        assert WorkExperienceStatus.APPROVED.can_transition_to(
            WorkExperienceStatus.REJECTED,
        )

    def test_rejected_can_transition_back_to_candidate(self) -> None:
        assert WorkExperienceStatus.REJECTED.can_transition_to(
            WorkExperienceStatus.CANDIDATE,
        )

    def test_archived_is_terminal(self) -> None:
        assert not WorkExperienceStatus.ARCHIVED.can_transition_to(
            WorkExperienceStatus.CANDIDATE,
        )
        assert not WorkExperienceStatus.ARCHIVED.can_transition_to(
            WorkExperienceStatus.APPROVED,
        )
        assert not WorkExperienceStatus.ARCHIVED.can_transition_to(
            WorkExperienceStatus.REJECTED,
        )

    def test_status_default_is_candidate(self) -> None:
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="Test",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.5,
            source_task_id="task-test",
            source_session_id="s1",
            source_trace_id="t1",
            applicability_tags=[],
        )
        assert card.status == WorkExperienceStatus.CANDIDATE


# =============================================================================
# Store Status Transition Tests
# =============================================================================


class TestStoreStatusTransitions:
    """Tests for store status transition methods."""

    def test_approve_new(
        self,
        store: LocalWorkExperienceStore,
        new_card: WorkExperience,
    ) -> None:
        result = store.update_status(
            new_card.experience_id,
            WorkExperienceStatus.APPROVED,
        )
        assert result is True
        retrieved = store.get(new_card.experience_id)
        assert retrieved is not None
        assert retrieved.status == WorkExperienceStatus.APPROVED

    def test_reject_new(
        self,
        store: LocalWorkExperienceStore,
        new_card: WorkExperience,
    ) -> None:
        result = store.update_status(
            new_card.experience_id,
            WorkExperienceStatus.REJECTED,
        )
        assert result is True
        retrieved = store.get(new_card.experience_id)
        assert retrieved is not None
        assert retrieved.status == WorkExperienceStatus.REJECTED

    def test_archive_mature(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        result = store.update_status(
            mature_card.experience_id,
            WorkExperienceStatus.ARCHIVED,
        )
        assert result is True
        retrieved = store.get(mature_card.experience_id)
        assert retrieved is not None
        assert retrieved.status == WorkExperienceStatus.ARCHIVED

    def test_invalid_transition_returns_false(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        # Can't go from CANDIDATE back to CANDIDATE (no-op is not allowed via update_status)
        result = store.update_status(
            mature_card.experience_id,
            WorkExperienceStatus.CANDIDATE,
        )
        assert result is False
        # Status unchanged (still CANDIDATE)
        retrieved = store.get(mature_card.experience_id)
        assert retrieved is not None
        assert retrieved.status == WorkExperienceStatus.CANDIDATE

    def test_archive_not_found_returns_false(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        from uuid import uuid4

        result = store.update_status(uuid4(), WorkExperienceStatus.ARCHIVED)
        assert result is False

    def test_record_effective_use(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        assert mature_card.effective_count == 2  # Set in fixture
        store.record_effective_use(mature_card.experience_id)
        retrieved = store.get(mature_card.experience_id)
        assert retrieved is not None
        assert retrieved.effective_count == 3
        assert retrieved.last_used_at is not None


# =============================================================================
# Service Tests
# =============================================================================


class TestWorkExperienceService:
    """Tests for WorkExperienceService governance methods."""

    def test_list_by_level(
        self,
        store: LocalWorkExperienceStore,
        new_card: WorkExperience,
        mature_card: WorkExperience,
        deprecated_card: WorkExperience,
    ) -> None:
        service = WorkExperienceService(store)
        new_cards = service.list_by_level(ExperienceLevel.NEW)
        assert len(new_cards) == 1
        assert new_cards[0].experience_id == new_card.experience_id

    def test_list_mature(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        service = WorkExperienceService(store)
        mature_cards = service.list_mature()
        assert len(mature_cards) == 1
        assert mature_cards[0].experience_id == mature_card.experience_id

    def test_promote_new_to_observed(
        self,
        store: LocalWorkExperienceStore,
        new_card: WorkExperience,
    ) -> None:
        service = WorkExperienceService(store)
        result = service.promote_to_observed(new_card.experience_id)
        assert result is True
        retrieved = store.get(new_card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_level == ExperienceLevel.OBSERVED

    def test_demote_mature_to_observed(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        service = WorkExperienceService(store)
        result = service.demote_to_observed(mature_card.experience_id)
        assert result is True
        retrieved = store.get(mature_card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_level == ExperienceLevel.OBSERVED

    def test_mark_deprecated(
        self,
        store: LocalWorkExperienceStore,
        new_card: WorkExperience,
    ) -> None:
        service = WorkExperienceService(store)
        result = service.mark_deprecated(new_card.experience_id)
        assert result is True
        retrieved = store.get(new_card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_level == ExperienceLevel.DEPRECATED

    def test_quality_score(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        service = WorkExperienceService(store)
        score = service.maturity_score_calc(mature_card)
        # MATURE level weight = 1.0, effective_ratio = 0.0 (hit_count=0 means no retrieval history)
        # score = 1.0*0.4 + 0*0.3 + 0*0.2 + 0.85*0.1 = 0.4 + 0 + 0 + 0.085 = 0.485
        assert score > 0.4
        assert score < 0.6

    def test_quality_score_high_usage(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="High quality card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.9,
            source_task_id="task-hq",
            source_session_id="s1",
            source_trace_id="t1",
            applicability_tags=[],
            hit_count=10,
            effective_count=5,
            experience_level=ExperienceLevel.MATURE,
        )
        service = WorkExperienceService(store)
        score = service.maturity_score_calc(card)
        # Level MATURE weight = 1.0
        # maturity_norm = 0 (default)
        # effective_ratio = 5 / (10 + 5) = 0.333
        # score = 1.0*0.4 + 0*0.3 + 0.333*0.2 + 0.9*0.1 = 0.4 + 0 + 0.067 + 0.09 = 0.557
        assert score > 0.5

    def test_top_cards(self, store: LocalWorkExperienceStore) -> None:
        for i, (conf, hits) in enumerate([(0.5, 0), (0.8, 5), (0.9, 2)]):
            card = WorkExperience(
                scope=WorkExperienceScope.GLOBAL,
                trigger_keywords=[f"kw{i}"],
                trigger_hint=f"hint:{i}",
                title=f"Card {i}",
                what_happened="",
                what_worked=["item"],
                what_failed=[],
                guidance="",
                avoidance="",
                confidence=conf,
                source_task_id=f"task-{i}",
                source_session_id="s1",
                source_trace_id="t1",
                applicability_tags=[],
                hit_count=hits,
                effective_count=0,
                experience_level=ExperienceLevel.MATURE,
            )
            store.save(card)

        service = WorkExperienceService(store)
        all_cards = store.list_all()
        top3 = service.top_cards(all_cards, top_k=3)
        assert len(top3) == 3
        # Top should be card with highest confidence (0.9) since all have same level weight
        # and all have effective_count=0 (so effective_ratio=0)
        assert top3[0].confidence == 0.9

    def test_find_duplicates(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        # Create a near-duplicate: same trigger_hint, high keyword overlap
        dupe = WorkExperience(
            scope=WorkExperienceScope.PROJECT,
            trigger_keywords=["csv", "file", "pandas", "encoding"],
            trigger_hint="type:csv",
            title="CSV Duplicate",
            what_happened="CSV parsed",
            what_worked=["pandas read_csv"],
            what_failed=[],
            guidance="Detect encoding first",
            avoidance="",
            confidence=0.7,
            source_task_id="task-dupe",
            source_session_id="session-1",
            source_trace_id="trace-dup",
            applicability_tags=["pandas", "csv", "encoding"],
            experience_level=ExperienceLevel.MATURE,
        )
        store.save(dupe)

        service = WorkExperienceService(store)
        dups = service.find_duplicates(mature_card, similarity_threshold=0.5)
        assert len(dups) == 1
        assert dups[0].experience_id == dupe.experience_id

    def test_merge_into(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        # Create a duplicate to merge
        dupe = WorkExperience(
            scope=WorkExperienceScope.PROJECT,
            trigger_keywords=["csv", "file"],
            trigger_hint="type:csv",
            title="CSV Duplicate",
            what_happened="CSV parsed",
            what_worked=["encoding detection"],
            what_failed=["wrong encoding"],
            guidance="Use chardet",
            avoidance="Don't assume encoding",
            confidence=0.7,
            source_task_id="task-dupe2",
            source_session_id="session-1",
            source_trace_id="trace-dup2",
            applicability_tags=["csv"],
            experience_level=ExperienceLevel.MATURE,
            hit_count=3,
            effective_count=1,
        )
        store.save(dupe)

        service = WorkExperienceService(store)
        result = service.merge_into(
            dupe.experience_id,
            mature_card.experience_id,
        )
        assert result is True

        # Source should be archived (status=ARCHIVED)
        assert (
            store.get(dupe.experience_id).status
            == WorkExperienceStatus.ARCHIVED
        )

        # Target should have merged fields
        merged = store.get(mature_card.experience_id)
        assert merged is not None
        # Union of what_worked from both cards
        assert "pandas read_csv with encoding param" in merged.what_worked
        assert "encoding detection" in merged.what_worked
        # Longer guidance wins ("Detect encoding first" > "Use chardet")
        assert merged.guidance == "Detect encoding first"
        assert merged.hit_count == mature_card.hit_count + dupe.hit_count
        assert (
            merged.effective_count
            == mature_card.effective_count + dupe.effective_count
        )


# =============================================================================
# Retriever: Non-Approved Not Returned Tests
# =============================================================================


class TestRetrieverGovernanceFiltering:
    """Tests: non-deprecated cards participate in retrieval by maturity level."""

    def test_mature_returned_by_default(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        retriever = WorkExperienceRetriever(store)
        results = retriever.retrieve()
        assert len(results) == 1
        assert results[0].experience_id == mature_card.experience_id

    def test_new_returned_by_default(
        self,
        store: LocalWorkExperienceStore,
        new_card: WorkExperience,
    ) -> None:
        """NEW level cards ARE returned by default (unlike old candidate status)."""
        retriever = WorkExperienceRetriever(store)
        results = retriever.retrieve()
        assert len(results) == 1
        assert results[0].experience_id == new_card.experience_id

    def test_deprecated_not_returned_by_default(
        self,
        store: LocalWorkExperienceStore,
        deprecated_card: WorkExperience,
    ) -> None:
        retriever = WorkExperienceRetriever(store)
        results = retriever.retrieve()
        assert len(results) == 0

    def test_deprecated_returned_when_included(
        self,
        store: LocalWorkExperienceStore,
        deprecated_card: WorkExperience,
    ) -> None:
        retriever = WorkExperienceRetriever(store)
        results = retriever.retrieve(include_deprecated=True)
        assert len(results) == 1
        assert results[0].experience_id == deprecated_card.experience_id

    def test_new_after_promotion_to_mature_returned(
        self,
        store: LocalWorkExperienceStore,
        new_card: WorkExperience,
    ) -> None:
        retriever = WorkExperienceRetriever(store)
        # Initially visible (NEW level)
        results = retriever.retrieve()
        assert len(results) == 1

        # Promote to OBSERVED
        store.update_experience_level(
            new_card.experience_id,
            ExperienceLevel.OBSERVED,
        )
        results = retriever.retrieve()
        assert len(results) == 1


# =============================================================================
# Integration: Effective Use Tracking
# =============================================================================


class TestEffectiveUseTracking:
    """Tests for effective use tracking in prompt injection."""

    def test_record_effective_use_updates_count(
        self,
        store: LocalWorkExperienceStore,
        mature_card: WorkExperience,
    ) -> None:
        initial = mature_card.effective_count
        store.record_effective_use(mature_card.experience_id)
        retrieved = store.get(mature_card.experience_id)
        assert retrieved is not None
        assert retrieved.effective_count == initial + 1
        assert retrieved.last_used_at is not None

    def test_record_effective_use_nonexistent_no_crash(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        from uuid import uuid4

        # Should not raise
        store.record_effective_use(uuid4())


# =============================================================================
# Integration: Prompt Injection with Status
# =============================================================================


class TestPromptInjectionWithGovernance:
    """Tests: deprecated cards do not reach the prompt injection stage."""

    def test_flag_off_no_impact(
        self,
        tmp_root,
    ) -> None:
        """Flag off: retriever still returns non-deprecated cards (existing behavior)."""
        with patch.dict(
            os.environ,
            {"ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "false"},
        ):
            from hubos.core.infra.feature_flags import reload_feature_flags

            reload_feature_flags()

            store = LocalWorkExperienceStore(root=tmp_root / "we_flag")
            # Add a NEW level card
            card = WorkExperience(
                scope=WorkExperienceScope.GLOBAL,
                trigger_keywords=["test"],
                trigger_hint="type:test",
                title="Test Card",
                what_happened="",
                what_worked=["item"],
                what_failed=[],
                guidance="",
                avoidance="",
                confidence=0.8,
                source_task_id="task-flag",
                source_session_id="s1",
                source_trace_id="t1",
                applicability_tags=[],
                experience_level=ExperienceLevel.NEW,
            )
            store.save(card)

            retriever = WorkExperienceRetriever(store)
            results = retriever.retrieve()

            # NEW card IS returned (maturity model includes non-deprecated)
            assert len(results) == 1

            # Flag off only affects injection, not retrieval

    def test_deprecated_card_not_in_prompt(
        self,
        tmp_root,
    ) -> None:
        """Deprecated card is not returned by retriever, so cannot reach prompt."""
        store = LocalWorkExperienceStore(root=tmp_root / "we_deprecated")
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="type:test",
            title="Deprecated Test",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.8,
            source_task_id="task-deprecated-test",
            source_session_id="s1",
            source_trace_id="t1",
            applicability_tags=[],
            experience_level=ExperienceLevel.DEPRECATED,
        )
        store.save(card)

        retriever = WorkExperienceRetriever(store)
        results = retriever.retrieve()
        assert len(results) == 0  # Deprecated is filtered out

    def test_mature_card_reaches_prompt(
        self,
        tmp_root,
    ) -> None:
        """Mature card is returned by retriever and can reach prompt injection."""
        store = LocalWorkExperienceStore(root=tmp_root / "we_mature")
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="type:test",
            title="Mature Test",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.8,
            source_task_id="task-mature-test",
            source_session_id="s1",
            source_trace_id="t1",
            applicability_tags=[],
            experience_level=ExperienceLevel.MATURE,
        )
        store.save(card)

        retriever = WorkExperienceRetriever(store)
        results = retriever.retrieve()
        assert len(results) == 1
        assert results[0].experience_level == ExperienceLevel.MATURE
