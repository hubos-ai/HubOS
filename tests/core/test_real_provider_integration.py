"""Tests for real provider integration (Week 5)."""

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from hubos.core.workers.providers.base import WorkerExecutionError, WorkerTimeoutError
from hubos.core.workers.providers.openai_provider import (
    OpenAIConfig,
    OpenAIErrorType,
    OpenAIWorkerProvider,
)


class TestOpenAIProvider:
    """Tests for OpenAI worker provider."""

    def test_provider_disabled_without_api_key(self) -> None:
        """Test that provider is disabled when no API key is configured."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key=""))

        assert provider.is_enabled is False
        assert provider.supports("general") is False

    def test_provider_enabled_with_api_key(self) -> None:
        """Test that provider is enabled when API key is configured."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test-key"))

        assert provider.is_enabled is True
        assert provider.supports("general") is True
        assert provider.supports("analysis") is True
        assert provider.supports("summary") is True

    def test_unsupported_task_type(self) -> None:
        """Test that unsupported task types return False."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test-key"))

        assert provider.supports("unknown_type") is False
        assert provider.supports("") is False

    def test_error_classification_timeout(self) -> None:
        """Test timeout error classification."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        error = Exception("Request timed out after 30s")
        error_type = provider._classify_error(error)

        assert error_type == OpenAIErrorType.TIMEOUT.value

    def test_error_classification_auth(self) -> None:
        """Test authentication error classification."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        error = Exception("OpenAI authentication failed (401)")
        error_type = provider._classify_error(error)

        assert error_type == OpenAIErrorType.AUTHENTICATION.value

    def test_error_classification_rate_limit(self) -> None:
        """Test rate limit error classification."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        error = Exception("OpenAI rate limit exceeded (429)")
        error_type = provider._classify_error(error)

        assert error_type == OpenAIErrorType.RATE_LIMIT.value

    def test_error_classification_invalid_input(self) -> None:
        """Test invalid input error classification."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        error = Exception("OpenAI request failed (400): Invalid request")
        error_type = provider._classify_error(error)

        assert error_type == OpenAIErrorType.INVALID_INPUT.value

    def test_error_classification_server_error(self) -> None:
        """Test server error classification."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        error = Exception("OpenAI server error (503)")
        error_type = provider._classify_error(error)

        assert error_type == OpenAIErrorType.SERVER_ERROR.value

    def test_error_classification_unknown(self) -> None:
        """Test unknown error classification."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        error = Exception("Something went wrong")
        error_type = provider._classify_error(error)

        assert error_type == OpenAIErrorType.UNKNOWN.value

    def test_execute_requires_prompt(self) -> None:
        """Test that execute fails without prompt in input_data."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test-key"))

        with pytest.raises(WorkerExecutionError) as exc_info:
            asyncio.run(provider.execute(
                unit_id=uuid4(),
                input_data={},
                timeout_seconds=30,
            ))

        assert "prompt" in str(exc_info.value)

    def test_execute_requires_enabled_provider(self) -> None:
        """Test that execute fails when provider is disabled."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key=""))

        with pytest.raises(WorkerExecutionError) as exc_info:
            asyncio.run(provider.execute(
                unit_id=uuid4(),
                input_data={"prompt": "Hello"},
                timeout_seconds=30,
            ))

        assert "not enabled" in str(exc_info.value)


class TestProviderConfiguration:
    """Tests for provider configuration from environment."""

    def test_config_from_defaults(self) -> None:
        """Test configuration with default values."""
        config = OpenAIConfig(api_key="test-key")

        assert config.model == "gpt-4o"
        assert config.base_url == "https://api.openai.com/v1"
        assert config.timeout_seconds == 60
        assert config.max_retries == 3

    def test_config_custom_values(self) -> None:
        """Test configuration with custom values."""
        config = OpenAIConfig(
            api_key="custom-key",
            model="gpt-3.5-turbo",
            base_url="https://api.custom.com/v1",
            timeout_seconds=120,
            max_retries=5,
        )

        assert config.api_key == "custom-key"
        assert config.model == "gpt-3.5-turbo"
        assert config.timeout_seconds == 120
        assert config.max_retries == 5

    def test_provider_name(self) -> None:
        """Test provider name property."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))
        assert provider.name == "openai"


class TestProviderTaskTypes:
    """Tests for provider-supported task types."""

    def test_all_supported_types(self) -> None:
        """Test all supported task types."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        supported = {"research", "analysis", "summary", "general", "code", "review"}

        for task_type in supported:
            assert provider.supports(task_type) is True

    def test_case_insensitive_support(self) -> None:
        """Test that task type checking is case insensitive."""
        provider = OpenAIWorkerProvider(OpenAIConfig(api_key="test"))

        assert provider.supports("RESEARCH") is True
        assert provider.supports("Research") is True
        assert provider.supports("ANALYSIS") is True
