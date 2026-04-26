# -*- coding: utf-8 -*-
"""Regression tests for desktop chat Work Experience usage."""

from unittest.mock import patch

from hubos.app.runner.runner import (
    _build_chat_work_experience_task,
    _build_chat_work_guidance,
)
from hubos.core.infra.feature_flags import reload_feature_flags


_SAMPLE_CARD = {
    "experience_id": "card-1",
    "title": "Search domestic product prices",
    "usage_pattern_summary": "Domestic ecommerce price search",
    "recommended_tool_order": ["web_crawl", "tavily_search"],
    "recommended_workflow": ["search exact model", "compare stores"],
    "what_worked": ["Use exact SKU and Chinese keywords"],
    "what_failed": ["Do not rely on one marketplace"],
}


def test_build_chat_work_experience_task_uses_chat_context():
    task = _build_chat_work_experience_task(
        query="搜索 3M VFS401-WM 国内价格",
        session_id="session-1",
        channel="console",
    )

    assert task.input_text == "搜索 3M VFS401-WM 国内价格"
    assert task.session_id == "session-1"
    assert task.channel == "console"
    assert task.requested_workflow == "desktop_chat"
    assert task.task_id.startswith("chat-turn-")
    assert task.trace_id.startswith("chat-trace-")


def test_build_chat_work_guidance_requires_injection_flag():
    with patch.dict(
        "os.environ",
        {
            "ENABLE_WORK_EXPERIENCE_LAYER": "true",
            "ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "false",
        },
    ):
        reload_feature_flags()
        assert _build_chat_work_guidance([_SAMPLE_CARD]) == ""

    reload_feature_flags()


def test_build_chat_work_guidance_returns_work_guidance_when_enabled():
    with patch.dict(
        "os.environ",
        {
            "ENABLE_WORK_EXPERIENCE_LAYER": "true",
            "ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION": "true",
        },
    ):
        reload_feature_flags()
        guidance = _build_chat_work_guidance([_SAMPLE_CARD])

    reload_feature_flags()

    assert "[Work Guidance]" in guidance
    assert "web_crawl" in guidance
    assert "tavily_search" in guidance
    assert "[/Work Guidance]" in guidance
