# -*- coding: utf-8 -*-
"""Test real model stage execution - Step 9.

Tests that _execute_stage() no longer returns "Processed:" placeholder
and uses the real LLM service when enabled.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestRealModelStageExecution:
    """Tests for real model stage execution."""

    def test_execute_stage_uses_llm_when_enabled(self):
        """Test that _execute_stage uses real LLM when flag is enabled."""
        from hubos.core.execution.orchestrator import ExecutionOrchestrator
        from hubos.core.execution.task_store import Task, TaskStage
        from hubos.core.execution.event_store import EventStore

        # Mock the feature flags and LLM runtime
        with patch(
            "hubos.core.infra.feature_flags.get_feature_flags",
        ) as mock_ff, patch(
            "hubos.core.llm.runtime.get_llm_runtime",
        ) as mock_runtime:
            mock_flags = MagicMock()
            mock_flags.enable_real_model_execution = True
            mock_ff.return_value = mock_flags

            mock_llm = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.text = "This is a strategic response from the AI model"
            mock_result.error = None
            mock_llm.generate_for_stage.return_value = mock_result
            mock_runtime.return_value = mock_llm

            event_store = EventStore()
            orchestrator = ExecutionOrchestrator(event_store=event_store)

            task = Task(
                task_id="test-task-1",
                trace_id="trace-1",
                input_text="What is the meaning of life?",
                requested_workflow="one_person_default",
            )

            stage_def = MagicMock()
            available_roles = {"ceo": True}

            result = orchestrator._execute_stage(
                task,
                TaskStage.CEO,
                stage_def,
                available_roles,
            )

            assert (
                result["content"]
                == "This is a strategic response from the AI model"
            )
            assert "Processed" not in result["content"]

    def test_execute_stage_mock_when_flag_disabled(self):
        """Test that _execute_stage uses mock when flag is disabled."""
        from hubos.core.execution.orchestrator import ExecutionOrchestrator
        from hubos.core.execution.task_store import Task, TaskStage
        from hubos.core.execution.event_store import EventStore

        with patch(
            "hubos.core.infra.feature_flags.get_feature_flags",
        ) as mock_ff:
            mock_flags = MagicMock()
            mock_flags.enable_real_model_execution = False
            mock_ff.return_value = mock_flags

            event_store = EventStore()
            orchestrator = ExecutionOrchestrator(event_store=event_store)

            task = Task(
                task_id="test-task-3",
                trace_id="trace-3",
                input_text="Test input",
                requested_workflow="one_person_default",
            )

            stage_def = MagicMock()
            available_roles = {"ceo": True}

            result = orchestrator._execute_stage(
                task,
                TaskStage.CEO,
                stage_def,
                available_roles,
            )

            assert "Processed" in result["content"]
            assert "Test input" in result["content"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
