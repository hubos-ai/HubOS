# -*- coding: utf-8 -*-
"""MiniMax AI provider implementation.

Handles API calls to MiniMax chat completion endpoint.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class MiniMaxResponse:
    """Response from MiniMax API."""

    text: str
    finish_reason: str
    usage: Optional[dict[str, int]] = None
    model: Optional[str] = None
    raw_response: Optional[dict] = None


class MiniMaxProvider:
    """MiniMax chat completion provider."""

    DEFAULT_BASE_URL = "https://api.minimax.chat/v1"
    DEFAULT_MODEL = "MiniMax-M2.7-highspeed"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: int = 60,
    ) -> None:
        """Initialize MiniMax provider.

        Args:
            api_key: MiniMax API key (reads from MINIMAX_API_KEY env if not provided)
            base_url: API base URL
            model: Model name to use
            timeout_seconds: Request timeout
        """
        self._api_key = api_key or os.environ.get("MINIMAX_API_KEY", "")
        self._base_url = (
            base_url
            or os.environ.get("MINIMAX_BASE_URL", self.DEFAULT_BASE_URL)
        ).rstrip("/")
        self._model = model or os.environ.get(
            "MINIMAX_MODEL",
            self.DEFAULT_MODEL,
        )
        self._timeout = timeout_seconds

    @property
    def is_configured(self) -> bool:
        """Check if provider is properly configured."""
        return bool(self._api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        role: Optional[str] = None,
    ) -> MiniMaxResponse:
        """Generate text from MiniMax model.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            role: Optional role context for the prompt

        Returns:
            MiniMaxResponse with generated text

        Raises:
            RuntimeError: If API call fails
        """
        if not self._api_key:
            raise RuntimeError(
                "MiniMax API key not configured (set MINIMAX_API_KEY env)",
            )

        # Build messages
        messages = []
        # Combine system_prompt and role into single system message if provided
        system_content = system_prompt or ""
        if role:
            system_content = (
                f"{system_content}\n\n[Role: {role.upper()}]".strip()
            )
        if system_content:
            messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        url = f"{self._base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }

        try:
            request = urllib.request.Request(
                url=url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )

            start_time = time.time()
            with urllib.request.urlopen(
                request,
                timeout=float(self._timeout),
            ) as response:
                elapsed_ms = (time.time() - start_time) * 1000
                data = json.loads(response.read().decode("utf-8"))

            logger.debug(f"MiniMax API call completed in {elapsed_ms:.0f}ms")

            # Extract response text
            choices = data.get("choices", [])
            if not choices:
                return MiniMaxResponse(
                    text="",
                    finish_reason="empty",
                    usage=data.get("usage"),
                    model=data.get("model"),
                    raw_response=data,
                )

            first_choice = choices[0]
            text = first_choice.get("message", {}).get("content", "")
            finish_reason = first_choice.get("finish_reason", "stop")

            return MiniMaxResponse(
                text=text,
                finish_reason=finish_reason,
                usage=data.get("usage"),
                model=data.get("model"),
                raw_response=data,
            )

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            logger.error(f"MiniMax API HTTP error {e.code}: {error_body}")
            raise RuntimeError(
                f"MiniMax API error {e.code}: {error_body[:500]}",
            )
        except urllib.error.URLError as e:
            logger.error(f"MiniMax API connection error: {e.reason}")
            raise RuntimeError(f"MiniMax connection error: {e.reason}")
        except json.JSONDecodeError as e:
            logger.error(f"MiniMax API invalid JSON response: {e}")
            raise RuntimeError(f"MiniMax API invalid response format")
        except Exception as e:
            logger.error(f"MiniMax API unexpected error: {e}")
            raise RuntimeError(f"MiniMax API error: {str(e)}")

    def health_check(self) -> tuple[bool, str]:
        """Check if provider is reachable and configured.

        Returns:
            Tuple of (is_healthy, message)
        """
        if not self._api_key:
            return False, "API key not configured"

        # Try a minimal request to verify credentials
        try:
            self.generate(
                prompt="Hi",
                max_tokens=1,
                temperature=0.0,
            )
            return True, "OK"
        except Exception as e:
            return False, str(e)[:100]
