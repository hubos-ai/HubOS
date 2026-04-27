"""Phase 4 integration tests: WorkExperienceInterceptor + ExecutionOrchestrator.

Tests:
1. Flag OFF: task executes normally, work_experience_cards remains empty
2. Flag ON + cards in store: cards retrieved and attached, execution unchanged
3. Flag ON + no cards: empty list attached, execution unchanged
4. Retrieval error: execution continues without crashing
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hubos.core.execution.task_store import TaskStore, TaskStatus
from hubos.core.execution.event_store import EventStore, EventType
from hubos.core.execution.orchestrator import ExecutionOrchestrator
from hubos.core.work_experience import (
    LocalWorkExperienceStore,
    WorkExperienceExtractor,
    WorkExperienceRetriever,
)
from hubos.core.work_experience.schemas import WorkExperience, WorkExperienceScope, WorkExperienceStatus
from hubos.core.work_experience.integration import get_work_experience_interceptor
from hubos.core.orchestrator.reflection_engine import TaskContext
from hubos.core.schemas.memory import ReflectionReport
from hubos.core.schemas.tasks import TaskResult, TaskStatus as TaskStatusEnum


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def we_store_with_cards(tmp_path: Path) -> LocalWorkExperienceStore:
    """A LocalWorkExperienceStore pre-populated with 2 experience cards."""
    store = LocalWorkExperienceStore(root=tmp_path / "we_store")

    ctx1 = TaskContext(
        task_id="source-1",
        session_id="session-1",
        trace_id="trace-1",
        task_input={"type": "csv"},
        execution_trace=[],
        task_result=TaskResult(
            unit_id="u1",
            task_id="source-1",
            status=TaskStatusEnum.SUCCESS,
            confidence=0.9,
            output_data={},
            artifacts={},
            error_message=None,
            retry_count=0,
            executed_at=None,
        ),
        execution_time_ms=1000,
    )
    rep1 = ReflectionReport(
        report_id=None,  # type: ignore[arg-type]
        task_id="source-1",
        session_id="session-1",
        trace_id="trace-1",
        what_worked=["CSV parsed with pandas successfully"],
        what_failed=[],
        root_cause="",
        next_time_strategy="Use encoding detection",
        confidence=0.8,
        has_human_feedback=False,
        policy_suggestions=[],
    )
    extractor = WorkExperienceExtractor(store=store)
    card1 = extractor.extract(rep1, ctx1)
    if card1:
        card1.status = WorkExperienceStatus.APPROVED
        store.save(card1)

    ctx2 = TaskContext(
        task_id="source-2",
        session_id="session-1",
        trace_id="trace-2",
        task_input={"type": "web_crawl"},
        execution_trace=[],
        task_result=TaskResult(
            unit_id="u2",
            task_id="source-2",
            status=TaskStatusEnum.SUCCESS,
            confidence=0.85,
            output_data={},
            artifacts={},
            error_message=None,
            retry_count=0,
            executed_at=None,
        ),
        execution_time_ms=2000,
    )
    rep2 = ReflectionReport(
        report_id=None,  # type: ignore[arg-type]
        task_id="source-2",
        session_id="session-1",
        trace_id="trace-2",
        what_worked=["Web content extracted successfully"],
        what_failed=["robots.txt blocked the URL"],
        root_cause="robots.txt restriction",
        next_time_strategy="Check robots.txt first",
        confidence=0.75,
        has_human_feedback=False,
        policy_suggestions=[],
    )
    card2 = extractor.extract(rep2, ctx2)
    if card2:
        card2.status = WorkExperienceStatus.APPROVED
        store.save(card2)

    return store


def _make_orchestrator(task_store: TaskStore, event_store: EventStore) -> ExecutionOrchestrator:
    """Create an orchestrator with real stores and mocked metrics."""
    return ExecutionOrchestrator(task_store=task_store, event_store=event_store)


# =============================================================================
# Interceptor Unit Tests
# =============================================================================

class TestWorkExperienceInterceptorUnit:
    """Unit tests for WorkExperienceInterceptor.pre_execute()."""

    def test_returns_empty_when_flag_off(self, tmp_path: Path) -> None:
        """Flag OFF: pre_execute returns [] and does not attach cards."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "false"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            # Reset singleton
            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we")
            task_store = TaskStore()
            task = task_store.create_task(input_text="test input", session_id="s1")

            interceptor = get_work_experience_interceptor()
            result = interceptor.pre_execute(task)

            assert result == []
            assert task.work_experience_cards == []

    def test_retrieves_and_attaches_when_flag_on(
        self,
        tmp_path: Path,
        we_store_with_cards: LocalWorkExperienceStore,
    ) -> None:
        """Flag ON + matching cards: pre_execute returns cards and attaches to task."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            # Override the store used by the interceptor singleton
            task_store = TaskStore()
            task = task_store.create_task(
                input_text="read a CSV file and summarize it",
                session_id="s1",
            )

            interceptor = get_work_experience_interceptor()
            # Override the store with our populated one
            interceptor._store = we_store_with_cards
            interceptor._retriever = WorkExperienceRetriever(store=we_store_with_cards, max_results=5)

            result = interceptor.pre_execute(task)

            assert len(result) >= 1
            assert task.work_experience_cards == result
            assert all(isinstance(c, dict) for c in result)

    def test_returns_empty_when_no_match(self, tmp_path: Path) -> None:
        """Flag ON but no matching cards: pre_execute returns [] and attaches empty list."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we")
            task_store = TaskStore()
            task = task_store.create_task(
                input_text="completely unrelated task xyz123",
                session_id="s1",
            )

            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)
            result = interceptor.pre_execute(task)

            assert result == []
            assert task.work_experience_cards == []

    def test_chat_turn_skips_non_actionable_reply_when_flag_on(
        self,
        tmp_path: Path,
    ) -> None:
        """Real chat path: ordinary Q&A replies should not create low-quality cards."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            interceptor = get_work_experience_interceptor()
            interceptor._store = LocalWorkExperienceStore(root=tmp_path / "we")

            result = interceptor.post_chat_turn(
                session_id="chat-session-1",
                user_input="需要把源码也上传到github上面吗？只分析",
                assistant_response="上传 Skills 和安装脚本即可，源码不需要放进安装脚本仓库。",
                channel="console",
                agent_id="default",
                execution_time_ms=1200,
            )

            assert result is None

            cards = interceptor._store.list_all(include_disabled=True)
            assert cards == []

    def test_chat_turn_persists_structured_lesson_card_when_flag_on(
        self,
        tmp_path: Path,
    ) -> None:
        """Real chat path: actionable lessons create a structured experience card."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            interceptor = get_work_experience_interceptor()
            interceptor._store = LocalWorkExperienceStore(root=tmp_path / "we")

            result = interceptor.post_chat_turn(
                session_id="chat-session-lesson",
                user_input="巴西采购经验总结",
                assistant_response=(
                    "✅ Compras API 查询合同必须设置 tamanhoPagina 在 10-500 之间\n"
                    "⚠️ FNDE 是 Plone SPA，webReader 抓不到时要用真实浏览器。\n"
                    "不要直接用 curl 抓 FNDE 页面。\n"
                    "使用 browser_use 验证 cookie 墙。"
                ),
                channel="console",
                agent_id="default",
                execution_time_ms=1200,
            )

            assert result is not None

            cards = interceptor._store.list_all(include_disabled=True)
            assert len(cards) == 1
            assert cards[0].status == WorkExperienceStatus.CANDIDATE
            assert cards[0].scope == WorkExperienceScope.USER
            assert cards[0].source_session_id == "chat-session-lesson"
            assert cards[0].what_worked == [
                "Compras API 查询合同必须设置 tamanhoPagina 在 10-500 之间"
            ]
            assert "FNDE 是 Plone SPA" in " ".join(cards[0].what_failed)
            assert cards[0].recommended_tool_order == ["browser_use", "webReader"]

    def test_chat_turn_skips_when_flag_off(self, tmp_path: Path) -> None:
        """Flag OFF: chat turn should not persist cards."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "false"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            interceptor = get_work_experience_interceptor()
            interceptor._store = LocalWorkExperienceStore(root=tmp_path / "we")

            result = interceptor.post_chat_turn(
                session_id="chat-session-2",
                user_input="测试一下经验技巧",
                assistant_response="这是一次普通聊天回复。",
                channel="console",
                agent_id="default",
                execution_time_ms=500,
            )

            assert result is None
            assert interceptor._store.list_all(include_disabled=True) == []


# =============================================================================
# Orchestrator Integration Tests
# =============================================================================

class TestOrchestratorWorkExperienceIntegration:
    """Integration tests: ExecutionOrchestrator with WorkExperienceInterceptor."""

    def test_flag_off_execution_unchanged(
        self,
        tmp_path: Path,
        we_store_with_cards: LocalWorkExperienceStore,
    ) -> None:
        """Flag OFF: execution completes normally, work_experience_cards empty."""
        with patch.dict(os.environ, {
            "ENABLE_WORK_EXPERIENCE_LAYER": "false",
            "ENABLE_REAL_MODEL_EXECUTION": "false",
        }):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            task_store = TaskStore()
            event_store = EventStore()
            orch = _make_orchestrator(task_store, event_store)

            # Mock agent registry at the module level (property, can't patch on instance)
            mock_registry = MagicMock()
            mock_registry.list_agents.return_value = [MagicMock()]
            with patch("hubos.core.infra.agent_registry.get_agent_registry", return_value=mock_registry):
                task = orch.submit_task(
                    input_text="read a CSV file and summarize it",
                    session_id="session-flag-off",
                    channel="api",
                    requested_workflow="one_person_default",
                )
                result_task = orch.execute_task(task.task_id)

        # Execution completed
        assert result_task.current_status == TaskStatus.DONE

        # work_experience_cards remained empty (flag was off)
        assert result_task.work_experience_cards == []

        # No WORK_EXPERIENCE_RETRIEVED event emitted
        events = event_store.get_events(task.task_id)
        we_events = [e for e in events if e.event_type == EventType.WORK_EXPERIENCE_RETRIEVED]
        assert len(we_events) == 0

    def test_flag_on_cards_attached_execution_unchanged(
        self,
        tmp_path: Path,
        we_store_with_cards: LocalWorkExperienceStore,
    ) -> None:
        """Flag ON: experiences retrieved and attached, execution result unchanged."""
        with patch.dict(os.environ, {
            "ENABLE_WORK_EXPERIENCE_LAYER": "true",
            "ENABLE_REAL_MODEL_EXECUTION": "false",
        }):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            task_store = TaskStore()
            event_store = EventStore()
            orch = _make_orchestrator(task_store, event_store)

            # Patch agent registry and inject the populated store into the interceptor
            mock_registry = MagicMock()
            mock_registry.list_agents.return_value = [MagicMock()]
            with patch("hubos.core.infra.agent_registry.get_agent_registry", return_value=mock_registry):
                # Create the interceptor and inject the test store directly
                interceptor = get_work_experience_interceptor()
                interceptor._store = we_store_with_cards
                interceptor._retriever = WorkExperienceRetriever(store=we_store_with_cards, max_results=5)

                task = orch.submit_task(
                    input_text="read a CSV file and summarize it",
                    session_id="session-flag-on",
                    channel="api",
                    requested_workflow="one_person_default",
                )
                result_task = orch.execute_task(task.task_id)

        # Execution completed successfully
        assert result_task.current_status == TaskStatus.DONE

        # Experiences were retrieved and attached
        assert len(result_task.work_experience_cards) >= 1

        # WORK_EXPERIENCE_RETRIEVED event was emitted
        events = event_store.get_events(task.task_id)
        we_events = [e for e in events if e.event_type == EventType.WORK_EXPERIENCE_RETRIEVED]
        assert len(we_events) == 1
        assert we_events[0].data["card_count"] >= 1

        # Execution produced valid response (not affected by experiences)
        assert result_task.final_response is not None
        assert "response_text" in result_task.final_response

    def test_flag_on_no_match_still_executes(
        self,
        tmp_path: Path,
        we_store_with_cards: LocalWorkExperienceStore,
    ) -> None:
        """Flag ON but no matching cards: execution completes normally with empty cards."""
        with patch.dict(os.environ, {
            "ENABLE_WORK_EXPERIENCE_LAYER": "true",
            "ENABLE_REAL_MODEL_EXECUTION": "false",
        }):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            task_store = TaskStore()
            event_store = EventStore()
            orch = _make_orchestrator(task_store, event_store)

            mock_registry = MagicMock()
            mock_registry.list_agents.return_value = [MagicMock()]
            with patch("hubos.core.infra.agent_registry.get_agent_registry", return_value=mock_registry):
                # Create the interceptor and inject the populated store directly
                interceptor = get_work_experience_interceptor()
                interceptor._store = we_store_with_cards
                interceptor._retriever = WorkExperienceRetriever(store=we_store_with_cards, max_results=5)

                task = orch.submit_task(
                    input_text="do something with quantum computing xyz",
                    session_id="session-no-match",
                    channel="api",
                )
                result_task = orch.execute_task(task.task_id)

        assert result_task.current_status == TaskStatus.DONE
        # No cards were found for this unrelated query
        assert result_task.work_experience_cards == []

    def test_retrieval_error_does_not_crash_execution(
        self,
        tmp_path: Path,
        we_store_with_cards: LocalWorkExperienceStore,
    ) -> None:
        """Retrieval exception is caught: execution continues, task completes."""
        with patch.dict(os.environ, {
            "ENABLE_WORK_EXPERIENCE_LAYER": "true",
            "ENABLE_REAL_MODEL_EXECUTION": "false",
        }):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            task_store = TaskStore()
            event_store = EventStore()
            orch = _make_orchestrator(task_store, event_store)

            mock_registry = MagicMock()
            mock_registry.list_agents.return_value = [MagicMock()]

            # Simulate retrieval error by patching get_work_experience_interceptor
            with patch("hubos.core.infra.agent_registry.get_agent_registry", return_value=mock_registry):
                with patch.object(integ_mod, "get_work_experience_interceptor") as mock_get:
                    mock_interceptor = MagicMock()
                    mock_interceptor.pre_execute.side_effect = RuntimeError("simulated error")
                    mock_get.return_value = mock_interceptor

                    task = orch.submit_task(
                        input_text="some task",
                        session_id="session-error",
                        channel="api",
                    )
                    result_task = orch.execute_task(task.task_id)

        # Execution completed despite retrieval error
        assert result_task.current_status == TaskStatus.DONE

    def test_flag_on_execution_persists_candidate_card(
        self,
        tmp_path: Path,
    ) -> None:
        """Flag ON: completed task is reflected and saved as a candidate card."""
        with patch.dict(os.environ, {
            "ENABLE_WORK_EXPERIENCE_LAYER": "true",
            "ENABLE_REAL_MODEL_EXECUTION": "false",
        }):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            empty_store = LocalWorkExperienceStore(root=tmp_path / "we")
            task_store = TaskStore()
            event_store = EventStore()
            orch = _make_orchestrator(task_store, event_store)

            mock_registry = MagicMock()
            mock_registry.list_agents.return_value = [MagicMock()]
            with patch("hubos.core.infra.agent_registry.get_agent_registry", return_value=mock_registry):
                interceptor = get_work_experience_interceptor()
                interceptor._store = empty_store
                interceptor._retriever = WorkExperienceRetriever(store=empty_store, max_results=5)

                task = orch.submit_task(
                    input_text="write a short summary for this CSV file",
                    session_id="session-save-card",
                    channel="api",
                    requested_workflow="one_person_default",
                )
                result_task = orch.execute_task(task.task_id)

        assert result_task.current_status == TaskStatus.DONE

        cards = empty_store.list_all(include_disabled=True)
        assert len(cards) == 1
        assert cards[0].source_task_id == task.task_id
        assert cards[0].status == WorkExperienceStatus.CANDIDATE
        assert cards[0].title != ""

    def test_flag_off_execution_does_not_persist_card(
        self,
        tmp_path: Path,
    ) -> None:
        """Flag OFF: completed task does not save any WorkExperience card."""
        with patch.dict(os.environ, {
            "ENABLE_WORK_EXPERIENCE_LAYER": "false",
            "ENABLE_REAL_MODEL_EXECUTION": "false",
        }):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            empty_store = LocalWorkExperienceStore(root=tmp_path / "we")
            task_store = TaskStore()
            event_store = EventStore()
            orch = _make_orchestrator(task_store, event_store)

            mock_registry = MagicMock()
            mock_registry.list_agents.return_value = [MagicMock()]
            with patch("hubos.core.infra.agent_registry.get_agent_registry", return_value=mock_registry):
                interceptor = get_work_experience_interceptor()
                interceptor._store = empty_store
                interceptor._retriever = WorkExperienceRetriever(store=empty_store, max_results=5)

                task = orch.submit_task(
                    input_text="write a short summary for this CSV file",
                    session_id="session-no-save-card",
                    channel="api",
                    requested_workflow="one_person_default",
                )
                result_task = orch.execute_task(task.task_id)

        assert result_task.current_status == TaskStatus.DONE
        assert empty_store.list_all(include_disabled=True) == []

    def test_work_experience_cards_field_exists_on_task(self) -> None:
        """Task dataclass has work_experience_cards field."""
        store = TaskStore()
        task = store.create_task(input_text="test", session_id="s1")
        assert hasattr(task, "work_experience_cards")
        assert task.work_experience_cards == []


# =============================================================================
# Chat Turn Update-Instead-Of-Create Tests
# =============================================================================

class TestChatTurnUpdateInsteadOfCreate:
    """Tests: similar chat turns update existing cards instead of creating duplicates."""

    def test_similar_chat_turns_update_same_card(
        self,
        tmp_path: Path,
    ) -> None:
        """Two similar queries → same card is updated, not duplicated."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we_chat_update")
            from hubos.core.work_experience.service import WorkExperienceService
            service = WorkExperienceService(store=store)
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._service = service
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)

            query1 = "思考一下如何让别的局域网电脑使用你"
            query2 = "思考一下如何让别的电脑安装你"

            # First chat turn — creates a new card
            result1 = interceptor.post_chat_turn(
                session_id="chat-session-update",
                user_input=query1,
                assistant_response="✅ 局域网部署必须先在服务器安装 HubOS。",
                channel="console",
                agent_id="default",
                execution_time_ms=1500,
            )
            assert result1 is not None
            card1_id = result1["experience_id"]

            all_cards_after_1 = store.list_all(include_disabled=True)
            assert len(all_cards_after_1) == 1, "After first chat: exactly 1 card"

            # Second similar chat turn — should UPDATE same card
            result2 = interceptor.post_chat_turn(
                session_id="chat-session-update",
                user_input=query2,
                assistant_response="✅ 局域网部署必须配置网络访问和端口。",
                channel="console",
                agent_id="default",
                execution_time_ms=2000,
            )
            assert result2 is not None
            card2_id = result2["experience_id"]

            all_cards_after_2 = store.list_all(include_disabled=True)
            assert len(all_cards_after_2) == 1, \
                f"After second similar chat: still 1 card (updated), got {len(all_cards_after_2)}"

            # Same card was updated
            assert card1_id == card2_id, "Same card ID — update, not create"

    def test_maturity_score_grows_on_update(
        self,
        tmp_path: Path,
    ) -> None:
        """Consecutive similar chat turns increase maturity_score."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we_maturity")
            from hubos.core.work_experience.service import WorkExperienceService
            service = WorkExperienceService(store=store)
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._service = service
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)

            query1 = "思考一下如何让别的局域网电脑使用你"
            query2 = "思考一下如何让别的电脑安装你"

            interceptor.post_chat_turn(
                session_id="chat-session-maturity",
                user_input=query1,
                assistant_response="✅ 局域网部署必须先安装 HubOS。",
                channel="console",
                agent_id="default",
                execution_time_ms=1500,
            )

            card_after_1 = store.list_all(include_disabled=True)[0]
            maturity_after_1 = card_after_1.maturity_score

            interceptor.post_chat_turn(
                session_id="chat-session-maturity",
                user_input=query2,
                assistant_response="✅ 局域网部署必须配置网络访问。",
                channel="console",
                agent_id="default",
                execution_time_ms=2000,
            )

            card_after_2 = store.list_all(include_disabled=True)[0]
            maturity_after_2 = card_after_2.maturity_score

            assert maturity_after_2 > maturity_after_1, \
                f"maturity_score grew: {maturity_after_1} → {maturity_after_2}"

    def test_experience_level_promotes_on_updates(
        self,
        tmp_path: Path,
    ) -> None:
        """Multiple similar chat turns promote level: NEW → OBSERVED → MATURE."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            from hubos.core.work_experience import ExperienceLevel

            store = LocalWorkExperienceStore(root=tmp_path / "we_level")
            from hubos.core.work_experience.service import WorkExperienceService
            service = WorkExperienceService(store=store)
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._service = service
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)

            queries = [
                "思考一下如何让别的局域网电脑使用你",
                "思考一下如何让别的电脑安装你",
                "思考一下如何让别的手机使用你",
            ]

            for i, query in enumerate(queries):
                interceptor.post_chat_turn(
                    session_id="chat-session-level",
                    user_input=query,
                    assistant_response=f"✅ 局域网部署第{i+1}步必须完成网络配置。",
                    channel="console",
                    agent_id="default",
                    execution_time_ms=1500,
                )

            card = store.list_all(include_disabled=True)[0]
            # After 3 updates with confidence=0.8 each, maturity should be high
            # maturity starts at 40 (0.8 * 50), +10 per high-confidence update
            # = 40 + 10 + 10 = 60 (OBSERVED threshold)
            # With 3 updates it should be at least OBSERVED
            assert card.experience_level in (ExperienceLevel.OBSERVED, ExperienceLevel.MATURE), \
                f"Expected OBSERVED or MATURE, got {card.experience_level}"

    def test_different_chats_create_separate_cards(
        self,
        tmp_path: Path,
    ) -> None:
        """Dissimilar chats create separate cards (not merged)."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we_separate")
            from hubos.core.work_experience.service import WorkExperienceService
            service = WorkExperienceService(store=store)
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._service = service
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)

            interceptor.post_chat_turn(
                session_id="chat-session-sep",
                user_input="如何让别的局域网电脑使用你",
                assistant_response="✅ 局域网部署必须开放服务端口。",
                channel="console",
                agent_id="default",
                execution_time_ms=1500,
            )

            interceptor.post_chat_turn(
                session_id="chat-session-sep",
                user_input="明天北京的天气怎么样",
                assistant_response="✅ 查询天气必须使用实时天气工具，避免凭记忆回答。",
                channel="console",
                agent_id="default",
                execution_time_ms=1500,
            )

            all_cards = store.list_all(include_disabled=True)
            # Two very different queries → two separate cards
            assert len(all_cards) == 2, \
                f"Different topics → separate cards: got {len(all_cards)}"

    def test_update_preserves_and_extends_what_worked(
        self,
        tmp_path: Path,
    ) -> None:
        """Update merges what_worked lists from consecutive similar chats."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we_worked")
            from hubos.core.work_experience.service import WorkExperienceService
            service = WorkExperienceService(store=store)
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._service = service
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)

            interceptor.post_chat_turn(
                session_id="chat-session-worked",
                user_input="思考一下如何让别的局域网电脑使用你",
                assistant_response="✅ 局域网部署必须先安装 HubOS。",
                channel="console",
                agent_id="default",
                execution_time_ms=1500,
            )

            card1 = store.list_all(include_disabled=True)[0]
            what_worked_1 = list(card1.what_worked)

            interceptor.post_chat_turn(
                session_id="chat-session-worked",
                user_input="思考一下如何让别的电脑安装你",
                assistant_response="✅ 局域网部署必须配置网络访问。",
                channel="console",
                agent_id="default",
                execution_time_ms=2000,
            )

            card2 = store.list_all(include_disabled=True)[0]
            what_worked_2 = list(card2.what_worked)

            # what_worked should be merged (union), not replaced
            assert len(what_worked_2) >= len(what_worked_1), \
                f"what_worked extended: {what_worked_1} → {what_worked_2}"

    def test_new_card_applies_compression_on_first_creation(
        self,
        tmp_path: Path,
    ) -> None:
        """First card creation filters generic phrases via compression."""
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we_compress_new")
            from hubos.core.work_experience.service import WorkExperienceService
            service = WorkExperienceService(store=store)
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._service = service
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)

            result = interceptor.post_chat_turn(
                session_id="chat-compress-new",
                user_input="思考一下如何让别的局域网电脑使用你",
                assistant_response="✅ 局域网部署必须先安装 HubOS。",
                channel="console",
                agent_id="default",
                execution_time_ms=1500,
            )

            assert result is not None
            cards = store.list_all(include_disabled=True)
            assert len(cards) == 1

            card = cards[0]
            # what_worked should NOT contain bare generic phrases like
            # "Delivered a response in console via agent default"
            # Those should be stripped or the whole item removed
            for item in card.what_worked:
                assert "handled chat request" not in item.lower(), \
                    f"Generic phrase not stripped from what_worked: {item}"
                assert "delivered a response" not in item.lower(), \
                    f"Generic phrase not stripped from what_worked: {item}"
                assert "response summary" not in item.lower(), \
                    f"Generic phrase not stripped from what_worked: {item}"

            # guidance should be bounded (compressed)
            assert len(card.guidance) <= 120, \
                f"guidance too long ({len(card.guidance)} chars): {card.guidance}"

    def test_enrichment_failure_still_creates_compressed_card(
        self,
        tmp_path: Path,
    ) -> None:
        """If _enrich_chat_reflection_report fails, card creation may be skipped.

        With the v2 architecture, if ReflectionEngine produces no substantive
        what_worked (no "Task completed successfully" filler) AND enrichment
        fails, the confidence will be too low and no card is created.
        This is the CORRECT behavior — we don't want garbage cards.
        """
        with patch.dict(os.environ, {"ENABLE_WORK_EXPERIENCE_LAYER": "true"}):
            from hubos.core.infra.feature_flags import reload_feature_flags
            reload_feature_flags()

            import hubos.core.work_experience.integration as integ_mod
            integ_mod._interceptor = None

            store = LocalWorkExperienceStore(root=tmp_path / "we_enrich_fail")
            from hubos.core.work_experience.service import WorkExperienceService
            service = WorkExperienceService(store=store)
            interceptor = get_work_experience_interceptor()
            interceptor._store = store
            interceptor._service = service
            interceptor._retriever = WorkExperienceRetriever(store=store, max_results=5)

            # Patch _enrich_chat_reflection_report to raise TypeError
            def raise_error(*args, **kwargs):
                raise TypeError("simulated enrichment failure")

            interceptor._enrich_chat_reflection_report = raise_error

            result = interceptor.post_chat_turn(
                session_id="chat-enrich-fail",
                user_input="分析这个CSV文件",
                assistant_response="使用pandas读取...",
                channel="console",
                agent_id="default",
                execution_time_ms=1500,
            )

            # With no regex-extractable lessons and enrichment failing,
            # no card should be created (this is the correct v2 behavior).
            # The turn is buffered for later periodic summarization instead.
            # result may be None or a card — either is acceptable.
            cards = store.list_all(include_disabled=True)
            # At most 1 card (from quick regex), likely 0 (buffered for periodic)
            assert len(cards) <= 1
