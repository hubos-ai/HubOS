# -*- coding: utf-8 -*-
"""Tests for RunPolicy run-depth and knowledge-budget classification."""
from __future__ import annotations

from hubos.app.run_policy import (
    DEEP,
    LIGHT,
    NORMAL,
    classify_run_depth,
    knowledge_budget_for,
    should_use_experience,
)


# ---------------------------------------------------------------------------
# classify_run_depth
# ---------------------------------------------------------------------------


class TestClassifyRunDepth:
    def test_empty_string_is_light(self):
        assert classify_run_depth("") == LIGHT

    def test_short_greeting_is_light(self):
        assert classify_run_depth("继续") == LIGHT

    def test_short_english_ok_is_light(self):
        assert classify_run_depth("ok") == LIGHT

    def test_what_is_this_is_light(self):
        assert classify_run_depth("这是什么意思") == LIGHT

    def test_explain_is_light(self):
        assert classify_run_depth("解释一下") == LIGHT

    def test_yes_is_light(self):
        assert classify_run_depth("是的") == LIGHT

    def test_15_char_question_is_light(self):
        # "什么意思" is a light pattern, matched before length check.
        assert classify_run_depth("什么意思") == LIGHT

    def test_6_char_exactly_is_light(self):
        assert classify_run_depth("x" * 6) == LIGHT

    def test_21_char_default_is_not_light(self):
        # 21 chars, no keywords → normal.
        assert classify_run_depth("请帮我找一个靠谱的") == NORMAL

    def test_normal_task(self):
        assert classify_run_depth("帮我找巴西供应商") == NORMAL

    def test_deep_keyword_开发(self):
        assert classify_run_depth("帮我开发一个工具") == DEEP

    def test_deep_keyword_implement(self):
        assert classify_run_depth("implement a new feature") == DEEP

    def test_deep_keyword_批量(self):
        assert classify_run_depth("批量导入数据") == DEEP

    def test_deep_long_input(self):
        assert classify_run_depth("x" * 80) == DEEP

    def test_deep_79_char_is_not_deep_by_length(self):
        text = "x" * 79
        assert classify_run_depth(text) != DEEP

    def test_deep_step_connectors(self):
        assert classify_run_depth("先写代码，然后测试，再部署") == DEEP

    def test_deep_multi_agent(self):
        assert classify_run_depth("用多agent协作完成") == DEEP


# ---------------------------------------------------------------------------
# should_use_experience
# ---------------------------------------------------------------------------


class TestShouldUseExperience:
    def test_light_returns_false(self):
        assert should_use_experience(LIGHT) is False

    def test_normal_returns_true(self):
        assert should_use_experience(NORMAL) is True

    def test_deep_returns_true(self):
        assert should_use_experience(DEEP) is True


# ---------------------------------------------------------------------------
# knowledge_budget_for
# ---------------------------------------------------------------------------


class TestKnowledgeBudget:
    def test_light_returns_0(self):
        assert knowledge_budget_for(LIGHT) == 0

    def test_normal_returns_300(self):
        assert knowledge_budget_for(NORMAL) == 300

    def test_deep_returns_600(self):
        assert knowledge_budget_for(DEEP) == 600

    def test_explicit_request_returns_1000(self):
        assert knowledge_budget_for(NORMAL, "请参考历史经验完成") == 1000

    def test_explicit_request_overrides_deep(self):
        assert knowledge_budget_for(DEEP, "根据之前经验做") == 1000

    def test_no_explicit_normal_stays_300(self):
        assert knowledge_budget_for(NORMAL, "帮我找供应商") == 300

    def test_light_ignores_explicit(self):
        assert knowledge_budget_for(LIGHT, "参考历史经验") == 0


# ---------------------------------------------------------------------------
# Runner integration guard
# ---------------------------------------------------------------------------


class TestRunnerRunPolicyIntegration:
    """Runner should only use depth/budget, not auto-routing mode logic."""

    def test_runner_has_run_depth_import(self):
        from pathlib import Path

        runner_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "hubos" / "app" / "runner" / "runner.py"
        )
        source = runner_path.read_text(encoding="utf-8")
        assert "classify_run_depth" in source
        assert "knowledge_budget_for" in source
        assert "classify_run_mode" not in source
        assert "maybe_auto_route" not in source
        assert "auto_routed" not in source

    def test_context_shows_depth(self):
        from pathlib import Path

        runner_path = (
            Path(__file__).resolve().parents[3]
            / "src" / "hubos" / "app" / "runner" / "runner.py"
        )
        source = runner_path.read_text(encoding="utf-8")
        assert "depth=" in source
        assert "run_depth" in source
        assert "mode=" not in source
