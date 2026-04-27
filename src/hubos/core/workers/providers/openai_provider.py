# -*- coding: utf-8 -*-
"""OpenAI worker provider for real task execution."""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from hubos.core.workers.providers.base import (
    WorkerExecutionError,
    WorkerProvider,
    WorkerResult,
    WorkerTimeoutError,
)

logger = logging.getLogger(__name__)


class OpenAIErrorType(str, Enum):
    """OpenAI error type classification."""

    TIMEOUT = "timeout"
    AUTHENTICATION = "authentication"
    RATE_LIMIT = "rate_limit"
    INVALID_INPUT = "invalid_input"
    SERVER_ERROR = "server_error"
    UNKNOWN = "unknown"


@dataclass
class OpenAIConfig:
    """OpenAI provider configuration."""

    api_key: str
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    timeout_seconds: int = 60
    max_retries: int = 3


class OpenAIWorkerProvider(WorkerProvider):
    """
    OpenAI worker provider for real task execution.

    Per Week 5 requirements:
    - Real API integration with OpenAI
    - Proper timeout/retry handling
    - Error classification (timeout/auth/rate_limit/invalid_input/unknown)
    - Returns structured TaskResult

    Falls back to stub provider if OpenAI is not configured.
    """

    SUPPORTED_TASKS = {
        "research",
        "analysis",
        "summary",
        "general",
        "code",
        "review",
    }

    def __init__(self, config: Optional[OpenAIConfig] = None) -> None:
        """
        Initialize OpenAI worker provider.

        Args:
            config: Optional OpenAI configuration. Reads from env if not provided.
        """
        self._config = config or self._load_config_from_env()
        self._enabled = bool(self._config and self._config.api_key)

    def _load_config_from_env(self) -> OpenAIConfig:
        """Load configuration from environment variables."""
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            return OpenAIConfig(api_key="")

        return OpenAIConfig(
            api_key=api_key,
            model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
            base_url=os.environ.get(
                "OPENAI_BASE_URL",
                "https://api.openai.com/v1",
            ),
            timeout_seconds=int(
                os.environ.get("OPENAI_TIMEOUT_SECONDS", "60"),
            ),
            max_retries=int(os.environ.get("OPENAI_MAX_RETRIES", "3")),
        )

    @property
    def name(self) -> str:
        """Return the provider name."""
        return "openai"

    @property
    def is_enabled(self) -> bool:
        """Check if provider is enabled."""
        return self._enabled

    async def execute(
        self,
        unit_id: UUID,
        input_data: dict[str, Any],
        timeout_seconds: int,
    ) -> WorkerResult:
        """
        Execute a task using OpenAI.

        Args:
            unit_id: The task unit ID.
            input_data: Input data for the task.
            timeout_seconds: Timeout in seconds.

        Returns:
            WorkerResult with OpenAI execution outcome.

        Raises:
            WorkerTimeoutError: If execution times out.
            WorkerExecutionError: If execution fails.
        """
        if not self._enabled:
            raise WorkerExecutionError(
                f"OpenAI provider not enabled (no API key)",
            )

        start_time = time.time()

        # Extract prompt from input_data
        prompt = (
            input_data.get("prompt")
            or input_data.get("content")
            or input_data.get("message", "")
        )

        if not prompt:
            raise WorkerExecutionError(
                f"OpenAI requires 'prompt' in input_data",
            )

        logger.info(
            "OpenAI worker executing",
            extra={
                "unit_id": str(unit_id),
                "provider": self.name,
                "model": self._config.model,
                "timeout_seconds": timeout_seconds,
                "prompt_length": len(prompt),
            },
        )

        try:
            result = await asyncio.wait_for(
                self._call_openai(prompt, input_data),
                timeout=min(timeout_seconds, self._config.timeout_seconds),
            )

            elapsed_ms = int((time.time() - start_time) * 1000)

            return WorkerResult(
                provider=self.name,
                unit_id=unit_id,
                success=True,
                data=result,
                confidence=0.9,
                artifacts=[],
                error=None,
                execution_time_ms=elapsed_ms,
                timestamp=datetime.now(timezone.utc),
            )

        except asyncio.TimeoutError:
            elapsed_ms = int((time.time() - start_time) * 1000)
            logger.warning(
                "OpenAI request timed out",
                extra={
                    "unit_id": str(unit_id),
                    "elapsed_ms": elapsed_ms,
                    "timeout_seconds": timeout_seconds,
                },
            )
            raise WorkerTimeoutError(
                f"OpenAI request timed out after {elapsed_ms}ms",
            )

        except Exception as e:
            elapsed_ms = int((time.time() - start_time) * 1000)
            error_type = self._classify_error(e)

            logger.error(
                "OpenAI execution failed",
                extra={
                    "unit_id": str(unit_id),
                    "error": str(e),
                    "error_type": error_type,
                    "elapsed_ms": elapsed_ms,
                },
            )

            raise WorkerExecutionError(
                f"OpenAI execution failed ({error_type}): {e}",
            ) from e

    async def _call_openai(
        self,
        prompt: str,
        input_data: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Make the actual OpenAI API call.

        Args:
            prompt: The prompt to send.
            input_data: Additional input data.

        Returns:
            Response data dictionary.
        """
        import aiohttp

        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }

        system_prompt = input_data.get("system_prompt") or input_data.get(
            "system",
            "",
        )

        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [],
        }

        if system_prompt:
            payload["messages"].append(
                {
                    "role": "system",
                    "content": system_prompt,
                },
            )

        payload["messages"].append(
            {
                "role": "user",
                "content": prompt,
            },
        )

        # Add temperature if specified
        if "temperature" in input_data:
            payload["temperature"] = float(input_data["temperature"])

        # Add max_tokens if specified
        if "max_tokens" in input_data:
            payload["max_tokens"] = int(input_data["max_tokens"])

        url = f"{self._config.base_url}/chat/completions"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(
                    total=self._config.timeout_seconds,
                ),
            ) as response:
                if response.status == 401:
                    raise Exception("OpenAI authentication failed (401)")
                elif response.status == 429:
                    raise Exception("OpenAI rate limit exceeded (429)")
                elif response.status >= 500:
                    raise Exception(f"OpenAI server error ({response.status})")
                elif response.status != 200:
                    text = await response.text()
                    raise Exception(
                        f"OpenAI request failed ({response.status}): {text}",
                    )

                result = await response.json()

                if "choices" not in result or not result["choices"]:
                    raise Exception("OpenAI response missing choices")

                content = result["choices"][0]["message"]["content"]

                return {
                    "content": content,
                    "model": result.get("model", self._config.model),
                    "usage": result.get("usage", {}),
                    "finish_reason": result["choices"][0].get("finish_reason"),
                }

    def _classify_error(self, error: Exception) -> str:
        """
        Classify an error into a known type.

        Args:
            error: The exception to classify.

        Returns:
            Error type string.
        """
        error_str = str(error).lower()

        if "timeout" in error_str or "timed out" in error_str:
            return OpenAIErrorType.TIMEOUT.value
        elif (
            "401" in error_str
            or "authentication" in error_str
            or "api key" in error_str
        ):
            return OpenAIErrorType.AUTHENTICATION.value
        elif "429" in error_str or "rate limit" in error_str:
            return OpenAIErrorType.RATE_LIMIT.value
        elif (
            "400" in error_str
            or "invalid" in error_str
            or "malformed" in error_str
        ):
            return OpenAIErrorType.INVALID_INPUT.value
        elif (
            "500" in error_str
            or "502" in error_str
            or "503" in error_str
            or "server error" in error_str
        ):
            return OpenAIErrorType.SERVER_ERROR.value
        else:
            return OpenAIErrorType.UNKNOWN.value

    def supports(self, task_type: str) -> bool:
        """
        Check if this provider supports a given task type.

        Args:
            task_type: The task type to check.

        Returns:
            True if supported, False otherwise.
        """
        return self._enabled and task_type.lower() in self.SUPPORTED_TASKS
