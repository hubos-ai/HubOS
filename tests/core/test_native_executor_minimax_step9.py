"""Test NativeExecutor with MiniMax provider - Step 9.

Tests that NativeExecutor properly calls MiniMax provider
and handles timeout/error cases.
"""

import pytest
from unittest.mock import MagicMock, patch


class TestNativeExecutorMiniMax:
    """Tests for NativeExecutor with MiniMax."""

    def test_executor_calls_minimax_when_enabled(self):
        """Test that executor calls MiniMax when real model execution is enabled."""
        from hubos.core.execution.executors.native_executor import NativeExecutor

        with patch('hubos.core.infra.feature_flags.get_feature_flags') as mock_ff, \
             patch('hubos.core.llm.runtime.get_llm_runtime') as mock_runtime:
            mock_flags = MagicMock()
            mock_flags.enable_real_model_execution = True
            mock_ff.return_value = mock_flags

            mock_llm = MagicMock()
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.text = "This is the MiniMax response"
            mock_result.error = None
            mock_llm.generate_for_stage.return_value = mock_result
            mock_runtime.return_value = mock_llm

            executor = NativeExecutor()
            result = executor.execute(
                node_id="node-1",
                role="ceo",
                input_text="Hello AI",
                timeout_ms=30000,
                attempt=1,
                metadata={"input_template": "Analyze: {input}"},
            )

            assert result.success is True
            assert result.output["response_text"] == "This is the MiniMax response"
            assert "Processed" not in result.output["response_text"]

    def test_executor_handles_error(self):
        """Test that executor properly handles LLM errors."""
        from hubos.core.execution.executors.native_executor import NativeExecutor

        with patch('hubos.core.infra.feature_flags.get_feature_flags') as mock_ff, \
             patch('hubos.core.llm.runtime.get_llm_runtime') as mock_runtime:
            mock_flags = MagicMock()
            mock_flags.enable_real_model_execution = True
            mock_ff.return_value = mock_flags

            mock_llm = MagicMock()
            mock_result = MagicMock()
            mock_result.success = False
            mock_result.error = "API error"
            mock_llm.generate_for_stage.return_value = mock_result
            mock_runtime.return_value = mock_llm

            executor = NativeExecutor()
            result = executor.execute(
                node_id="node-2",
                role="dev",
                input_text="Complex task",
                timeout_ms=5000,
                attempt=1,
                metadata=None,
            )

            assert result.success is False
            assert "API error" in result.error

    def test_executor_uses_mock_when_disabled(self):
        """Test that executor uses mock when real model is disabled."""
        from hubos.core.execution.executors.native_executor import NativeExecutor

        with patch('hubos.core.infra.feature_flags.get_feature_flags') as mock_ff:
            mock_flags = MagicMock()
            mock_flags.enable_real_model_execution = False
            mock_ff.return_value = mock_flags

            executor = NativeExecutor()
            result = executor.execute(
                node_id="node-3",
                role="review",
                input_text="Test content",
                timeout_ms=10000,
                attempt=1,
                metadata=None,
            )

            assert result.success is True
            assert "Processed" in result.output["response_text"]
            assert result.metadata.get("mock") is True


class TestMiniMaxProvider:
    """Tests for MiniMax provider directly."""

    def test_provider_requires_api_key(self):
        """Test that provider raises error when API key is not set."""
        with patch.dict('os.environ', {}, clear=True):
            from hubos.core.llm.providers.minimax_provider import MiniMaxProvider

            provider = MiniMaxProvider(api_key=None)
            assert provider.is_configured is False

    def test_provider_is_configured_with_key(self):
        """Test that provider is configured when API key is provided."""
        with patch.dict('os.environ', {}, clear=True):
            from hubos.core.llm.providers.minimax_provider import MiniMaxProvider

            provider = MiniMaxProvider(api_key="test-key-123")
            assert provider.is_configured is True

    def test_generate_without_key_raises(self):
        """Test that generate() raises error when API key is not configured."""
        with patch.dict('os.environ', {}, clear=True):
            from hubos.core.llm.providers.minimax_provider import MiniMaxProvider

            provider = MiniMaxProvider(api_key=None)

            with pytest.raises(RuntimeError, match="API key not configured"):
                provider.generate(prompt="Hello")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
