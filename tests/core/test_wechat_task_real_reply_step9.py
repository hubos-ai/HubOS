# -*- coding: utf-8 -*-
"""Test WeChat task real reply - Step 9.

Tests that WeChat tasks eventually use real response_text
from the model, not mock/echo responses.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestWeChatTaskRealReply:
    """Tests for WeChat task with real model response."""

    def test_stage_execution_for_wechat_task(self):
        """Test that stage execution returns real AI response for WeChat task."""
        from hubos.core.execution.orchestrator import ExecutionOrchestrator
        from hubos.core.execution.task_store import Task, TaskStage
        from hubos.core.execution.event_store import EventStore

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
            mock_result.text = "这是来自真实AI模型的回答，不包含任何Processed标签。"
            mock_llm.generate_for_stage.return_value = mock_result
            mock_runtime.return_value = mock_llm

            event_store = EventStore()
            task_store = MagicMock()

            orchestrator = ExecutionOrchestrator(
                task_store=task_store,
                event_store=event_store,
            )

            task = Task(
                task_id="wechat-task-001",
                trace_id="trace-wechat-001",
                input_text="你是谁",
                requested_workflow="one_person_default",
                channel="wechat",
                session_id="wechat:o9cq807bAuJP6uzpqmOdDe5EKTS4@im.wechat",
            )

            stage_def = MagicMock()
            available_roles = {"review": True}

            result = orchestrator._execute_stage(
                task,
                TaskStage.REVIEW,
                stage_def,
                available_roles,
            )

            # Verify response contains real AI output
            assert "Processed:" not in result["content"]
            assert result["content"] == "这是来自真实AI模型的回答，不包含任何Processed标签。"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
