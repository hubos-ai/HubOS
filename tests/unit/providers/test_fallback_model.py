# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from agentscope.model import ChatModelBase
from agentscope.model._model_response import ChatResponse

from hubos.providers.fallback_model import FallbackChatModel


class DummyChatModel(ChatModelBase):
    def __init__(
        self,
        model_name: str = "dummy-model",
        *,
        stream: bool = True,
        marker: str = "primary",
        should_fail: bool = False,
    ) -> None:
        super().__init__(model_name=model_name, stream=stream)
        self.marker = marker
        self.should_fail = should_fail

    async def __call__(self, *args, **kwargs):
        if self.should_fail:
            raise RuntimeError(f"{self.marker} failed")
        return ChatResponse(
            content=f"{self.marker} ok",
        )


class DummyStreamModel(DummyChatModel):
    def __init__(
        self,
        *args,
        fail_before_first: bool = False,
        fail_after_first: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(*args, stream=True, **kwargs)
        self.fail_before_first = fail_before_first
        self.fail_after_first = fail_after_first
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.calls += 1

        async def _stream():
            if self.fail_before_first:
                raise RuntimeError(f"{self.marker} stream failed")
            yield ChatResponse(content=f"{self.marker} chunk")
            if self.fail_after_first:
                raise RuntimeError(f"{self.marker} stream failed")

        return _stream()


def test_fallback_model_exposes_primary_stream_and_model_name() -> None:
    primary = DummyChatModel(
        model_name="glm-primary",
        stream=True,
        marker="primary",
    )
    fallback = DummyChatModel(
        model_name="deepseek-fallback",
        stream=False,
        marker="fallback",
    )

    model = FallbackChatModel(primary=primary, fallback=fallback)

    assert model.stream is True
    assert model.model_name == "glm-primary"


def test_fallback_model_delegates_unknown_attributes_to_primary() -> None:
    primary = DummyChatModel(marker="primary")
    fallback = DummyChatModel(marker="fallback")

    model = FallbackChatModel(primary=primary, fallback=fallback)

    assert model.marker == "primary"


@pytest.mark.asyncio
async def test_fallback_model_uses_fallback_when_primary_fails() -> None:
    primary = DummyChatModel(marker="primary", should_fail=True)
    fallback = DummyChatModel(marker="fallback")

    model = FallbackChatModel(primary=primary, fallback=fallback)
    response = await model(messages=[])

    assert response.content == "fallback ok"


@pytest.mark.asyncio
async def test_fallback_model_handles_failure_while_reading_stream() -> None:
    primary = DummyStreamModel(
        marker="primary",
        fail_before_first=True,
    )
    fallback = DummyStreamModel(marker="fallback")
    model = FallbackChatModel(primary=primary, fallback=fallback)

    response = await model(messages=[])
    chunks = [chunk async for chunk in response]

    assert [chunk.content for chunk in chunks] == ["fallback chunk"]
    assert fallback.calls == 1


@pytest.mark.asyncio
async def test_fallback_model_does_not_switch_after_partial_stream() -> None:
    primary = DummyStreamModel(
        marker="primary",
        fail_after_first=True,
    )
    fallback = DummyStreamModel(marker="fallback")
    model = FallbackChatModel(primary=primary, fallback=fallback)

    response = await model(messages=[])
    with pytest.raises(RuntimeError, match="primary stream failed"):
        _ = [chunk async for chunk in response]

    assert fallback.calls == 0
