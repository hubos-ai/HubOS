# -*- coding: utf-8 -*-
"""Keyword extraction helpers for Work Experience retrieval.

The chat runtime often receives Chinese task text. Plain ASCII tokenization misses
business intent such as "找客户" or country names such as "乌兹别克斯坦". These
helpers add a small semantic layer so retrieval can match practical work patterns.
"""

from __future__ import annotations

import re
from typing import Iterable

_COUNTRY_ALIASES: dict[str, tuple[str, ...]] = {
    "uz": ("uz", "uzb", "uzbekistan", "乌兹别克斯坦", "乌兹", "uzbekiston"),
    "ru": ("ru", "rus", "russia", "俄罗斯"),
    "br": ("br", "bra", "brazil", "巴西"),
    "mx": ("mx", "mexico", "墨西哥"),
    "us": ("us", "usa", "united states", "美国"),
    "ae": ("ae", "uae", "united arab emirates", "阿联酋"),
    "sa": ("sa", "saudi", "saudi arabia", "沙特"),
    "jp": ("jp", "japan", "日本"),
    "kr": ("kr", "korea", "south korea", "韩国"),
    "de": ("de", "germany", "德国"),
    "vn": ("vn", "vietnam", "越南"),
}

_INTENT_ALIASES: dict[str, tuple[str, ...]] = {
    "customer_development": (
        "找客户",
        "挖客户",
        "开发客户",
        "客户开发",
        "客户线索",
        "潜在客户",
        "leads",
        "lead",
        "prospect",
        "prospects",
        "customer leads",
        "find_customer_leads",
        "find customer",
        "find customers",
    ),
    "education_equipment": (
        "教育设备",
        "教学用品",
        "教学仪器",
        "教学设备",
        "实验室用品",
        "实验室设备",
        "教学模型",
        "educational equipment",
        "school equipment",
        "laboratory equipment",
        "teaching models",
        "lab equipment",
    ),
    "distributor": (
        "经销商",
        "分销商",
        "进口商",
        "批发商",
        "采购商",
        "供应商",
        "distributor",
        "importer",
        "wholesaler",
        "procurement",
        "buyer",
        "supplier",
        "vendor",
    ),
    "search": ("搜索", "查找", "搜", "search", "crawl", "web", "browser"),
    "feishu": (
        "飞书",
        "feishu",
        "lark",
        "bitable",
        "多维表格",
        "多维文档",
        "知识库",
    ),
    "data_import": (
        "导入",
        "导入数据",
        "批量导入",
        "数据导入",
        "import",
        "batch_create",
    ),
    "skill_registry": (
        "技能池",
        "skill_pool",
        "注册技能",
        "技能注册",
    ),
}


def extract_semantic_keywords(values: Iterable[object]) -> list[str]:
    """Extract ASCII tokens plus Chinese/business semantic keywords."""
    keywords: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        lower = text.lower()
        keywords.update(re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]*", lower))

        for canonical, aliases in _COUNTRY_ALIASES.items():
            if any(alias in lower or alias in text for alias in aliases):
                keywords.add(canonical)
                # Add readable aliases for overlap with older cards/tools.
                keywords.update(
                    a for a in aliases if re.match(r"^[a-z0-9_-]+$", a)
                )

        for canonical, aliases in _INTENT_ALIASES.items():
            if any(alias in lower or alias in text for alias in aliases):
                keywords.add(canonical)
                if canonical == "customer_development":
                    keywords.update(
                        {
                            "customer",
                            "customers",
                            "lead",
                            "leads",
                            "prospect",
                            "hunter",
                        },
                    )
                elif canonical == "education_equipment":
                    keywords.update(
                        {
                            "education",
                            "educational",
                            "laboratory",
                            "equipment",
                            "teaching",
                        },
                    )
                elif canonical == "distributor":
                    keywords.update(
                        {"distributor", "importer", "buyer", "procurement", "supplier", "vendor"},
                    )

        if "客户" in text and any(
            marker in text for marker in ("找", "搜", "查", "挖", "开发")
        ):
            keywords.add("customer_development")
            keywords.update(
                {
                    "customer",
                    "customers",
                    "lead",
                    "leads",
                    "prospect",
                    "hunter",
                },
            )

    return sorted(keywords)
