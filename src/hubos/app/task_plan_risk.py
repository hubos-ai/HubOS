# -*- coding: utf-8 -*-
"""Heuristic risk assessment for task plans.

Detects high/medium/low risk actions based on keywords in plan titles,
steps, and user text. No LLM calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, List


# ---------------------------------------------------------------------------
# Risk levels
# ---------------------------------------------------------------------------


RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"


@dataclass
class RiskAssessment:
    level: str = RISK_LOW
    reasons: List[str] = field(default_factory=list)

    @property
    def requires_confirmation(self) -> bool:
        return self.level == RISK_HIGH


# ---------------------------------------------------------------------------
# Keyword rules
# ---------------------------------------------------------------------------

_HIGH_RULES: List[tuple[re.Pattern, str]] = [
    (
        re.compile(
            r"发送邮件|群发|私信|联系客户|send\s+email|outreach" r"|message\s+customer",
            re.IGNORECASE,
        ),
        "involves sending messages to external parties",
    ),
    (
        re.compile(
            r"删除|清空|覆盖|delete|remove|overwrite|drop\s+table",
            re.IGNORECASE,
        ),
        "involves deleting or overwriting data",
    ),
    (
        re.compile(r"push|commit|发布|deploy|release|publish", re.IGNORECASE),
        "involves deploying or publishing changes",
    ),
    (
        re.compile(r"付款|转账|payment|pay|transfer\s+funds", re.IGNORECASE),
        "involves financial transactions",
    ),
    (
        re.compile(r"批量操作|批量修改|bulk|mass\s+", re.IGNORECASE),
        "involves bulk/mass operations",
    ),
    (
        re.compile(r"真实发送|正式发送|production|live\s+env", re.IGNORECASE),
        "targets production/live environment",
    ),
]

_MEDIUM_RULES: List[tuple[re.Pattern, str]] = [
    (
        re.compile(r"修改配置|config|\.env|secret|api\s*key", re.IGNORECASE),
        "modifies configuration or secrets",
    ),
    (
        re.compile(r"抓取大量数据|scrape|crawl", re.IGNORECASE),
        "invololves large-scale data scraping",
    ),
    (
        re.compile(r"调用外部服务|external\s+api", re.IGNORECASE),
        "calls external services",
    ),
    (
        re.compile(r"生成并保存文件|write\s+file|save\s+file", re.IGNORECASE),
        "writes or saves files",
    ),
]


# ---------------------------------------------------------------------------
# assess_plan_risk
# ---------------------------------------------------------------------------


def assess_plan_risk(
    title: str,
    steps: list[Any],
    original_user_text: str = "",
) -> RiskAssessment:
    """Assess risk level of a plan based on heuristic keyword matching.

    Checks title, step titles/descriptions, and original user text.
    """
    # Collect all text to scan
    texts = [title]
    if original_user_text:
        texts.append(original_user_text)

    for step in steps:
        if isinstance(step, dict):
            texts.append(step.get("title", ""))
            texts.append(step.get("description", ""))
        else:
            texts.append(getattr(step, "title", ""))
            texts.append(getattr(step, "description", ""))

    combined = " ".join(t for t in texts if t)

    # Check high risk first
    high_reasons: List[str] = []
    for pattern, reason in _HIGH_RULES:
        if pattern.search(combined):
            high_reasons.append(reason)

    if high_reasons:
        return RiskAssessment(level=RISK_HIGH, reasons=high_reasons)

    # Check medium risk
    medium_reasons: List[str] = []
    for pattern, reason in _MEDIUM_RULES:
        if pattern.search(combined):
            medium_reasons.append(reason)

    if medium_reasons:
        return RiskAssessment(level=RISK_MEDIUM, reasons=medium_reasons)

    return RiskAssessment(level=RISK_LOW, reasons=[])
