# -*- coding: utf-8 -*-
"""Unit tests for the Work Experience Layer (Phase 0-3)."""

import json
import shutil
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
    return tmp_path / "work_experience"


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


@pytest.fixture
def failure_context() -> TaskContext:
    """Provide a TaskContext for a failed task."""
    return TaskContext(
        task_id="task-fail-001",
        session_id="session-test-001",
        trace_id="trace-fail-001",
        task_input={
            "type": "web_crawl",
            "url": "https://example.com/page",
        },
        execution_trace=[
            {
                "step": 1,
                "tool": "web_crawl",
                "success": False,
                "error": "timeout",
            },
        ],
        task_result=TaskResult(
            unit_id="unit-002",
            task_id="task-fail-001",
            status=TaskStatus.FAILURE,
            confidence=0.3,
            output_data={},
            artifacts={},
            error_message="Connection timeout after 30s",
            retry_count=1,
            executed_at=datetime.now(timezone.utc),
        ),
        execution_time_ms=30000,
    )


@pytest.fixture
def failure_report() -> ReflectionReport:
    """Provide a ReflectionReport for a failed task."""
    return ReflectionReport(
        report_id=uuid4(),
        task_id="task-fail-001",
        session_id="session-test-001",
        trace_id="trace-fail-001",
        what_worked=[],
        what_failed=[
            "Web crawl timed out after 30 seconds",
            "robots.txt blocked the URL",
        ],
        root_cause="Timeout - network latency exceeded threshold",
        next_time_strategy="Increase timeout for external URLs; check robots.txt first",
        confidence=0.55,
        has_human_feedback=False,
        policy_suggestions=[],
        created_at=datetime.now(timezone.utc),
    )


# =============================================================================
# Store Tests
# =============================================================================


class TestLocalWorkExperienceStore:
    """Tests for LocalWorkExperienceStore."""

    def test_save_and_get(self, store: LocalWorkExperienceStore) -> None:
        """Round-trip save and get returns the same card."""
        card = WorkExperience(
            scope=WorkExperienceScope.PROJECT,
            trigger_keywords=["csv", "file", "encoding"],
            trigger_hint="type:csv",
            title="CSV encoding handling",
            what_happened="Parsed CSV with unicode successfully",
            what_worked=["pandas read_csv with encoding param"],
            what_failed=[],
            guidance="Always detect encoding first",
            avoidance="Don't assume UTF-8",
            confidence=0.8,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=["pandas", "csv", "encoding"],
        )
        store.save(card)

        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.experience_id == card.experience_id
        assert retrieved.title == card.title
        assert retrieved.scope == WorkExperienceScope.PROJECT
        assert retrieved.confidence == 0.8
        assert retrieved.trigger_hint == "type:csv"
        assert retrieved.what_worked == ["pandas read_csv with encoding param"]

    def test_save_updates_existing(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """Saving with the same ID updates the card."""
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test:key",
            title="Original title",
            what_happened="",
            what_worked=["original item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.5,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card)

        card.title = "Updated title"
        store.save(card)

        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.title == "Updated title"

    def test_list_all(self, store: LocalWorkExperienceStore) -> None:
        """list_all returns all saved non-disabled cards."""
        for scope in WorkExperienceScope:
            card = WorkExperience(
                scope=scope,
                trigger_keywords=[scope.value],
                trigger_hint=f"hint:{scope.value}",
                title=f"Card for {scope.value}",
                what_happened="",
                what_worked=["item"],
                what_failed=[],
                guidance="",
                avoidance="",
                confidence=0.6,
                source_task_id=f"task-{scope.value}",
                source_session_id="session-1",
                source_trace_id="trace-1",
                applicability_tags=[],
            )
            store.save(card)

        all_cards = store.list_all()
        assert len(all_cards) == len(WorkExperienceScope)

    def test_list_by_scope(self, store: LocalWorkExperienceStore) -> None:
        """list_by_scope returns only cards for that scope."""
        card_g = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["global"],
            trigger_hint="global",
            title="Global card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.6,
            source_task_id="task-g",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card_g)

        card_u = WorkExperience(
            scope=WorkExperienceScope.USER,
            trigger_keywords=["user"],
            trigger_hint="user",
            title="User card",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.6,
            source_task_id="task-u",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card_u)

        global_cards = store.list_by_scope(WorkExperienceScope.GLOBAL)
        assert len(global_cards) == 1
        assert global_cards[0].experience_id == card_g.experience_id

        user_cards = store.list_by_scope(WorkExperienceScope.USER)
        assert len(user_cards) == 1
        assert user_cards[0].experience_id == card_u.experience_id

    def test_disable(self, store: LocalWorkExperienceStore) -> None:
        """disable marks the card and excludes it from list_all."""
        card = WorkExperience(
            scope=WorkExperienceScope.SESSION,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="To be disabled",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.6,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card)

        result = store.disable(card.experience_id)
        assert result is True

        # Should not appear in list_all
        assert store.count_all() == 0
        assert (
            store.get(card.experience_id) is not None
        )  # Still findable by ID

    def test_disable_not_found(self, store: LocalWorkExperienceStore) -> None:
        """disable returns False for unknown ID."""
        result = store.disable(uuid4())
        assert result is False

    def test_increment_hit(self, store: LocalWorkExperienceStore) -> None:
        """increment_hit increases hit_count and sets last_retrieved_at."""
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test",
            title="Hit counter test",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.6,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            hit_count=0,
        )
        store.save(card)

        store.increment_hit(card.experience_id)
        retrieved = store.get(card.experience_id)
        assert retrieved is not None
        assert retrieved.hit_count == 1
        assert retrieved.last_retrieved_at is not None

        store.increment_hit(card.experience_id)
        retrieved2 = store.get(card.experience_id)
        assert retrieved2 is not None
        assert retrieved2.hit_count == 2

    def test_get_not_found(self, store: LocalWorkExperienceStore) -> None:
        """get returns None for unknown ID."""
        assert store.get(uuid4()) is None

    def test_count_all_and_by_scope(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """count helpers return correct counts."""
        for i, scope in enumerate(WorkExperienceScope):
            for _ in range(i + 1):
                card = WorkExperience(
                    scope=scope,
                    trigger_keywords=[scope.value],
                    trigger_hint=f"hint:{i}",
                    title=f"Card {i}",
                    what_happened="",
                    what_worked=["item"],
                    what_failed=[],
                    guidance="",
                    avoidance="",
                    confidence=0.6,
                    source_task_id=f"task-{i}",
                    source_session_id="session-1",
                    source_trace_id="trace-1",
                    applicability_tags=[],
                )
                store.save(card)

        assert store.count_all() == sum(range(1, len(WorkExperienceScope) + 1))
        assert store.count_by_scope(WorkExperienceScope.GLOBAL) == 1
        assert store.count_by_scope(WorkExperienceScope.USER) == 2

    def test_index_jsonl_created(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """Saving a card appends an entry to index.jsonl."""
        card = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["index", "test"],
            trigger_hint="index:test",
            title="Index test",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.6,
            source_task_id="task-index",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
        )
        store.save(card)

        index_path = store._root / "index.jsonl"
        assert index_path.exists()
        lines = index_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) >= 1
        entry = json.loads(lines[-1])
        assert entry["experience_id"] == str(card.experience_id)


# =============================================================================
# Extractor Tests
# =============================================================================


class TestWorkExperienceExtractor:
    """Tests for WorkExperienceExtractor."""

    def test_extracts_full_card(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """extract produces a complete WorkExperience from a rich report."""
        extractor = WorkExperienceExtractor(store, min_confidence=0.5)
        card = extractor.extract(sample_report, sample_context)

        assert card is not None
        assert card.scope == WorkExperienceScope.SESSION  # default scope
        assert len(card.trigger_keywords) > 0
        assert (
            "csv" in card.trigger_keywords or "file" in card.trigger_keywords
        )
        assert (
            card.trigger_hint == "type:file_proce"
        )  # first key="type", val="file_process"[:10]
        assert "CSV" in card.title or "CSV encoding" in card.title
        assert len(card.what_happened) > 0
        assert len(card.what_worked) == 2
        assert len(card.what_failed) == 1
        assert len(card.guidance) > 0
        assert card.confidence == 0.75
        assert card.source_task_id == "task-test-001"
        assert card.source_session_id == "session-test-001"

    def test_returns_none_low_confidence(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
    ) -> None:
        """extract returns None when confidence is below threshold."""
        report = ReflectionReport(
            report_id=uuid4(),
            task_id="task-test-001",
            session_id="session-test-001",
            trace_id="trace-test-001",
            what_worked=["something worked"],
            what_failed=[],
            root_cause="",
            next_time_strategy="",
            confidence=0.3,  # below default 0.5 threshold
            has_human_feedback=False,
            policy_suggestions=[],
            created_at=datetime.now(timezone.utc),
        )
        extractor = WorkExperienceExtractor(store, min_confidence=0.5)
        card = extractor.extract(report, sample_context)
        assert card is None

    def test_returns_none_empty_data(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
    ) -> None:
        """extract returns None when both what_worked and what_failed are empty."""
        report = ReflectionReport(
            report_id=uuid4(),
            task_id="task-test-001",
            session_id="session-test-001",
            trace_id="trace-test-001",
            what_worked=[],  # empty
            what_failed=[],  # empty
            root_cause="",
            next_time_strategy="",
            confidence=0.8,
            has_human_feedback=False,
            policy_suggestions=[],
            created_at=datetime.now(timezone.utc),
        )
        extractor = WorkExperienceExtractor(store, min_confidence=0.5)
        card = extractor.extract(report, sample_context)
        assert card is None

    def test_extracts_keywords_from_task_input(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """Keywords are extracted from task_input string values."""
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(sample_report, sample_context)

        assert card is not None
        # "csv", "file", "process", "read", "summarize" should appear
        kw_lower = {k.lower() for k in card.trigger_keywords}
        assert any(k in kw_lower for k in ["csv", "file", "process", "read"])

    def test_guidance_from_next_time_strategy(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """guidance field is populated from next_time_strategy."""
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(sample_report, sample_context)

        assert card is not None
        assert (
            "chardet" in card.guidance.lower()
            or "encoding" in card.guidance.lower()
        )

    def test_avoidance_from_what_failed(
        self,
        store: LocalWorkExperienceStore,
        failure_context: TaskContext,
        failure_report: ReflectionReport,
    ) -> None:
        """avoidance field is populated from what_failed and root_cause."""
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(failure_report, failure_context)

        assert card is not None
        assert len(card.avoidance) > 0

    def test_applicability_tags_from_trace(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """applicability_tags are extracted from execution_trace tool names."""
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(sample_report, sample_context)

        assert card is not None
        assert (
            "file_reader" in card.applicability_tags
            or "csv_parser" in card.applicability_tags
        )

    def test_failure_task_produces_card(
        self,
        store: LocalWorkExperienceStore,
        failure_context: TaskContext,
        failure_report: ReflectionReport,
    ) -> None:
        """A failed task with sufficient confidence still produces a card."""
        extractor = WorkExperienceExtractor(store, min_confidence=0.5)
        card = extractor.extract(failure_report, failure_context)

        assert card is not None
        assert card.source_task_id == "task-fail-001"
        assert len(card.what_failed) > 0

    def test_infer_scope_user(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """Scope is inferred as USER when task_input contains user_id."""
        sample_context.task_input["user_id"] = "user-123"
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(sample_report, sample_context)

        assert card is not None
        assert card.scope == WorkExperienceScope.USER

    def test_infer_scope_project(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """Scope is inferred as PROJECT when task_input contains project_id."""
        sample_context.task_input["project_id"] = "proj-abc"
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(sample_report, sample_context)

        assert card is not None
        assert card.scope == WorkExperienceScope.PROJECT

    def test_custom_min_confidence(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
    ) -> None:
        """A card with confidence between default and custom threshold is included."""
        report = ReflectionReport(
            report_id=uuid4(),
            task_id="task-test-001",
            session_id="session-test-001",
            trace_id="trace-test-001",
            what_worked=["Worked fine"],
            what_failed=[],
            root_cause="",
            next_time_strategy="",
            confidence=0.55,  # above 0.5 but below 0.6
            has_human_feedback=False,
            policy_suggestions=[],
            created_at=datetime.now(timezone.utc),
        )
        extractor_default = WorkExperienceExtractor(store, min_confidence=0.5)
        card_default = extractor_default.extract(report, sample_context)
        assert card_default is not None

        extractor_strict = WorkExperienceExtractor(store, min_confidence=0.6)
        card_strict = extractor_strict.extract(report, sample_context)
        assert card_strict is None


# =============================================================================
# Retriever Tests
# =============================================================================


class TestWorkExperienceRetriever:
    """Tests for WorkExperienceRetriever."""

    @pytest.fixture
    def populated_store(self) -> LocalWorkExperienceStore:  # type: ignore[override]
        """Provide a store with 6 cards across different scopes and keywords.

        Creates its own tempfile.TemporaryDirectory for guaranteed test isolation.
        """
        import tempfile as _tempfile

        _tmp = _tempfile.TemporaryDirectory()
        store = LocalWorkExperienceStore(
            root=Path(_tmp.name) / "work_experience",
        )
        cards_data = [
            (
                WorkExperienceScope.GLOBAL,
                ["python", "file", "csv"],
                "hint:csv",
                "Global CSV handling",
            ),
            (
                WorkExperienceScope.USER,
                ["python", "web", "api"],
                "hint:web",
                "User API handling",
            ),
            (
                WorkExperienceScope.PROJECT,
                ["python", "database", "sql"],
                "hint:sql",
                "Project SQL query",
            ),
            (
                WorkExperienceScope.SESSION,
                ["javascript", "web"],
                "hint:js",
                "Session JS task",
            ),
            (
                WorkExperienceScope.GLOBAL,
                ["python", "encoding"],
                "hint:encoding",
                "Global encoding fix",
            ),
            (
                WorkExperienceScope.USER,
                ["python", "file"],
                "hint:file",
                "User file task",
            ),
        ]
        for scope, keywords, hint, title in cards_data:
            card = WorkExperience(
                scope=scope,
                trigger_keywords=keywords,
                trigger_hint=hint,
                title=title,
                what_happened="",
                what_worked=["item"],
                what_failed=[],
                guidance="",
                avoidance="",
                confidence=0.7,
                source_task_id=f"task-{title}",
                source_session_id="session-1",
                source_trace_id="trace-1",
                applicability_tags=keywords[:2],
                status=WorkExperienceStatus.APPROVED,  # Phase 6: default to APPROVED for retrieval tests
            )
            store.save(card)
        # Store the tmp dir so it can be cleaned up when the fixture is garbage-collected
        store._own_tmpdir = _tmp  # type: ignore[attr-defined]
        return store

    def test_retrieve_all_no_filter(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """retrieve with no filters returns all non-disabled cards."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)
        results = retriever.retrieve()

        assert len(results) == 6

    def test_retrieve_scope_filter(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """Scope filter returns only cards at that scope."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)

        global_results = retriever.retrieve(scope=WorkExperienceScope.GLOBAL)
        assert len(global_results) == 2
        assert all(
            c.scope == WorkExperienceScope.GLOBAL for c in global_results
        )

        user_results = retriever.retrieve(scope=WorkExperienceScope.USER)
        assert len(user_results) == 2
        assert all(c.scope == WorkExperienceScope.USER for c in user_results)

    def test_retrieve_keyword_filter(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """Keyword filter returns cards with at least one matching keyword."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)

        results = retriever.retrieve(keywords=["python", "csv"])
        # python matches: GLOBAL(csv), USER(web+api), PROJECT(db+sql), GLOBAL(encoding), USER(file) = 5
        # csv matches: GLOBAL(csv) = 1 (already counted)
        # total unique: 5
        assert len(results) == 5
        # All returned cards should have python or csv
        for card in results:
            has_match = any(
                k in ["python", "csv"] for k in card.trigger_keywords
            )
            assert has_match

    def test_retrieve_keyword_scoring(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """Cards with more keyword overlap are ranked higher."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)

        results = retriever.retrieve(keywords=["python", "file"])
        # Should be sorted by overlap score desc
        scores = []
        for card in results:
            overlap = len(set(["python", "file"]) & set(card.trigger_keywords))
            scores.append(overlap)
        assert scores == sorted(scores, reverse=True)

    def test_retrieve_trigger_hint_filter(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """trigger_hint prefix filter returns only matching cards."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)

        results = retriever.retrieve(trigger_hint="hint:csv")
        assert len(results) == 1
        assert results[0].trigger_hint == "hint:csv"

        results2 = retriever.retrieve(trigger_hint="hint:py")  # No match
        assert len(results2) == 0

    def test_retrieve_combined_filters(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """Scope + keyword + trigger_hint can be combined."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)

        results = retriever.retrieve(
            scope=WorkExperienceScope.GLOBAL,
            keywords=["python"],
        )
        assert len(results) == 2
        assert all(c.scope == WorkExperienceScope.GLOBAL for c in results)
        assert all("python" in c.trigger_keywords for c in results)

    def test_retrieve_max_results(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """max_results limits the number of returned cards."""
        retriever = WorkExperienceRetriever(populated_store, max_results=3)
        results = retriever.retrieve()
        assert len(results) == 3

    def test_retrieve_excludes_disabled_by_default(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """Disabled cards are excluded from default retrieval."""
        # Disable one card
        all_cards = populated_store.list_all()
        populated_store.disable(all_cards[0].experience_id)

        retriever = WorkExperienceRetriever(populated_store, max_results=10)
        results = retriever.retrieve()
        assert len(results) == 5

    def test_retrieve_includes_disabled_when_requested(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """include_disabled=True returns disabled cards too."""
        all_cards = populated_store.list_all()
        populated_store.disable(all_cards[0].experience_id)

        retriever = WorkExperienceRetriever(populated_store, max_results=10)
        results = retriever.retrieve(include_disabled=True)
        assert len(results) == 6

    def test_retrieve_sorted_by_scope_priority(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """When no keywords provided, results are sorted: GLOBAL > USER > PROJECT > SESSION."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)

        results = retriever.retrieve(keywords=["python"])  # All have python

        # GLOBAL cards first
        global_cards = [
            c for c in results if c.scope == WorkExperienceScope.GLOBAL
        ]
        user_cards = [
            c for c in results if c.scope == WorkExperienceScope.USER
        ]
        project_cards = [
            c for c in results if c.scope == WorkExperienceScope.PROJECT
        ]

        # Verify ordering: global cards appear before user, user before project
        first_global_idx = next(
            i
            for i, c in enumerate(results)
            if c.scope == WorkExperienceScope.GLOBAL
        )
        first_user_idx = next(
            i
            for i, c in enumerate(results)
            if c.scope == WorkExperienceScope.USER
        )
        first_project_idx = next(
            i
            for i, c in enumerate(results)
            if c.scope == WorkExperienceScope.PROJECT
        )

        assert first_global_idx < first_user_idx < first_project_idx

    def test_retrieve_increments_hit_count(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """Calling retrieve increments hit_count on returned cards."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)
        results = retriever.retrieve()

        for card in results:
            retrieved = populated_store.get(card.experience_id)
            assert retrieved is not None
            assert retrieved.hit_count >= 1

    def test_retrieve_for_task(
        self,
        populated_store: LocalWorkExperienceStore,
    ) -> None:
        """retrieve_for_task extracts keywords and trigger from task_input dict."""
        retriever = WorkExperienceRetriever(populated_store, max_results=10)

        task_input = {"type": "file", "query": "process python csv file"}
        results = retriever.retrieve_for_task(task_input)

        # Should match cards with file or python or csv keywords
        assert len(results) > 0

    def test_retrieve_empty_result(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """retrieve returns empty list when nothing matches."""
        retriever = WorkExperienceRetriever(store, max_results=5)
        results = retriever.retrieve(keywords=["nonexistentkeywordxyz"])
        assert results == []

    def test_trigger_hint_prefix_matching_and_fallback(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """verify trigger_hint prefix matching via startswith and keyword-only fallback.

        retrieve_for_task builds trigger_hint as:
          f"{first_key}:{first_val[:10].lower().replace(' ', '_')}"

        retrieve() matches with: c.trigger_hint.startswith(task_hint)
        So a card with trigger_hint='input_text:send_a_di' matches task_hint='input_text:send_a_di'
        (exact 10-char match), or a card with trigger_hint='input_text:send_a_discord'
        also matches (card longer, prefix covers task hint).

        When no trigger_hint matches exist, retrieve_for_task falls back to
        keyword-only retrieval.
        """
        import tempfile as _tempfile

        _tmp = _tempfile.TemporaryDirectory()
        store = LocalWorkExperienceStore(root=Path(_tmp.name) / "we_test")
        retriever = WorkExperienceRetriever(store, max_results=5)

        # Card 1: trigger_hint = 'input_text:send_a_di' — exact 10-char match with task_input1
        # task_input1 builds: first_key="input_text", first_val[:10]="send a di"
        #   → task_hint="input_text:send_a_di"
        # "input_text:send_a_di".startswith("input_text:send_a_di") = True
        card1 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["discord", "notification", "message"],
            trigger_hint="input_text:send_a_di",
            title="Discord send message",
            what_happened="",
            what_worked=["Use code blocks for Discord"],
            what_failed=[],
            guidance="Format with ```code block```",
            avoidance=[],
            confidence=0.9,
            source_task_id="task-1",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=["discord"],
            status=WorkExperienceStatus.APPROVED,
        )
        store.save(card1)

        # Card 2: trigger_hint = 'input_data:error_in' — matches task hint via prefix
        # task_input2 builds: first_key="input_data", first_val[:10]="error in"
        #   → task_hint="input_data:error_in"
        # "input_data:error_in".startswith("input_data:error_in") = True
        card2 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["logging", "debug", "error"],
            trigger_hint="input_data:error_in",
            title="Error logging",
            what_happened="",
            what_worked=["Log at ERROR level"],
            what_failed=[],
            guidance="Use logging.error()",
            avoidance=[],
            confidence=0.8,
            source_task_id="task-2",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=["logging"],
            status=WorkExperienceStatus.APPROVED,
        )
        store.save(card2)

        # Card 3: trigger_hint = 'output:format' — NO prefix overlap with any task hint
        card3 = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["format", "output"],
            trigger_hint="output:format",
            title="Output formatting",
            what_happened="",
            what_worked=["Pretty print"],
            what_failed=[],
            guidance="Use pprint",
            avoidance=[],
            confidence=0.7,
            source_task_id="task-3",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=["format"],
            status=WorkExperienceStatus.APPROVED,
        )
        store.save(card3)

        # Test 1: exact 10-char prefix match (card hint == task hint)
        task_input1 = {"input_text": "send a discord notification"}
        results1 = retriever.retrieve_for_task(task_input1)
        assert any(
            c.trigger_hint == "input_text:send_a_di" for c in results1
        ), f"Expected card1 via prefix match, got {[c.trigger_hint for c in results1]}"

        # Test 2: prefix match where card hint is longer than task hint
        # task_hint = "input_data:error_in", card hint = "input_data:error_in" (same)
        task_input2 = {"input_data": "error in the system crash"}
        results2 = retriever.retrieve_for_task(task_input2)
        assert any(
            c.trigger_hint == "input_data:error_in" for c in results2
        ), f"Expected card2 via prefix match, got {[c.trigger_hint for c in results2]}"

        # Test 3: no prefix match → falls back to keyword-only
        # task_hint = "output:render_th", no card starts with this
        # keywords = ["render","the","formatted","table","output"]
        # card3 has ["format","output"] → overlap with "output"=1 → card3 returned
        task_input3 = {"output": "render the formatted table output"}
        results3 = retriever.retrieve_for_task(task_input3)
        assert (
            len(results3) >= 1
        ), f"Expected keyword-only fallback to return something, got {results3}"

        # Test 4: verify startswith mechanics — CARD.startswith(TASK_HINT)
        assert "input_text:send_a_di".startswith(
            "input_text:send_a_di",
        )  # exact
        assert "input_text:send_a_discord".startswith(
            "input_text:send_a_di",
        )  # card longer
        assert not "input_text:send_a_di".startswith(
            "input_text:send_a_discord",
        )  # task longer
        assert not "output:format".startswith(
            "input_text:send_a_di",
        )  # different prefix


# =============================================================================
# Governance Observability Tests
# =============================================================================


class TestWorkExperienceGovernance:
    """Tests for governance observability methods on LocalWorkExperienceStore."""

    def test_get_all_stats_empty(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """stats returns zeros when no cards exist."""
        stats = store.get_all_stats()
        assert stats["total_cards"] == 0
        assert stats["total_hits"] == 0
        assert stats["total_effective_uses"] == 0
        assert stats["avg_confidence"] == 0.0

    def test_get_all_stats_with_cards(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """stats returns correct aggregate values."""
        for i in range(3):
            card = WorkExperience(
                scope=WorkExperienceScope.GLOBAL,
                trigger_keywords=["test"],
                trigger_hint="test:hint",
                title=f"Card {i}",
                what_happened="",
                what_worked=["item"],
                what_failed=[],
                guidance="",
                avoidance="",
                confidence=0.7 + i * 0.1,
                source_task_id=f"task-{i}",
                source_session_id="session-1",
                source_trace_id="trace-1",
                applicability_tags=[],
                status=WorkExperienceStatus.APPROVED,
            )
            store.save(card)

        stats = store.get_all_stats()
        assert stats["total_cards"] == 3
        assert stats["total_hits"] == 0  # not incremented yet
        assert stats["total_effective_uses"] == 0
        assert abs(stats["avg_confidence"] - 0.8) < 0.01

    def test_get_top_effective_cards(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """top_effective returns cards sorted by effective_count desc."""
        for i in range(5):
            card = WorkExperience(
                scope=WorkExperienceScope.GLOBAL,
                trigger_keywords=["test"],
                trigger_hint=f"test:hint{i}",
                title=f"Card {i}",
                what_happened="",
                what_worked=["item"],
                what_failed=[],
                guidance="",
                avoidance="",
                confidence=0.8,
                source_task_id=f"task-{i}",
                source_session_id="session-1",
                source_trace_id="trace-1",
                applicability_tags=[],
                status=WorkExperienceStatus.APPROVED,
            )
            store.save(card)
            if i % 2 == 0:
                store.record_effective_use(card.experience_id)

        top = store.get_top_effective_cards(n=3)
        assert len(top) == 3
        # Even-indexed cards have effective_count >= 1, odd have 0
        assert all(c.effective_count >= top[-1].effective_count for c in top)

    def test_get_high_hit_low_effective_cards(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """Returns cards with high hits but low effective/hit ratio."""
        # Card A: 10 hits, 1 effective → ratio 0.1 (below 0.3 threshold)
        card_a = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test:a",
            title="Card A",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.8,
            source_task_id="task-a",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            status=WorkExperienceStatus.APPROVED,
        )
        store.save(card_a)
        for _ in range(10):
            store.increment_hit(card_a.experience_id)
        store.record_effective_use(card_a.experience_id)  # only 1 effective

        # Card B: 5 hits, 4 effective → ratio 0.8 (above threshold)
        card_b = WorkExperience(
            scope=WorkExperienceScope.GLOBAL,
            trigger_keywords=["test"],
            trigger_hint="test:b",
            title="Card B",
            what_happened="",
            what_worked=["item"],
            what_failed=[],
            guidance="",
            avoidance="",
            confidence=0.8,
            source_task_id="task-b",
            source_session_id="session-1",
            source_trace_id="trace-1",
            applicability_tags=[],
            status=WorkExperienceStatus.APPROVED,
        )
        store.save(card_b)
        for _ in range(5):
            store.increment_hit(card_b.experience_id)
        for _ in range(4):
            store.record_effective_use(card_b.experience_id)

        alerts = store.get_high_hit_low_effective_cards(
            min_hits=5,
            effective_ratio_threshold=0.3,
        )
        assert len(alerts) == 1
        assert alerts[0].title == "Card A"


# =============================================================================
# Integration: Extractor + Store + Retriever
# =============================================================================


class TestWorkExperienceIntegration:
    """End-to-end integration: extract -> save -> retrieve."""

    def test_full_pipeline(
        self,
        store: LocalWorkExperienceStore,
        sample_context: TaskContext,
        sample_report: ReflectionReport,
    ) -> None:
        """Extract a card, save it, retrieve it."""
        extractor = WorkExperienceExtractor(store)
        card = extractor.extract(sample_report, sample_context)
        assert card is not None
        # Phase 6: mark as APPROVED so retriever returns it (default filter)
        card.status = WorkExperienceStatus.APPROVED
        store.save(card)

        retriever = WorkExperienceRetriever(store)
        results = retriever.retrieve(
            scope=WorkExperienceScope.SESSION,
            keywords=["csv", "file"],
        )

        assert len(results) >= 1
        retrieved_ids = {c.experience_id for c in results}
        assert card.experience_id in retrieved_ids

    def test_multiple_cards_retrievable_by_keyword(
        self,
        store: LocalWorkExperienceStore,
    ) -> None:
        """Multiple cards can be saved and retrieved by shared keyword."""
        for i, keyword in enumerate(["python", "web", "database"]):
            report = ReflectionReport(
                report_id=uuid4(),
                task_id=f"task-{i}",
                session_id="session-1",
                trace_id="trace-1",
                what_worked=[f"Worked with {keyword}"],
                what_failed=[],
                root_cause="",
                next_time_strategy=f"Use {keyword} next time",
                confidence=0.7,
                has_human_feedback=False,
                policy_suggestions=[],
                created_at=datetime.now(timezone.utc),
            )
            context = TaskContext(
                task_id=f"task-{i}",
                session_id="session-1",
                trace_id="trace-1",
                task_input={"type": keyword, "query": f"{keyword} task"},
                execution_trace=[],
                task_result=TaskResult(
                    unit_id=f"unit-{i}",
                    task_id=f"task-{i}",
                    status=TaskStatus.SUCCESS,
                    confidence=0.8,
                    output_data={},
                    artifacts={},
                    error_message=None,
                    retry_count=0,
                    executed_at=datetime.now(timezone.utc),
                ),
                execution_time_ms=100,
            )
            extractor = WorkExperienceExtractor(store)
            card = extractor.extract(report, context)
            if card:
                # Phase 6: mark as APPROVED so retriever returns it
                card.status = WorkExperienceStatus.APPROVED
                store.save(card)

        retriever = WorkExperienceRetriever(store)
        python_results = retriever.retrieve(keywords=["python"])
        assert len(python_results) >= 1
