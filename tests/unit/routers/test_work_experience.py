# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name
"""Unit tests for the v4-backed Work Experience admin REST API."""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hubos.app.routers.work_experience import router
from hubos.core.work_experience.schemas_v4 import WorkflowCard
from hubos.core.work_experience.store_v4 import CardStore

app = FastAPI()
app.include_router(router, prefix="/api")


@pytest.fixture
def tmp_root():
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def store(tmp_root):
    return CardStore(root=tmp_root / "work_experience_v4")


@pytest.fixture
def api_client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def make_card(
    store: CardStore,
    card_id: str = "test-card",
    task_type: str = "测试任务方法论",
    description: str = "用于测试的工作方法卡",
    status: str = "approved",
    level: str = "mature",
    executions: int = 3,
    disabled: bool = False,
    workflow: list[str] | None = None,
    tools: dict[str, str] | None = None,
    pitfalls: list[str] | None = None,
    success_patterns: list[str] | None = None,
) -> WorkflowCard:
    card = WorkflowCard(
        card_id=card_id,
        task_type=task_type,
        description=description,
        workflow=workflow or ["先分析任务", "选择工具", "验证结果"],
        tools=tools or {"search": "找公开资料", "browser": "验证页面"},
        pitfalls=pitfalls or ["不要泛搜"],
        success_patterns=success_patterns or ["先找权威数据源"],
        executions=executions,
        status=status,
        experience_level=level,
        disabled=disabled,
        source_sessions=["session-1"],
    )
    store.save(card)
    return card


def patch_store(store: CardStore):
    return patch(
        "hubos.app.routers.work_experience._get_store",
        return_value=store,
    )


async def test_list_cards_empty(api_client, store):
    with patch_store(store):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards")
    assert resp.status_code == 200
    assert resp.json() == {"cards": [], "total": 0}


async def test_list_cards_returns_v4_cards(api_client, store):
    make_card(store, card_id="a", task_type="A", status="candidate")
    make_card(store, card_id="b", task_type="B", status="approved")
    make_card(
        store,
        card_id="c",
        task_type="C",
        status="rejected",
        disabled=True,
    )

    with patch_store(store):
        async with api_client as c:
            resp = await c.get(
                "/api/work-experience/cards?include_disabled=true",
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert {card["experience_id"] for card in data["cards"]} == {"a", "b", "c"}


async def test_list_cards_filters_status_level_and_scope(api_client, store):
    make_card(store, card_id="a", status="candidate", level="new")
    make_card(store, card_id="b", status="approved", level="mature")

    with patch_store(store):
        async with api_client as c:
            resp = await c.get(
                "/api/work-experience/cards?status=approved&level=mature&scope=global",
            )
            empty = await c.get("/api/work-experience/cards?scope=session")

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["cards"][0]["experience_id"] == "b"
    assert empty.status_code == 200
    assert empty.json()["total"] == 0


async def test_list_cards_pagination(api_client, store):
    for i in range(5):
        make_card(store, card_id=f"card-{i}", task_type=f"Card {i}")

    with patch_store(store):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards?limit=2&offset=1")

    assert resp.status_code == 200
    assert resp.json()["total"] == 5
    assert len(resp.json()["cards"]) == 2


async def test_list_cards_invalid_filters(api_client, store):
    with patch_store(store):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards?status=bad")
            level = await c.get("/api/work-experience/cards?level=bad")
    assert resp.status_code == 400
    assert level.status_code == 400


async def test_get_card(api_client, store):
    make_card(store, card_id="gov-procurement", task_type="政府采购客户开发")

    with patch_store(store):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards/gov-procurement")

    assert resp.status_code == 200
    data = resp.json()
    assert data["experience_id"] == "gov-procurement"
    assert data["title"] == "政府采购客户开发"
    assert data["scope"] == "global"
    assert data["recommended_workflow"]


async def test_get_card_not_found(api_client, store):
    with patch_store(store):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards/missing")
    assert resp.status_code == 404


async def test_status_transitions_persist(api_client, store):
    make_card(store, card_id="a", status="candidate", disabled=False)

    with patch_store(store):
        async with api_client as c:
            resp = await c.post("/api/work-experience/cards/a/archive")

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "archived"
    stored = store.get("a")
    assert stored is not None
    assert stored.status == "archived"
    assert stored.disabled is True


async def test_reactivate_restores_disabled_deprecated(api_client, store):
    make_card(
        store,
        card_id="a",
        status="archived",
        level="deprecated",
        disabled=True,
    )

    with patch_store(store):
        async with api_client as c:
            resp = await c.post("/api/work-experience/cards/a/reactivate")

    assert resp.status_code == 200
    stored = store.get("a")
    assert stored is not None
    assert stored.status == "candidate"
    assert stored.experience_level == "observed"
    assert stored.disabled is False


async def test_level_transitions(api_client, store):
    make_card(store, card_id="a", level="new")

    with patch_store(store):
        async with api_client as c:
            promote = await c.post("/api/work-experience/cards/a/promote")
            deprecate = await c.post("/api/work-experience/cards/a/deprecate")

    assert promote.status_code == 200
    assert promote.json()["new_level"] == "observed"
    assert deprecate.status_code == 200
    stored = store.get("a")
    assert stored is not None
    assert stored.experience_level == "deprecated"
    assert stored.disabled is True


async def test_quality_and_maturity(api_client, store):
    make_card(store, card_id="a", executions=10)

    with patch_store(store):
        async with api_client as c:
            quality = await c.get("/api/work-experience/cards/a/quality-score")
            maturity = await c.get("/api/work-experience/cards/a/maturity")

    assert quality.status_code == 200
    assert quality.json()["hit_count"] == 10
    assert quality.json()["quality_score"] > 0
    assert maturity.status_code == 200
    assert maturity.json()["experience_level"] == "mature"


async def test_find_duplicates(api_client, store):
    make_card(store, card_id="a", task_type="政府采购开发", tools={"search": "搜索"})
    make_card(store, card_id="b", task_type="政府采购开发优化", tools={"search": "搜索"})
    make_card(store, card_id="c", task_type="图片生成", tools={"image": "画图"})

    with patch_store(store):
        async with api_client as c:
            resp = await c.get(
                "/api/work-experience/cards/a/duplicates?threshold=0.1",
            )

    assert resp.status_code == 200
    ids = {item["experience_id"] for item in resp.json()["duplicates"]}
    assert "b" in ids


async def test_merge_cards(api_client, store):
    make_card(
        store,
        card_id="target",
        workflow=["A"],
        tools={"search": "old"},
        executions=2,
    )
    make_card(
        store,
        card_id="source",
        workflow=["B"],
        tools={"browser": "new"},
        executions=3,
    )

    with patch_store(store):
        async with api_client as c:
            resp = await c.post(
                "/api/work-experience/merge",
                json={"source_id": "source", "target_id": "target"},
            )

    assert resp.status_code == 200
    target = store.get("target")
    source = store.get("source")
    assert target is not None and source is not None
    assert target.executions == 5
    assert "B" in target.workflow
    assert source.status == "archived"
    assert source.disabled is True


async def test_stats_and_top_cards(api_client, store):
    make_card(
        store,
        card_id="low",
        status="candidate",
        level="new",
        executions=1,
    )
    make_card(
        store,
        card_id="high",
        status="approved",
        level="mature",
        executions=20,
    )

    with patch_store(store):
        async with api_client as c:
            stats = await c.get("/api/work-experience/stats")
            top = await c.get("/api/work-experience/top-cards?top_k=1")
            by_level = await c.get("/api/work-experience/by-level")

    assert stats.status_code == 200
    assert stats.json()["total_cards"] == 2
    assert stats.json()["by_status"]["approved"] == 1
    assert top.status_code == 200
    assert top.json()["cards"][0]["experience_id"] == "high"
    assert by_level.status_code == 200
    assert by_level.json()["mature"] == 1


async def test_candidates(api_client, store):
    make_card(store, card_id="candidate", status="candidate")
    make_card(store, card_id="approved", status="approved")

    with patch_store(store):
        async with api_client as c:
            resp = await c.get("/api/work-experience/candidates")

    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["cards"][0]["experience_id"] == "candidate"
