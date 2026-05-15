# -*- coding: utf-8 -*-
"""RunPolicy — lightweight run-depth classification.

RunDepth (light/normal/deep) controls Experience matching and Knowledge injection.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# RunDepth constants
# ---------------------------------------------------------------------------

LIGHT = "light"
NORMAL = "normal"
DEEP = "deep"

# ---------------------------------------------------------------------------
# Pattern libraries — RunDepth
# ---------------------------------------------------------------------------

# Short / simple Q&A patterns → light
_LIGHT_PATTERNS = re.compile(
    r"^(这是什么意思|可以吗|继续|好的|解释一下|什么意思|现在几点"
    r"|是的|不是|对|好|ok|okay|yes|no|继续|好的呢|嗯|好的好的"
    r"|what is this|continue|ok|explain|what does this mean"
    r"|how are you|thanks|thank you|谢谢|没问题|明白了|收到)$",
    re.IGNORECASE,
)

# Complex task keywords → deep
_DEEP_KEYWORDS = re.compile(
    r"开发|实现|修复|优化|部署|重构|调研|客户开发|找客户|批量|自动化"
    r"|多agent|多步骤|multi.?agent|automat|batch|refactor"
    r"|deploy|implement|optimize|research",
    re.IGNORECASE,
)

# Multi-step connectors → deep
_STEP_CONNECTORS = re.compile(
    r"先.{2,}然后|先.{2,}再|然后.{2,}最后|并且|接着",
)

_LIGHT_MAX_CHARS = 6
_DEEP_MIN_CHARS = 80

# Explicit historical-knowledge request → override budget to 1000
_EXPLICIT_REQUEST_PATTERNS = re.compile(
    r"参考历史经验|查历史知识|根据之前经验|按照之前方法|参照上次|按历史|复用之前|follow previous",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_run_depth(user_text: str) -> str:
    """Classify user input into LIGHT / NORMAL / DEEP."""
    text = (user_text or "").strip()
    if not text:
        return LIGHT

    # 1. Explicit simple Q&A patterns → light (regardless of length)
    if _LIGHT_PATTERNS.match(text):
        return LIGHT

    # 2. Complex keywords → deep (checked before length short-circuit)
    if _DEEP_KEYWORDS.search(text):
        return DEEP

    # 3. Multi-step connectors → deep
    if _STEP_CONNECTORS.search(text):
        return DEEP

    # 4. Long input → deep
    if len(text) >= _DEEP_MIN_CHARS:
        return DEEP

    # 5. Very short input with no keywords/connectors → light
    if len(text) <= _LIGHT_MAX_CHARS:
        return LIGHT

    # 6. Default → normal
    return NORMAL


def should_use_experience(depth: str) -> bool:
    """Whether to run Experience matching at this depth."""
    return depth != LIGHT


def knowledge_budget_for(depth: str, user_text: str = "") -> int:
    """Token budget for Knowledge injection at this depth.

    Returns 0 for light, 300/600 for normal/deep, 1000 for explicit requests.
    """
    if depth == LIGHT:
        return 0

    # Check explicit request override
    if user_text and _EXPLICIT_REQUEST_PATTERNS.search(user_text):
        return 1000

    if depth == DEEP:
        return 600

    return 300
