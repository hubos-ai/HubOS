# -*- coding: utf-8 -*-
from hubos.core.work_experience.keyword_utils import extract_semantic_keywords


def test_extracts_chinese_customer_development_and_country_keywords() -> None:
    keywords = set(extract_semantic_keywords(["在帮我找下乌兹别克斯坦的客户，找5个"]))

    assert "customer_development" in keywords
    assert "leads" in keywords
    assert "customer" in keywords
    assert "uz" in keywords
    assert "uzbekistan" in keywords
