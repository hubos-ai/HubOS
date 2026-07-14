# -*- coding: utf-8 -*-
"""Unit tests for the Weixin iLink HTTP client."""
from __future__ import annotations

import pytest

from hubos.app.channels.weixin.client import ILinkClient


@pytest.mark.asyncio
async def test_start_disables_env_proxy_inheritance() -> None:
    client = ILinkClient(bot_token="token")

    await client.start()
    try:
        assert client._client is not None
        assert client._client._trust_env is False
    finally:
        await client.stop()
