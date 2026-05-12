# -*- coding: utf-8 -*-
"""Tests for task_plan_risk — heuristic risk assessment."""
from __future__ import annotations

import pytest

from hubos.app.task_plan_risk import (
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
    RiskAssessment,
    assess_plan_risk,
)


class TestAssessPlanRisk:
    def test_send_email_high(self):
        r = assess_plan_risk("发送邮件给客户", [{}])
        assert r.level == RISK_HIGH
        assert r.requires_confirmation is True

    def test_outreach_high(self):
        r = assess_plan_risk(
            "Customer outreach",
            [{"title": "Send cold emails"}],
        )
        assert r.level == RISK_HIGH

    def test_delete_high(self):
        r = assess_plan_risk("清理数据库", [{"title": "删除旧记录"}])
        assert r.level == RISK_HIGH

    def test_deploy_high(self):
        r = assess_plan_risk("部署到生产环境", [{"title": "deploy to prod"}])
        assert r.level == RISK_HIGH

    def test_push_commit_high(self):
        r = assess_plan_risk("代码提交", [{"title": "push to main"}])
        assert r.level == RISK_HIGH

    def test_payment_high(self):
        r = assess_plan_risk("自动付款", [{"title": "转账"}])
        assert r.level == RISK_HIGH

    def test_bulk_high(self):
        r = assess_plan_risk("批量操作用户数据", [{"title": "批量修改"}])
        assert r.level == RISK_HIGH

    def test_production_live_high(self):
        r = assess_plan_risk("正式发送通知", [{"title": "push to production"}])
        assert r.level == RISK_HIGH

    # ── medium ───────────────────────────────────────────────────────────

    def test_config_medium(self):
        r = assess_plan_risk("修改配置", [{"title": "Update config file"}])
        assert r.level == RISK_MEDIUM
        assert r.requires_confirmation is False

    def test_env_secret_medium(self):
        r = assess_plan_risk("Update .env", [{"title": "Rotate api key"}])
        assert r.level == RISK_MEDIUM

    def test_scrape_medium(self):
        r = assess_plan_risk("数据采集", [{"title": "scrape website"}])
        assert r.level == RISK_MEDIUM

    def test_external_api_medium(self):
        r = assess_plan_risk("调用外部服务", [{"title": "Call external API"}])
        assert r.level == RISK_MEDIUM

    def test_write_file_medium(self):
        r = assess_plan_risk("生成报告", [{"title": "write file to disk"}])
        assert r.level == RISK_MEDIUM

    # ── low ──────────────────────────────────────────────────────────────

    def test_analysis_low(self):
        r = assess_plan_risk("数据分析", [{"title": "Analyze trends"}])
        assert r.level == RISK_LOW
        assert r.requires_confirmation is False

    def test_research_low(self):
        r = assess_plan_risk("调研市场", [{"title": "Research competitors"}])
        assert r.level == RISK_LOW

    def test_summary_low(self):
        r = assess_plan_risk("总结报告", [{"title": "Summarize findings"}])
        assert r.level == RISK_LOW

    # ── user text considered ─────────────────────────────────────────────

    def test_user_text_checked(self):
        r = assess_plan_risk("普通标题", [{}], original_user_text="帮我群发邮件给客户")
        assert r.level == RISK_HIGH

    # ── multiple reasons ─────────────────────────────────────────────────

    def test_multiple_high_reasons(self):
        r = assess_plan_risk("发布并付款", [{"title": "deploy and pay"}])
        assert r.level == RISK_HIGH
        assert len(r.reasons) >= 2


class TestRiskAssessment:
    def test_requires_confirmation_only_high(self):
        assert RiskAssessment(level=RISK_HIGH).requires_confirmation is True
        assert RiskAssessment(level=RISK_MEDIUM).requires_confirmation is False
        assert RiskAssessment(level=RISK_LOW).requires_confirmation is False
