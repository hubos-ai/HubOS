# -*- coding: utf-8 -*-
"""Tests for Feishu channel routing helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hubos.app.channels.feishu.channel import FeishuChannel
from hubos.app.channels.feishu.constants import FEISHU_RICH_TEXT_SAFE_BYTES


async def _dummy_process(_request):
    if False:  # pragma: no cover
        yield None


def test_group_session_id_includes_sender_for_workspace_isolation() -> None:
    """Different users in the same group must not share one session key."""
    channel = FeishuChannel(
        process=_dummy_process,
        enabled=False,
        app_id="cli_test_abcd",
        app_secret="",
        bot_prefix="",
    )
    meta = {
        "feishu_chat_type": "group",
        "feishu_chat_id": "oc_group_shared_123456",
    }

    sid_a = channel.resolve_session_id("ou_user_alpha_1111", meta)
    sid_b = channel.resolve_session_id("ou_user_beta_2222", meta)

    assert sid_a != sid_b
    assert sid_a.startswith("abcd_")
    assert sid_b.startswith("abcd_")


def test_p2p_session_id_still_uses_sender_only() -> None:
    channel = FeishuChannel(
        process=_dummy_process,
        enabled=False,
        app_id="cli_test_abcd",
        app_secret="",
        bot_prefix="",
    )

    sid = channel.resolve_session_id(
        "ou_user_alpha_1111",
        {"feishu_chat_type": "p2p", "feishu_chat_id": "oc_ignored"},
    )

    assert sid == channel.resolve_session_id("ou_user_alpha_1111", {})
    assert "ignored" not in sid


@pytest.mark.asyncio
async def test_long_text_reply_is_sent_as_file(tmp_path) -> None:
    channel = FeishuChannel(
        process=_dummy_process,
        enabled=False,
        app_id="cli_test_abcd",
        app_secret="",
        bot_prefix="",
    )
    channel._media_dir = tmp_path  # noqa: SLF001
    channel._send_message = AsyncMock(  # noqa: SLF001
        side_effect=["notice-message-id", "file-message-id"],
    )
    channel._upload_file = AsyncMock(return_value="file-key-123")  # noqa: SLF001

    body = "长回复内容\n" + ("这是完整正文。" * FEISHU_RICH_TEXT_SAFE_BYTES)
    msg_id = await channel._send_text("open_id", "ou_test", body)  # noqa: SLF001

    assert msg_id == "file-message-id"
    assert channel._upload_file.await_count == 1  # noqa: SLF001
    assert channel._send_message.await_count == 2  # noqa: SLF001
    notice_call = channel._send_message.await_args_list[0]  # noqa: SLF001
    file_call = channel._send_message.await_args_list[1]  # noqa: SLF001
    assert notice_call.args[2] == "post"
    assert "回复内容较长" in notice_call.args[3]
    assert body not in notice_call.args[3]
    assert file_call.args[2] == "file"
    assert "file-key-123" in file_call.args[3]

    uploaded_path = channel._upload_file.await_args.args[0]  # noqa: SLF001
    assert uploaded_path.endswith(".md")
    assert "长回复内容" in tmp_path.joinpath(uploaded_path.split("/")[-1]).read_text()
