"""Test one_person_default real reply - Step 9.

Tests that the one_person_default workflow returns natural language
responses without "Processed:" or stage labels when using real model.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestOnePersonDefaultRealReply:
    """Tests for one_person_default with real model reply."""

    def test_stage_output_is_natural_language(self):
        """Test that stage outputs are natural language, not Processed:."""
        from hubos.core.execution.orchestrator import ExecutionOrchestrator
        from hubos.core.execution.task_store import Task, TaskStage
        from hubos.core.execution.event_store import EventStore

        with patch('hubos.core.infra.feature_flags.get_feature_flags') as mock_ff, \
             patch('hubos.core.llm.runtime.get_llm_runtime') as mock_runtime:
            mock_flags = MagicMock()
            mock_flags.enable_real_model_execution = True
            mock_ff.return_value = mock_flags

            mock_llm = MagicMock()

            review_result = MagicMock()
            review_result.success = True
            review_result.text = "这是最终的自然语言回复，不包含任何Processed标签。"

            mock_llm.generate_for_stage.return_value = review_result
            mock_runtime.return_value = mock_llm

            event_store = EventStore()
            task_store = MagicMock()
            orchestrator = ExecutionOrchestrator(
                task_store=task_store,
                event_store=event_store,
            )

            task = Task(
                task_id="test-review-1",
                trace_id="trace-review-1",
                input_text="你是谁",
                requested_workflow="one_person_default",
            )

            stage_def = MagicMock()
            available_roles = {"review": True}

            result = orchestrator._execute_stage(
                task, TaskStage.REVIEW, stage_def, available_roles
            )

            # Verify stage output is natural language
            assert "Processed:" not in result["content"]
            assert result["content"] == "这是最终的自然语言回复，不包含任何Processed标签。"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
