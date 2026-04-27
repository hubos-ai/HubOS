# -*- coding: utf-8 -*-
"""Test identity reply is WeChat friendly - Step 9.1.

Tests that input "你是谁" produces a short, natural response
without markdown titles or internal analysis talk.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestIdentityReplyWeChatFriendly:
    """Tests for WeChat-friendly identity reply."""

    def test_identity_question_produces_short_reply(self):
        """Test that '你是谁' gets a short reply, not a report."""
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

            # Simulate what the AI should actually return for "你是谁"
            review_text = "你好！我是AI助手，帮你回答问题、写代码、分析数据。有什么要帮忙的吗？"
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.text = review_text
            mock_llm.generate_for_stage.return_value = mock_result
            mock_llm._clean_review_output.side_effect = lambda x: review_text
            mock_runtime.return_value = mock_llm

            event_store = EventStore()
            task_store = MagicMock()
            orchestrator = ExecutionOrchestrator(
                task_store=task_store,
                event_store=event_store,
            )

            task = Task(
                task_id="test-identity",
                trace_id="trace-identity",
                input_text="你是谁",
                requested_workflow="one_person_default",
            )

            stage_outputs = {
                "review": {"content": review_text, "confidence": 0.9},
            }

            result = orchestrator._generate_response(task, stage_outputs)

            response_text = result["response_text"]

            # Should be short (under 100 chars)
            assert len(response_text) < 100

            # Should NOT contain markdown titles
            assert "##" not in response_text
            assert "#" not in response_text

            # Should NOT contain internal analysis talk
            assert "战略分析" not in response_text
            assert "用户意图" not in response_text
            assert "关键考量" not in response_text
            assert "Processed" not in response_text

            # Should be natural Chinese
            assert "你好" in response_text or "我是" in response_text

    def test_response_contains_no_analysis_labels(self):
        """Test that response doesn't contain analysis labels."""
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
            review_text = "我是AI助手，帮你解决问题。"
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.text = review_text
            mock_llm.generate_for_stage.return_value = mock_result
            mock_llm._clean_review_output.side_effect = lambda x: review_text
            mock_runtime.return_value = mock_llm

            event_store = EventStore()
            task_store = MagicMock()
            orchestrator = ExecutionOrchestrator(
                task_store=task_store,
                event_store=event_store,
            )

            task = Task(
                task_id="test-no-labels",
                trace_id="trace-no-labels",
                input_text="测试",
                requested_workflow="one_person_default",
            )

            stage_outputs = {
                "review": {"content": review_text, "confidence": 0.9},
            }

            result = orchestrator._generate_response(task, stage_outputs)

            # Check no analysis labels
            labels = ["##", "###", "**", "用户意图", "关键考量", "战略分析"]
            for label in labels:
                assert (
                    label not in result["response_text"]
                ), f"Found {label} in response"


class TestWeChatFriendlyShortForm:
    """Tests for WeChat-friendly short form responses."""

    def test_clean_review_output_removes_all_internal_markers(self):
        """Test that _clean_review_output removes all internal markers."""
        from hubos.core.llm.runtime import LLMRuntime

        runtime = LLMRuntime()

        # Simulate a bad AI output with thinking tags and internal content
        bad_output = """<think>
用户问我是谁，我应该简短回答。
</think>

## 战略分析
用户想知道AI的身份和能力
## 用户意图
获取AI简介

你好！我是AI助手，可以帮你回答问题、写代码、分析数据。有什么需要帮忙的吗？"""

        clean = runtime._clean_review_output(bad_output)

        # Verify all internal markers removed
        assert "<think>" not in clean
        assert "</think>" not in clean
        assert "## 战略分析" not in clean
        assert "## 用户意图" not in clean
        assert "## 用户" not in clean

        # Verify actual answer is preserved
        assert "你好" in clean
        assert "AI助手" in clean


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
