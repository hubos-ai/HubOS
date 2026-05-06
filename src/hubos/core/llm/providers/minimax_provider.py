# -*- coding: utf-8 -*-
"""LLM provider — delegates to agentscope model clients.

Reads HubOS provider config (api_key, base_url, model class) and creates
the corresponding agentscope model client. No manual HTTP handling.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# agentscope model class registry
_MODEL_CLASSES: dict[str, type] = {}


def _get_model_classes() -> dict[str, type]:
    """Lazy-load agentscope model classes."""
    if _MODEL_CLASSES:
        return _MODEL_CLASSES
    try:
        from agentscope.model import (
            AnthropicChatModel,
            GeminiChatModel,
            OpenAIChatModel,
        )

        _MODEL_CLASSES.update(
            {
                "OpenAIChatModel": OpenAIChatModel,
                "AnthropicChatModel": AnthropicChatModel,
                "GeminiChatModel": GeminiChatModel,
            },
        )
    except ImportError:
        logger.warning("agentscope not installed, LLM provider unavailable")
    return _MODEL_CLASSES


@dataclass
class MiniMaxResponse:
    """Response from LLM API."""

    text: str
    finish_reason: str
    usage: Optional[dict[str, int]] = None
    model: Optional[str] = None
    raw_response: Optional[dict] = None


class MiniMaxProvider:
    """Universal LLM provider backed by agentscope model clients.

    Reads HubOS provider config to determine which agentscope model class
    to use (OpenAI, Anthropic, Gemini, etc.) and calls it directly.
    """

    DEFAULT_BASE_URL = "https://api.minimax.chat/v1"
    DEFAULT_MODEL = "MiniMax-M2.7-highspeed"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout_seconds: int = 60,
        *,
        model_class_name: Optional[str] = None,
    ) -> None:
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
        self._model_class_name = model_class_name or "OpenAIChatModel"
        self._client: Any = None

    def _get_client(self) -> Any:
        """Create the agentscope model client lazily."""
        if self._client is not None:
            return self._client

        classes = _get_model_classes()
        cls = classes.get(self._model_class_name)
        if cls is None:
            raise RuntimeError(
                f"Unknown model class: {self._model_class_name}, "
                f"available: {list(classes.keys())}",
            )

        self._client = cls(
            model_name=self._model,
            api_key=self._api_key or None,
            stream=False,
            client_kwargs={"base_url": self._base_url},
        )
        return self._client

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        role: Optional[str] = None,
    ) -> MiniMaxResponse:
        """Generate text using agentscope model client."""
        if not self._api_key:
            raise RuntimeError("LLM API key not configured")

        messages = []
        system_content = system_prompt or ""
        if role:
            system_content = (
                f"{system_content}\n\n[Role: {role.upper()}]".strip()
            )
        if system_content:
            messages.append({"role": "system", "content": system_content})
        messages.append({"role": "user", "content": prompt})

        client = self._get_client()

        # agentscope model __call__ is async
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context — use nest_asyncio or run in thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                response = pool.submit(
                    asyncio.run,
                    client(messages),
                ).result()
        else:
            response = asyncio.run(client(messages))

        # Parse ChatResponse (agentscope dict-like object)
        text = ""
        # ChatResponse.content is a list of content blocks
        content = (
            response.get("content")
            if isinstance(response, dict)
            else getattr(response, "content", None)
        )
        if content and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "")

        usage = None
        u = (
            response.get("usage")
            if isinstance(response, dict)
            else getattr(response, "usage", None)
        )
        if u:
            usage = {
                "input_tokens": getattr(u, "input_tokens", 0)
                if not isinstance(u, dict)
                else u.get("input_tokens", 0),
                "output_tokens": getattr(u, "output_tokens", 0)
                if not isinstance(u, dict)
                else u.get("output_tokens", 0),
            }

        return MiniMaxResponse(
            text=text,
            finish_reason="stop",
            usage=usage,
            model=self._model,
        )

    def health_check(self) -> tuple[bool, str]:
        if not self._api_key:
            return False, "API key not configured"
        try:
            self.generate(prompt="Hi", max_tokens=1, temperature=0.0)
            return True, "OK"
        except Exception as e:
            return False, str(e)[:100]
