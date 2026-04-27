# -*- coding: utf-8 -*-
"""Test final response uses review stage only - Step 9.1.

Tests that final_response.response_text优先取review阶段输出，
不再拼接ceo/info/dev全部内容。
"""

import pytest
from unittest.mock import MagicMock, patch


class TestFinalResponseReviewOnly:
    """Tests for final response using review stage only."""

    def test_response_text_prefers_review_stage(self):
        """Test that response_text is from review stage, not concatenation."""
        from hubos.core.execution.orchestrator import ExecutionOrchestrator
        from hubos.core.execution.task_store import Task
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
            # Mock the cleaner to return the cleaned text
            mock_llm._clean_review_output.side_effect = lambda x: x.replace(
                "[REVIEW]",
                "",
            ).strip()

            # Only review has meaningful content
            stage_outputs = {
                "ceo": {"content": "CEO internal analysis", "confidence": 0.9},
                "info": {"content": "INFO internal notes", "confidence": 0.9},
                "dev": {
                    "content": "DEV implementation details",
                    "confidence": 0.9,
                },
                "review": {"content": "这是最终的用户面向答复，简洁自然。", "confidence": 0.9},
            }
            mock_llm.generate_for_stage.side_effect = (
                lambda stage, input, context: MagicMock(
                    success=True,
                    text=stage_outputs.get(stage, {}).get("content", ""),
                )
            )
            mock_runtime.return_value = mock_llm

            event_store = EventStore()
            task_store = MagicMock()
            orchestrator = ExecutionOrchestrator(
                task_store=task_store,
                event_store=event_store,
            )

            task = Task(
                task_id="test-review-only",
                trace_id="trace-review-only",
                input_text="测试问题",
                requested_workflow="one_person_default",
            )

            result = orchestrator._generate_response(task, stage_outputs)

            # response_text should be from review stage
            assert "这是最终的用户面向答复" in result["response_text"]
            # Should NOT be all stages concatenated
            assert "CEO internal analysis" not in result["response_text"]
            assert "INFO internal notes" not in result["response_text"]

    def test_internal_reasoning_preserves_all_stages(self):
        """Test that internal_reasoning preserves all stage outputs for debugging."""
        from hubos.core.execution.orchestrator import ExecutionOrchestrator
        from hubos.core.execution.task_store import Task
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
            mock_llm._clean_review_output.side_effect = lambda x: x

            stage_outputs = {
                "ceo": {"content": "CEO分析内容", "confidence": 0.9},
                "info": {"content": "INFO信息收集", "confidence": 0.9},
                "dev": {"content": "DEV开发细节", "confidence": 0.9},
                "review": {"content": "用户面向答复", "confidence": 0.9},
            }
            mock_runtime.return_value = mock_llm

            event_store = EventStore()
            task_store = MagicMock()
            orchestrator = ExecutionOrchestrator(
                task_store=task_store,
                event_store=event_store,
            )

            task = Task(
                task_id="test-internal",
                trace_id="trace-internal",
                input_text="测试",
                requested_workflow="one_person_default",
            )

            result = orchestrator._generate_response(task, stage_outputs)

            # internal_reasoning should have all stages
            ir = result.get("internal_reasoning", {})
            assert "ceo" in ir
            assert "info" in ir
            assert "dev" in ir
            assert "review" in ir
            assert "CEO分析内容" in ir["ceo"]
            assert "INFO信息收集" in ir["info"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
