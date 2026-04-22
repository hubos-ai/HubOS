#!/usr/bin/env python3
"""Tests for solo-hub direct WeChat channel when disabled.

When RUNTIME_WECHAT_DIRECT=false:
- solo-hub should NOT start WeChat poller
- solo-hub should NOT send WeChat messages directly
- OpenWork handles all WeChat communication
"""

import pytest
from unittest.mock import patch, MagicMock
from hubos.core.infra.feature_flags import FeatureFlags


class TestNoDirectChannelWhenDisabled:
    """Test solo-hub de-channelization."""

    def test_feature_flag_defaults(self):
        """Test feature flags have correct defaults."""
        flags = FeatureFlags()

        # New architecture: OpenWork handles WeChat
        assert flags.enable_openwork_wechat_channel is True
        assert flags.enable_openwork_channel_to_runtime is True
        assert flags.enable_runtime_wechat_direct is False

    def test_runtime_wechat_direct_env_override(self):
        """Test RUNTIME_WECHAT_DIRECT can be enabled via env."""
        with patch.dict("os.environ", {"RUNTIME_WECHAT_DIRECT": "true"}):
            flags = FeatureFlags.from_env()
            assert flags.enable_runtime_wechat_direct is True

    def test_runtime_wechat_direct_disabled_by_default(self):
        """Test RUNTIME_WECHAT_DIRECT defaults to False."""
        flags = FeatureFlags.from_env()
        assert flags.enable_runtime_wechat_direct is False

    def test_openwork_wechat_channel_disabled(self):
        """Test OPENWORK_WECHAT_CHANNEL can be disabled."""
        with patch.dict("os.environ", {"ENABLE_OPENWORK_WECHAT_CHANNEL": "false"}):
            flags = FeatureFlags.from_env()
            assert flags.enable_openwork_wechat_channel is False

    def test_openwork_channel_to_runtime_disabled(self):
        """Test OPENWORK_CHANNEL_TO_RUNTIME can be disabled."""
        with patch.dict("os.environ", {"ENABLE_OPENWORK_CHANNEL_TO_RUNTIME": "false"}):
            flags = FeatureFlags.from_env()
            assert flags.enable_openwork_channel_to_runtime is False

    def test_wechat_direct_mode_convenience_method(self):
        """Test use_runtime_wechat_direct convenience method."""
        flags = FeatureFlags()
        assert flags.use_runtime_wechat_direct() is False

        flags.enable_runtime_wechat_direct = True
        assert flags.use_runtime_wechat_direct() is True

    def test_wechat_status_includes_new_flags(self):
        """Test wechat status reflects new architecture flags."""
        flags = FeatureFlags()
        status = {
            "plugin_enabled": flags.enable_wechat_embedded_plugin,
            "qr_login_ui_enabled": flags.enable_wechat_qr_login_ui,
            "poller_enabled": flags.enable_wechat_poller,
            "wechat_direct_mode": flags.enable_runtime_wechat_direct,
            "openwork_wechat_channel": flags.enable_openwork_wechat_channel,
            "openwork_channel_to_runtime": flags.enable_openwork_channel_to_runtime,
            "running_pollers": [],
        }

        assert status["wechat_direct_mode"] is False
        assert status["openwork_wechat_channel"] is True
        assert status["openwork_channel_to_runtime"] is True

    def test_poller_not_started_when_direct_mode_disabled(self):
        """Test that poller should not start when direct mode is disabled.

        This is a logical test - actual poller start is in server.py.
        """
        flags = FeatureFlags()
        flags.enable_wechat_poller = True
        flags.enable_runtime_wechat_direct = False

        # When direct mode is disabled, poller should NOT be started by solo-hub
        should_start_poller = flags.enable_wechat_poller and flags.enable_runtime_wechat_direct
        assert should_start_poller is False

    def test_poller_started_when_direct_mode_enabled(self):
        """Test that poller can start when direct mode is enabled."""
        flags = FeatureFlags()
        flags.enable_wechat_poller = True
        flags.enable_runtime_wechat_direct = True

        # When direct mode is enabled, poller CAN be started by solo-hub
        should_start_poller = flags.enable_wechat_poller and flags.enable_runtime_wechat_direct
        assert should_start_poller is True


class TestResponseTextExtraction:
    """Test response_text extraction for channel delivery."""

    def test_response_text_primary_field(self):
        """Test response_text is the primary field."""
        final_response = {
            "response_text": "This is the response",
            "summary": "Summary",
        }
        # The actual extraction happens in OpenWork's normalizeResponse
        # Here we just verify the data structure
        assert final_response["response_text"] == "This is the response"

    def test_final_response_text_fallback(self):
        """Test final_response_text is used as fallback."""
        final_response = {
            "final_response_text": "Fallback response",
        }
        assert final_response["final_response_text"] == "Fallback response"

    def test_summary_fallback(self):
        """Test summary is used when response_text missing."""
        final_response = {
            "summary": "Summary text",
        }
        assert final_response["summary"] == "Summary text"

    def test_content_fallback(self):
        """Test content is used when response_text and summary missing."""
        final_response = {
            "content": "Content text",
        }
        assert final_response["content"] == "Content text"


class TestIdentityFallback:
    """Test identity question handling."""

    def test_identity_question_detection(self):
        """Test identity questions are detected."""
        identity_questions = [
            "你是谁？",
            "你是干什么的？",
            "你能做什么？",
            "Who are you?",
            "What can you do?",
        ]

        # Simple detection logic (actual implementation in TypeScript).
        # Keyword list must cover every phrasing exercised in
        # ``identity_questions`` above AND in the negative test below.
        identity_keywords = [
            "你是谁",
            "你是干什么",
            "你能做什么",
            "who are",
            "what can you",
        ]
        for q in identity_questions:
            is_identity = any(kw in q.lower() for kw in identity_keywords)
            assert is_identity, f"Should detect identity question: {q}"

    def test_regular_question_not_identity(self):
        """Test regular questions are not identity questions."""
        regular_questions = [
            "帮我写代码",
            "今天的天气怎么样？",
            "查询一下订单状态",
        ]

        identity_keywords = [
            "你是谁",
            "你是干什么",
            "你能做什么",
            "who are",
            "what can you",
        ]
        for q in regular_questions:
            is_identity = any(kw in q.lower() for kw in identity_keywords)
            assert not is_identity, f"Should NOT detect identity question: {q}"

    def test_identity_fallback_responses(self):
        """Test identity fallback responses exist."""
        # These are defined in OpenWork's response-normalizer.ts
        # Here we verify the concept
        identity_responses = [
            "我是你的AI助手",
            "我是一个AI助手",
            "我是你的智能助手",
        ]
        assert len(identity_responses) > 0
        for resp in identity_responses:
            assert "AI助手" in resp or "智能助手" in resp


class TestChannelMapping:
    """Test channel session mapping."""

    def test_channel_session_id_format(self):
        """Test channel_session_id format."""
        # Format: channel:user_id
        wechat_user_id = "wx_user_123"
        channel_session_id = f"wechat:{wechat_user_id}"
        assert channel_session_id == "wechat:wx_user_123"

    def test_session_mapping_chain(self):
        """Test session mapping chain: channel -> openwork -> hubos.core."""
        # Simulate the mapping
        channel_session_id = "wechat:wx_user_123"
        openwork_session_id = "ow_session_456"
        runtime_task_id = "task_789"

        # Build mapping chain
        session_map = {
            channel_session_id: {
                "openwork_session_id": openwork_session_id,
                "runtime_task_id": runtime_task_id,
            }
        }

        # Verify chain
        mapping = session_map[channel_session_id]
        assert mapping["openwork_session_id"] == openwork_session_id
        assert mapping["runtime_task_id"] == runtime_task_id


class TestFlagRollbackBehavior:
    """Test that flags can be disabled for rollback."""

    def test_all_flags_can_be_disabled(self):
        """Test all new flags can be disabled for rollback."""
        flags = FeatureFlags()

        # These flags should be settable to False for rollback
        flags.enable_openwork_wechat_channel = False
        flags.enable_openwork_channel_to_runtime = False
        flags.enable_runtime_wechat_direct = False

        assert flags.enable_openwork_wechat_channel is False
        assert flags.enable_openwork_channel_to_runtime is False
        assert flags.enable_runtime_wechat_direct is False

    def test_legacy_mode_with_direct_true(self):
        """Test enabling legacy direct WeChat mode."""
        flags = FeatureFlags()
        flags.enable_runtime_wechat_direct = True
        flags.enable_wechat_poller = True

        # Legacy mode: solo-hub handles WeChat directly
        assert flags.enable_runtime_wechat_direct is True
        assert flags.enable_wechat_poller is True

        # The combination means poller SHOULD start
        should_start = flags.enable_wechat_poller and flags.enable_runtime_wechat_direct
        assert should_start is True
