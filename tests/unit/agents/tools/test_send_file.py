# -*- coding: utf-8 -*-
"""Tests for send_file_to_user direct channel delivery."""

from __future__ import annotations

from pathlib import Path

import pytest

from hubos.agents.tools.send_file import send_file_to_user
from hubos.app.channels.delivery_context import (
    DeliveryContext,
    reset_current_delivery_context,
    set_current_delivery_context,
)


def _block_text(block) -> str:
    if isinstance(block, dict):
        return block.get("text", "")
    return getattr(block, "text", "")


@pytest.mark.asyncio
async def test_send_file_to_user_sends_directly_when_delivery_context_present(
    tmp_path: Path,
) -> None:
    """Tool should deliver media immediately via the active channel."""
    sent: list[tuple[str, list, dict]] = []
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    async def _send_parts(
        to_handle: str,
        parts: list,
        meta: dict | None,
    ) -> None:
        sent.append((to_handle, parts, dict(meta or {})))

    token = set_current_delivery_context(
        DeliveryContext(
            channel="feishu",
            to_handle="feishu:open_id:ou_test",
            meta={"user_id": "ou_test"},
            send_parts=_send_parts,
        ),
    )
    try:
        response = await send_file_to_user(str(image_path))
    finally:
        reset_current_delivery_context(token)

    assert len(sent) == 1
    to_handle, parts, meta = sent[0]
    assert to_handle == "feishu:open_id:ou_test"
    assert meta["user_id"] == "ou_test"
    assert len(parts) == 1
    assert getattr(parts[0], "type", None) == "image"
    assert getattr(parts[0], "image_url", "").startswith("file://")
    assert len(response.content) == 1
    assert _block_text(response.content[0]) == "File sent successfully."


@pytest.mark.asyncio
async def test_send_file_to_user_falls_back_without_delivery_context(
    tmp_path: Path,
) -> None:
    """Without a live channel context, preserve ToolResponse media fallback."""
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

    response = await send_file_to_user(str(image_path))

    assert len(response.content) == 2
    assert response.content[0]["type"] == "image"
    assert response.content[0]["source"]["url"].startswith("file://")
    assert _block_text(response.content[1]) == "File sent successfully."
