# -*- coding: utf-8 -*-
"""FallbackChatModel — primary → fallback model switching.

Wraps two ``ChatModelBase`` instances.  When the primary model raises
a non-retryable (or exhaustively-retried) exception, the fallback is
tried transparently.  This is the last line of defence after
``RetryChatModel`` has exhausted all retries.

Typical use-case::

    primary = RetryChatModel(glm_model, ...)
    fallback = RetryChatModel(deepseek_model, ...)
    model = FallbackChatModel(primary, fallback)
"""

import logging
from collections.abc import AsyncIterator
from typing import Any, AsyncGenerator, Union

from agentscope.model import ChatModelBase, ChatResponse

logger = logging.getLogger(__name__)


class FallbackChatModel(ChatModelBase):
    """Proxies LLM calls, falling back to a secondary model on failure.

    Both ``primary`` and ``fallback`` should already include retry/rate-limit
    wrappers so that this class only sees failures after all retries are
    exhausted (e.g. quota exhaustion, API key invalid).

    When the primary call fails with ANY exception, the fallback model
    is called with the exact same arguments.  If the fallback also fails,
    the **primary** exception is re-raised (since the primary represents
    the user's configured model).
    """

    def __init__(
        self,
        primary: ChatModelBase,
        fallback: ChatModelBase,
        primary_label: str = "primary",
        fallback_label: str = "fallback",
    ):
        super().__init__(
            model_name=getattr(primary, "model_name", "fallback-primary"),
            stream=bool(getattr(primary, "stream", True)),
        )
        self._primary = primary
        self._fallback = fallback
        self._primary_label = primary_label
        self._fallback_label = fallback_label

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes to the primary model.

        AgentScope and HubOS wrappers sometimes access model capabilities
        directly on the outermost model object. Keep fallback wrapper
        transparent unless it intentionally overrides the attribute.
        """
        return getattr(self._primary, name)

    async def __call__(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
        try:
            result = await self._primary(*args, **kwargs)
        except Exception as e:
            return await self._call_fallback(e, args, kwargs)

        if isinstance(result, AsyncIterator):
            return self._stream_with_fallback(result, args, kwargs)
        return result

    async def _call_fallback(
        self,
        primary_exc: Exception,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        """Call the fallback and preserve the primary error if both fail."""
        logger.warning(
            "%s model failed (%s: %s), falling back to %s",
            self._primary_label,
            type(primary_exc).__name__,
            primary_exc,
            self._fallback_label,
        )

        try:
            result = await self._fallback(*args, **kwargs)
            logger.info(
                "Fallback %s model succeeded",
                self._fallback_label,
            )
            return result
        except Exception as fallback_exc:
            logger.error(
                "Fallback %s also failed (%s: %s)",
                self._fallback_label,
                type(fallback_exc).__name__,
                fallback_exc,
            )
            # Re-raise the PRIMARY exception — that's the user's configured
            # model and the error that matters most.
            raise primary_exc from fallback_exc  # pylint: disable=raise-missing-from

    async def _stream_with_fallback(
        self,
        primary_stream: AsyncIterator[ChatResponse],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> AsyncGenerator[ChatResponse, None]:
        """Fallback when a primary stream fails before emitting any output."""
        emitted = False
        primary_failure: Exception | None = None
        try:
            async for chunk in primary_stream:
                emitted = True
                yield chunk
            return
        except Exception as primary_exc:
            # Switching models after partial output would duplicate or corrupt
            # the response, so only a pre-first-token failure is recoverable.
            if emitted:
                raise
            primary_failure = primary_exc

        fallback_result = await self._call_fallback(
            primary_failure,
            args,
            kwargs,
        )
        if isinstance(fallback_result, AsyncIterator):
            async for chunk in fallback_result:
                yield chunk
        else:
            yield fallback_result
