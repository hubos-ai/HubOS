"""Tests for Work Experience Layer maturity model (Phase 7+)."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from hubos.core.orchestrator.reflection_engine import TaskContext
from hubos.core.schemas.memory import ReflectionReport
from hubos.core.schemas.tasks import TaskResult, TaskStatus
from hubos.core.work_experience import (
    LocalWorkExperienceStore,
    WorkExperienceExtractor,
    WorkExperienceRetriever,
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
def tmp_root(tmp_path: Path) -> Path:
    """Provide a temporary root directory for the store."""
    return tmp_path / "work_experience_maturity"


@pytest.fixture
def store(tmp_root: Path) -> LocalWorkExperienceStore:
    """Provide a LocalWorkExperienceStore backed by a temp directory."""
    return LocalWorkExperienceStore(root=tmp_root)


@pytest.fixture
def sample_context() -> TaskContext:
    """Provide a TaskContext with known content."""
    return TaskContext(
        task_id="task-test-001",
        session_id="session-test-001",
        trace_id="trace-test-001",
        task_input={
            "type": "file_process",
            "query": "read and summarize a CSV file",
            "format": "csv",
        },
        execution_trace=[
            {"step": 1, "tool": "file_reader", "success": True},
            {"step": 2, "tool": "csv_parser", "success": True},
        ],
        task_result=TaskResult(
            unit_id="unit-001",
            task_id="task-test-001",
            status=TaskStatus.SUCCESS,
            confidence=0.9,
            output_data={"summary": "10 rows processed"},
            artifacts={},
            error_message=None,
            retry_count=0,
            executed_at=datetime.now(timezone.utc),
        ),
        execution_time_ms=1500,
    )


@pytest.fixture
def sample_report() -> ReflectionReport:
    """Provide a ReflectionReport with rich content."""
    return ReflectionReport(
        report_id=uuid4(),
        task_id="task-test-001",
        session_id="session-test-001",
        trace_id="trace-test-001",
        what_worked=[
            "CSV file was parsed correctly using pandas",
            "Result was formatted as a clean markdown table",
        ],
        what_failed=[
            "Unicode characters in the file caused encoding issues",
        ],
        root_cause="File encoding was not detected correctly; default UTF-8 failed",
        next_time_strategy=(
            "Use chardet to detect file encoding before parsing. "
            "Consider setting encoding='utf-8-sig' for CSV files."
        ),
        confidence=0.75,
        has_human_feedback=False,
        policy_suggestions=[],
        created_at=datetime.now(timezone.utc),
    )


# =============================================================================
# Experience Level Tests
# =============================================================================

class TestExperienceLevel:
    """Tests for ExperienceLevel enum."""

    def test_level_weights(self) -> None:
        """Level weights are correct."""
        assert ExperienceLevel.NEW.retrieval_weight() == 0.3
        assert ExperienceLevel.OBSERVED.retrieval_weight() == 0.6
        assert ExperienceLevel.MATURE.retrieval_weight() == 1.0
        assert ExperienceLevel.DEPRECATED.retrieval_weight() == 0.0

    def test_level_transitions(self) -> None:
        """Valid level transitions work."""
        assert ExperienceLevel.NEW.can_transition_to(ExperienceLevel.OBSERVED)
        assert ExperienceLevel.NEW.can_transition_to(ExperienceLevel.MATURE)
        assert ExperienceLevel.NEW.can_transition_to(ExperienceLevel.DEPRECATED)

        assert ExperienceLevel.OBSERVED.can_transition_to(ExperienceLevel.MATURE)
        assert ExperienceLevel.OBSERVED.can_transition_to(ExperienceLevel.NEW)  # regress
        assert ExperienceLevel.OBSERVED.can_transition_to(ExperienceLevel.DEPRECATED)

        assert ExperienceLevel.MATURE.can_transition_to(ExperienceLevel.OBSERVED)  # regress
        assert ExperienceLevel.MATURE.can_transition_to(ExperienceLevel.DEPRECATED)

        assert not ExperienceLevel.DEPRECATED.can_transition_to(ExperienceLevel.NEW)
        assert not ExperienceLevel.DEPRECATED.can_transition_to(ExperienceLevel.OBSERVED)
        assert not ExperienceLevel.DEPRECATED.can_transition_to(ExperienceLevel.MATURE)


# =============================================================================
# Store Maturity Tests
# =============================================================================

class TestStoreMaturity:
    """Tests for store maturity methods."""

    def test_update_experience_level(self, store: LocalWorkExperienceStore) -> None:
        """update_experience_level transitions work."""
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="Test card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.NEW,
        )
        store.save(card)

        # Promote to observed
        ok = store.update_experience_level(card.experience_id, ExperienceLevel.OBSERVED)
        assert ok is True
        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_level == ExperienceLevel.OBSERVED

    def test_update_experience_level_invalid_transition(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """Invalid level transitions return False."""
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="Test card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.DEPRECATED,
        )
        store.save(card)

        # Can't transition from deprecated
        ok = store.update_experience_level(card.experience_id, ExperienceLevel.NEW)
        assert ok is False

    def test_update_maturity_score(self, store: LocalWorkExperienceStore) -> None:
        """update_maturity_score works."""
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="Test card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            maturity_score=50.0,
        )
        store.save(card)

        ok = store.update_maturity_score(card.experience_id, 75.5)
        assert ok is True
        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.maturity_score == 75.5

    def test_update_maturity_score_clamps(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """maturity_score is clamped to 0-100."""
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="Test card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            maturity_score=50.0,
        )
        store.save(card)

        store.update_maturity_score(card.experience_id, 150.0)
        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.maturity_score == 100.0

        store.update_maturity_score(card.experience_id, -20.0)
        retrieved2 = store.get(card.experience_id)
        assert retrieved2 is not None
        assert retrieved2.maturity_score == 0.0

    def test_find_similar(self, store: LocalWorkExperienceStore) -> None:
        """find_similar returns cards with matching trigger_hint prefix."""
        card1 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["csv", "file"],
            trigger_hint="input_text:csv_pro",
            title="CSV processing",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card1)

        card2 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["csv", "encoding"],
            trigger_hint="input_text:csv_enc",
            title="CSV encoding",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.8,
            source_task_id="task-2",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card2)

        card3 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["json"],
            trigger_hint="input_text:json_par",
            title="JSON parsing",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.6,
            source_task_id="task-3",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card3)

        # Find similar to card1
        similar = store.find_similar("input_text:csv")
        assert len(similar) == 2
        assert card1.experience_id in [c.experience_id for c in similar]
        assert card2.experience_id in [c.experience_id for c in similar]
        assert card3.experience_id not in [c.experience_id for c in similar]

    def test_find_similar_with_keywords(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """find_similar filters by keywords when provided."""
        card1 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["csv", "file", "pandas"],
            trigger_hint="input_text:csv",
            title="CSV with pandas",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card1)

        card2 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["csv", "encoding", "chardet"],
            trigger_hint="input_text:csv",
            title="CSV encoding",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.8,
            source_task_id="task-2",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card2)

        # Find with pandas keyword
        similar = store.find_similar("input_text:csv", keywords=["pandas"])
        assert len(similar) == 1
        assert similar[0].experience_id == card1.experience_id

    def test_backward_compatibility_status(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """Old cards without experience_level default to NEW."""
        # Simulate old card data (before experience_level field was added)
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="Old card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            # Old cards don't have experience_level - will default to NEW
        )
        store.save(card)

        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_level == ExperienceLevel.NEW


# =============================================================================
# Extractor Maturity Tests
# =============================================================================

class TestExtractorMaturity:
    """Tests for extractor new fields."""

    def test_extract_populates_new_fields(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """Extractor populates new work guidance fields."""
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(sample_report, sample_context)

        assert card is not None
        # New fields should be populated
        assert card.experience_level == ExperienceLevel.NEW
        assert card.maturity_score > 0  # Based on confidence * 50
        assert card.success_rate_estimate == sample_report.confidence
        # recommended_tool_order from execution_trace
        assert len(card.recommended_tool_order) > 0
        # recommended_workflow from execution_trace
        assert len(card.recommended_workflow) > 0
        # usage_pattern_summary
        assert len(card.usage_pattern_summary) > 0

    def test_extract_recommended_tool_order(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """Extractor gets tool order from execution trace."""
        context = TaskContext(
            task_id="task-tools",
            session_id="session-1",
            trace_id="trace-1",
            task_input={"type": "web_crawl"},
            execution_trace=[
                {"step": 1, "tool": "http_fetch", "success": True},
                {"step": 2, "tool": "html_parser", "success": True},
                {"step": 3, "tool": "data_saver", "success": True},
            ],
            task_result=TaskResult(
                unit_id="unit-1",
                task_id="task-tools",
                status=TaskStatus.SUCCESS,
                confidence=0.8,
                output_data={},
                artifacts={},
                error_message=None,
                retry_count=0,
                executed_at=datetime.now(timezone.utc),
            ),
            execution_time_ms=1000,
        )
        report = ReflectionReport(
            report_id=uuid4(),
            task_id="task-tools",
            session_id="session-1",
            trace_id="trace-1",
            what_worked=["Fetched and parsed web content"],
            what_failed=[],
            root_cause="",
            next_time_strategy="Use http_fetch then html_parser",
            confidence=0.8,
            has_human_feedback=False,
            policy_suggestions=[],
            created_at=datetime.now(timezone.utc),
        )

        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(report, context)

        assert card is not None
        assert "http_fetch" in card.recommended_tool_order
        assert "html_parser" in card.recommended_tool_order
        assert "data_saver" in card.recommended_tool_order
        # Order preserved
        assert card.recommended_tool_order.index("http_fetch") < card.recommended_tool_order.index("html_parser")


# =============================================================================
# Retriever Maturity Tests
# =============================================================================

class TestRetrieverMaturity:
    """Tests for maturity-based retrieval."""

    def test_new_experience_participates_at_low_weight(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """New experiences can be retrieved (low weight but participating)."""
        new_card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test", "new"],
            trigger_hint="input_text:test",
            title="New experience",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.5,
            source_task_id="task-new",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.NEW,
            maturity_score=25.0,
        )
        store.save(new_card)

        retriever = WorkExperienceRetriever(store, max_results=5)
        results = retriever.retrieve(keywords=["test"])

        assert len(results) >= 1
        assert new_card.experience_id in [c.experience_id for c in results]

    def test_mature_experience_ranks_higher(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """Mature experiences rank higher than new in retrieval."""
        new_card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test", "mature"],
            trigger_hint="input_text:test",
            title="New experience",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.5,
            source_task_id="task-new",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.NEW,
            maturity_score=25.0,
        )
        store.save(new_card)

        mature_card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test", "mature"],
            trigger_hint="input_text:test",
            title="Mature experience",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.8,
            source_task_id="task-mature",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.MATURE,
            maturity_score=85.0,
        )
        store.save(mature_card)

        retriever = WorkExperienceRetriever(store, max_results=5)
        results = retriever.retrieve(keywords=["test", "mature"])

        assert len(results) >= 2
        # Mature should be first
        mature_idx = next(i for i, c in enumerate(results) if c.experience_id == mature_card.experience_id)
        new_idx = next(i for i, c in enumerate(results) if c.experience_id == new_card.experience_id)
        assert mature_idx < new_idx

    def test_deprecated_excluded_by_default(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """Deprecated experiences are excluded from retrieval."""
        deprecated_card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test", "deprecated"],
            trigger_hint="input_text:test",
            title="Deprecated experience",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.3,
            source_task_id="task-deprecated",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.DEPRECATED,
            maturity_score=10.0,
        )
        store.save(deprecated_card)

        retriever = WorkExperienceRetriever(store, max_results=5)
        results = retriever.retrieve(keywords=["test", "deprecated"])

        assert deprecated_card.experience_id not in [c.experience_id for c in results]

    def test_deprecated_included_when_requested(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """Deprecated experiences included when include_deprecated=True."""
        deprecated_card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test", "deprecated"],
            trigger_hint="input_text:test",
            title="Deprecated experience",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.3,
            source_task_id="task-deprecated",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.DEPRECATED,
            maturity_score=10.0,
        )
        store.save(deprecated_card)

        retriever = WorkExperienceRetriever(store, max_results=5)
        results = retriever.retrieve(keywords=["test", "deprecated"], include_deprecated=True)

        assert deprecated_card.experience_id in [c.experience_id for c in results]

    def test_disabled_excluded_by_default(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """Disabled experiences are excluded from retrieval."""
        disabled_card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test", "disabled"],
            trigger_hint="input_text:test",
            title="Disabled experience",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.3,
            source_task_id="task-disabled",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.NEW,
            maturity_score=10.0,
            disabled=True,
        )
        store.save(disabled_card)

        retriever = WorkExperienceRetriever(store, max_results=5)
        results = retriever.retrieve(keywords=["test", "disabled"])

        assert disabled_card.experience_id not in [c.experience_id for c in results]


# =============================================================================
# Service Maturity Tests
# =============================================================================

class TestServiceMaturity:
    """Tests for service maturity methods."""

    def test_promote_to_observed(self, store: LocalWorkExperienceStore) -> None:
        """Service can promote NEW to OBSERVED."""
        from hubos.core.work_experience.service import WorkExperienceService

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
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.NEW,
        )
        store.save(card)

        service = WorkExperienceService(store)
        ok = service.promote_to_observed(card.experience_id)

        assert ok is True
        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_level == ExperienceLevel.OBSERVED

    def test_mark_deprecated(self, store: LocalWorkExperienceStore) -> None:
        """Service can mark a card as deprecated."""
        from hubos.core.work_experience.service import WorkExperienceService

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
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.MATURE,
        )
        store.save(card)

        service = WorkExperienceService(store)
        ok = service.mark_deprecated(card.experience_id)

        assert ok is True
        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_level == ExperienceLevel.DEPRECATED

    def test_update_existing_experience(
        self, store: LocalWorkExperienceStore
    ) -> None:
        """Service can update an existing experience with new observations."""
        from hubos.core.work_experience.service import WorkExperienceService

        existing = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["csv", "file"],
            trigger_hint="input_text:csv",
            title="CSV handling",
            what_happened="",
            what_worked=["pandas read_csv"],
            what_failed=[],
            guidance="Use encoding detection",
            avoidance="",
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            experience_level=ExperienceLevel.OBSERVED,
            maturity_score=50.0,
            recommended_tool_order=["file_reader", "csv_parser"],
            recommended_workflow=["1. Read file", "2. Parse CSV"],
        )
        store.save(existing)

        new_context = TaskContext(
            task_id="task-2",
            session_id="session-1",
            trace_id="trace-2",
            task_input={"type": "csv_process"},
            execution_trace=[
                {"step": 1, "tool": "file_reader", "success": True},
                {"step": 2, "tool": "chardet_detector", "success": True},
                {"step": 3, "tool": "csv_parser", "success": True},
            ],
            task_result=TaskResult(
                unit_id="unit-2",
                task_id="task-2",
                status=TaskStatus.SUCCESS,
                confidence=0.85,
                output_data={},
                artifacts={},
                error_message=None,
                retry_count=0,
                executed_at=datetime.now(timezone.utc),
            ),
            execution_time_ms=2000,
        )
        new_report = ReflectionReport(
            report_id=uuid4(),
            task_id="task-2",
            session_id="session-1",
            trace_id="trace-2",
            what_worked=["Used chardet for encoding detection", "pandas read_csv worked"],
            what_failed=["Old approach failed on latin-1 files"],
            root_cause="Latin-1 encoding not handled",
            next_time_strategy="Always use chardet before parsing",
            confidence=0.85,
            has_human_feedback=False,
            policy_suggestions=[],
            created_at=datetime.now(timezone.utc),
        )

        service = WorkExperienceService(store)
        ok = service.update_existing_experience(existing.experience_id, new_report, new_context)

        assert ok is True
        updated = store.get(existing.experience_id)
        assert updated is not None
        # Should have merged what_worked (may have "Used " prefix)
        assert any("chardet" in w for w in updated.what_worked)
        # Should have updated tool order with new tool
        assert "chardet_detector" in updated.recommended_tool_order
        # Should have increased maturity score
        assert updated.maturity_score > 50.0


# =============================================================================
# Effective Ratio Tests
# =============================================================================

class TestEffectiveRatio:
    """Tests for effective_ratio method."""

    def test_effective_ratio_no_hits(self) -> None:
        """Card with no hits has effective_ratio of 0."""
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
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            hit_count=0,
            effective_count=0,
        )
        assert card.effective_ratio() == 0.0

    def test_effective_ratio_with_hits(self) -> None:
        """Card with hits has correct effective_ratio."""
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
            confidence=0.7,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            hit_count=10,
            effective_count=7,
        )
        assert card.effective_ratio() == 0.7
