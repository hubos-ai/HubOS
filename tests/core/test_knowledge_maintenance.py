# -*- coding: utf-8 -*-
"""Tests for pending factual knowledge maintenance."""
from __future__ import annotations

from hubos.core.knowledge_maintenance import (
    format_candidate,
    sanitize_candidate,
    write_pending_candidates,
)


def test_write_pending_candidate(tmp_path):
    paths = write_pending_candidates(
        [
            {
                "title": "FNDE 使用 SIGARP",
                "domain": "business",
                "entities": ["Brazil", "FNDE", "SIGARP"],
                "tags": ["brazil", "procurement"],
                "links": ["[[巴西 FNDE 使用 SIGARP]]"],
                "confidence": "high",
                "summary": "FNDE 教育采购不完全在 Comprasnet，核心系统包含 SIGARP。",
                "evidence": ["历史查询 FNDE CNPJ 在 Compras API 返回空"],
                "use_when": ["开发巴西教育客户"],
                "details": ["需要真实浏览器时不要死磕 webReader"],
            },
        ],
        workspace_dir=tmp_path,
        session_id="s1",
        agent_id="default",
    )

    assert len(paths) == 1
    assert paths[0].parent == tmp_path / "memory" / "knowledge_pending"
    text = paths[0].read_text(encoding="utf-8")
    assert "status: pending" in text
    assert "source_session: s1" in text
    assert "FNDE 使用 SIGARP" in text
    assert "Evidence:" in text


def test_sensitive_candidate_skipped(tmp_path):
    paths = write_pending_candidates(
        [
            {
                "title": "API key 配置",
                "summary": "secret token 是 sk-abc123，不应写入知识库。",
            },
        ],
        workspace_dir=tmp_path,
    )
    assert paths == []


def test_sanitize_candidate_normalizes_lists():
    candidate = sanitize_candidate(
        {
            "title": "测试事实",
            "summary": "稳定事实。",
            "domain": "bad-domain",
            "entities": "A, B, A",
            "tags": ["x", "y"],
            "confidence": "bad",
        },
    )
    assert candidate is not None
    assert candidate.domain == "general"
    assert candidate.confidence == "medium"
    assert candidate.entities == ["A", "B"]


def test_format_candidate():
    candidate = sanitize_candidate(
        {
            "title": "测试事实",
            "summary": "稳定事实。",
            "entities": ["A"],
            "tags": ["tag"],
            "links": ["[[Related]]"],
            "evidence": ["observed"],
            "use_when": ["testing"],
            "details": ["detail"],
        },
        session_id="s2",
        agent_id="agent",
    )
    assert candidate is not None
    text = format_candidate(candidate)
    assert "links: [[Related]]" in text
    assert "Evidence:" in text
    assert "Use when:" in text
    assert "Details:" in text
