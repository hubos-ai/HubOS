# -*- coding: utf-8 -*-
"""Unit tests for Weixin channel typing cleanup."""
from __future__ import annotations

import pytest

from hubos.app.channels.weixin.channel import WeixinChannel


async def _dummy_process(_request):
    if False:  # pragma: no cover
        yield None


def _make_channel() -> WeixinChannel:
    return WeixinChannel(
        process=_dummy_process,
        enabled=True,
        bot_token="test-token",
    )


@pytest.mark.asyncio
async def test_on_process_completed_stops_typing_indicator() -> None:
    channel = _make_channel()
    stopped = {"value": 0}

    def _stop():
        stopped["value"] += 1

    channel._typing_stop_funcs["user123"] = _stop

    await channel._on_process_completed(None, "user123", {})

    assert stopped["value"] == 1
    assert "user123" not in channel._typing_stop_funcs


@pytest.mark.asyncio
async def test_on_consume_cancelled_stops_typing_indicator() -> None:
    channel = _make_channel()
    stopped = {"value": 0}

    def _stop():
        stopped["value"] += 1

    channel._typing_stop_funcs["user123"] = _stop

    await channel._on_consume_cancelled(None, "user123")

    assert stopped["value"] == 1
    assert "user123" not in channel._typing_stop_funcs
