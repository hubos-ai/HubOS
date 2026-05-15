# -*- coding: utf-8 -*-
"""Tests for Knowledge Injection Layer."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hubos.core.knowledge_injection import (
    KnowledgeHit,
    KnowledgeInjectionConfig,
    _parse_knowledge_file,
    _score_hit,
    build_relevant_guidance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_card(**overrides):
    """Create a minimal WorkflowCard-like object."""
    from hubos.core.work_experience.schemas_v4 import WorkflowCard

    defaults = dict(
        task_type="政府采购供应商开发",
        description="测试卡片",
        workflow=["步骤1", "步骤2"],
        tools={"browser": "用于搜索"},
        pitfalls=["坑1"],
        success_patterns=["模式1"],
        experience_type="customer_development",
        entities=["Brazil", "CNPJ"],
        executions=3,
    )
    defaults.update(overrides)
    return WorkflowCard(**defaults)


def _write_knowledge_file(tmp: Path, content: str, name: str = "test.md"):
    """Write a knowledge .md file in the correct directory."""
    kdir = tmp / "memory" / "knowledge"
    kdir.mkdir(parents=True, exist_ok=True)
    (kdir / name).write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Experience card generates guidance
# ---------------------------------------------------------------------------


class TestExperienceCardGuidance:
    def test_card_generates_guidance(self):
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="帮我找巴西供应商",
            experience_card=card,
        )
        assert text  # non-empty
        assert "work_experience" in text
        assert meta["item_count"] == 1
        assert meta["sources"] == {"work_experience": 1}
        assert "titles" in meta
        assert "scores" in meta

    def test_card_includes_task_type(self):
        card = _make_card(task_type="数据导入")
        text, _ = build_relevant_guidance(
            user_message="导入数据",
            experience_card=card,
        )
        assert "数据导入" in text


# ---------------------------------------------------------------------------
# 2. Knowledge file type/entities match
# ---------------------------------------------------------------------------


class TestKnowledgeFileMatch:
    def test_type_entities_match(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## 巴西政府采购

            type: customer_development
            entities: Brazil, CNPJ, Compras.gov.br
            confidence: high

            Summary:
            巴西政府采购需要CNPJ号码注册供应商，使用Compras.gov.br平台。

            Use when:
            - 巴西政府采购
            - 供应商开发
        """
        )
        _write_knowledge_file(tmp_path, content)

        text, meta = build_relevant_guidance(
            user_message="帮我找巴西的CNPJ供应商",
            workspace_dir=tmp_path,
        )
        assert "knowledge" in meta.get("sources", {})
        assert meta["item_count"] >= 1


# ---------------------------------------------------------------------------
# 3. Irrelevant knowledge not injected
# ---------------------------------------------------------------------------


class TestIrrelevantKnowledge:
    def test_irrelevant_not_injected(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## 日本税务申报

            type: system_debug
            entities: Japan, 税务
            confidence: low

            Summary:
            日本税务申报流程指南，涉及消费税和法人税。

            Use when:
            - 日本税务
        """
        )
        _write_knowledge_file(tmp_path, content)

        text, meta = build_relevant_guidance(
            user_message="帮我写一个React组件",
            workspace_dir=tmp_path,
        )
        # score should be below min_score
        assert "knowledge" not in meta.get("sources", {})


# ---------------------------------------------------------------------------
# 4. Max items enforced
# ---------------------------------------------------------------------------


class TestMaxItems:
    def test_max_items_enforced(self, tmp_path):
        for i in range(5):
            content = textwrap.dedent(
                f"""\
                ## 测试知识{i}

                type: general
                entities: React, component{i}
                confidence: high

                Summary:
                React组件{i}的开发指南。

                Use when:
                - React开发
            """
            )
            _write_knowledge_file(tmp_path, content, name=f"k{i}.md")

        config = KnowledgeInjectionConfig(max_items=3, min_score=0.0)
        text, meta = build_relevant_guidance(
            user_message="React component development",
            workspace_dir=tmp_path,
            config=config,
        )
        assert meta["item_count"] <= 3


# ---------------------------------------------------------------------------
# 5. Token budget levels
# ---------------------------------------------------------------------------


class TestTokenBudget:
    def test_default_budget(self):
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="普通任务",
            experience_card=card,
        )
        assert meta["budget_tokens"] == 300

    def test_complex_keyword_budget(self):
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="帮我开发一个自动化批量导入工具",
            experience_card=card,
        )
        assert meta["budget_tokens"] == 600

    def test_complex_long_message_budget(self):
        card = _make_card()
        long_msg = "请帮我完成以下工作：" + "x" * 80
        text, meta = build_relevant_guidance(
            user_message=long_msg,
            experience_card=card,
        )
        assert meta["budget_tokens"] == 600

    def test_complex_step_connectors_budget(self):
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="先写代码，然后测试，再部署",
            experience_card=card,
        )
        assert meta["budget_tokens"] == 600

    def test_explicit_request_budget(self):
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="请参考历史经验完成这个任务",
            experience_card=card,
        )
        assert meta["budget_tokens"] == 1000


# ---------------------------------------------------------------------------
# 6. No hits returns empty
# ---------------------------------------------------------------------------


class TestNoHits:
    def test_no_hits_returns_empty(self):
        text, meta = build_relevant_guidance(
            user_message="简单问答",
        )
        assert text == ""
        assert meta["item_count"] == 0
        assert meta["estimated_tokens"] == 0
        assert meta["budget_tokens"] == 0
        assert meta["sources"] == {}
        assert meta["titles"] == []
        assert meta["scores"] == []

    def test_empty_dir_no_error(self, tmp_path):
        kdir = tmp_path / "memory" / "knowledge"
        kdir.mkdir(parents=True, exist_ok=True)

        text, meta = build_relevant_guidance(
            user_message="随便聊聊",
            workspace_dir=tmp_path,
        )
        assert text == ""


# ---------------------------------------------------------------------------
# 7. Scoring internals
# ---------------------------------------------------------------------------


class TestScoring:
    def test_score_hit_entity_overlap(self):
        hit = KnowledgeHit(
            source="knowledge",
            title="test",
            summary="Brazil supplier",
            score=0.0,
            type="customer_development",
            entities=["Brazil", "CNPJ"],
            confidence="high",
        )
        score = _score_hit(hit, "帮我找Brazil的CNPJ供应商", "")
        assert score > 0.3  # entity overlap + confidence bonus

    def test_score_hit_no_match(self):
        hit = KnowledgeHit(
            source="knowledge",
            title="test",
            summary="Japan tax guide",
            score=0.0,
            type="system_debug",
            entities=["Japan"],
            confidence="low",
        )
        score = _score_hit(hit, "帮我写React组件", "")
        assert score < 0.2

    def test_parse_knowledge_file(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## First Entry

            type: general
            domain: business
            entities: A, B
            tags: alpha, beta
            links: [[Related Note]]
            confidence: high

            Summary:
            First summary content.

            Evidence:
            - evidence A

            Use when:
            - condition A

            ## Second Entry

            type: code_fix
            entities: Python
            confidence: medium

            Summary:
            Second summary content.
        """
        )
        kdir = tmp_path / "memory" / "knowledge"
        kdir.mkdir(parents=True, exist_ok=True)
        fpath = kdir / "test.md"
        fpath.write_text(content, encoding="utf-8")

        entries = _parse_knowledge_file(fpath)
        assert len(entries) == 2
        assert entries[0].title == "First Entry"
        assert entries[0].type == "general"
        assert entries[0].domain == "business"
        assert entries[0].entities == ["A", "B"]
        assert entries[0].tags == ["alpha", "beta"]
        assert entries[0].links == ["[[Related Note]]"]
        assert entries[0].confidence == "high"
        assert "First summary" in entries[0].summary
        assert "evidence A" in entries[0].summary
        assert entries[1].title == "Second Entry"
        assert entries[1].type == "code_fix"

    def test_parse_obsidian_frontmatter(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## Obsidian Entry

            ---
            type: fact
            domain: tools
            entities: [webReader, DuckDuckGo]
            tags: search, fallback
            links: [[搜索工具优先级与兜底]], [[MCP 工具从 Python 脚本调用]]
            confidence: high
            updated: 2026-05-13
            ---

            Summary:
            webReader can render search result pages when direct HTML is empty.

            Use when:
            - search API fails
        """
        )
        kdir = tmp_path / "memory" / "knowledge"
        kdir.mkdir(parents=True, exist_ok=True)
        fpath = kdir / "obsidian.md"
        fpath.write_text(content, encoding="utf-8")

        entries = _parse_knowledge_file(fpath)
        assert len(entries) == 1
        assert entries[0].title == "Obsidian Entry"
        assert entries[0].type == "fact"
        assert entries[0].domain == "tools"
        assert entries[0].entities == ["webReader", "DuckDuckGo"]
        assert entries[0].tags == ["search", "fallback"]
        assert entries[0].links == [
            "[[搜索工具优先级与兜底]]",
            "[[MCP 工具从 Python 脚本调用]]",
        ]
        assert entries[0].confidence == "high"


# ---------------------------------------------------------------------------
# 8. Metadata observability
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_metadata_fields_present(self):
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="帮我找供应商",
            experience_card=card,
        )
        assert "item_count" in meta
        assert "estimated_tokens" in meta
        assert "budget_tokens" in meta
        assert "sources" in meta
        assert "titles" in meta
        assert "scores" in meta

    def test_metadata_sources_breakdown(self, tmp_path):
        content = textwrap.dedent(
            """\
            ## 测试知识

            type: general
            entities: 供应商
            confidence: high

            Summary:
            找供应商的流程。

            Use when:
            - 供应商开发
        """
        )
        _write_knowledge_file(tmp_path, content)
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="帮我找巴西供应商",
            experience_card=card,
            workspace_dir=tmp_path,
        )
        assert meta["item_count"] >= 2
        assert meta["sources"].get("work_experience") == 1
        assert meta["sources"].get("knowledge", 0) >= 1
        assert len(meta["titles"]) == meta["item_count"]
        assert len(meta["scores"]) == meta["item_count"]

    def test_metadata_estimated_tokens_positive(self):
        card = _make_card()
        text, meta = build_relevant_guidance(
            user_message="找供应商",
            experience_card=card,
        )
        assert meta["estimated_tokens"] > 0
        # Rough check: estimated_tokens ≈ len(text) / 4
        assert abs(meta["estimated_tokens"] - len(text) // 4) <= 1
