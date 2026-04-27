# -*- coding: utf-8 -*-
# pylint: disable=redefined-outer-name
"""Unit tests for the work_experience admin REST API.

Tests the Phase 6.5 admin API:
- List / filter cards
- Get single card
- Status transitions (approve / reject / archive / reactivate)
- Duplicate detection
- Merge cards
- Quality stats
- Approved-only retrieval preserved (service-level enforcement)

Uses a patched LocalWorkExperienceStore backed by a temp directory.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from hubos.app.routers.work_experience import router
from hubos.core.work_experience import WorkExperienceService
from hubos.core.work_experience.schemas import (
    WorkExperience,
    WorkExperienceScope,
    WorkExperienceStatus,
)

app = FastAPI()
app.include_router(router, prefix="/api")


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def tmp_root():
    """Temp directory for the store."""
    with tempfile.TemporaryDirectory() as td:
        yield Path(td)


@pytest.fixture
def store(tmp_root):
    """Provide a clean LocalWorkExperienceStore backed by temp dir."""
    from hubos.core.work_experience.store import LocalWorkExperienceStore

    return LocalWorkExperienceStore(root=tmp_root / "we")


@pytest.fixture
def service(store):
    """Provide a WorkExperienceService."""
    return WorkExperienceService(store=store)


@pytest.fixture
def api_client():
    """Create an async test client."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


# =============================================================================
# Test Data
# =============================================================================


def make_card(
    store,
    scope=WorkExperienceScope.PROJECT,
    status=WorkExperienceStatus.CANDIDATE,
    title="Test Card",
    keywords=None,
    hint="type:test",
    confidence=0.8,
    hit_count=0,
    effective_count=0,
    experience_level=None,
):
    """Helper to create and save a test card."""
    from hubos.core.work_experience.schemas import ExperienceLevel as EL

    card = WorkExperience(
        scope=scope,
        trigger_keywords=keywords or ["test", "csv"],
        trigger_hint=hint,
        title=title,
        what_happened="Something happened",
        what_worked=["Method A worked"],
        what_failed=[],
        guidance="Use method A",
        avoidance="Avoid method B",
        confidence=confidence,
        source_task_id="task-test",
        source_session_id="session-1",
        source_trace_id="trace-1",
        applicability_tags=["test"],
        status=status,
        hit_count=hit_count,
        effective_count=effective_count,
        experience_level=experience_level or EL.NEW,
    )
    store.save(card)
    return card


# =============================================================================
# GET /api/work-experience/cards
# =============================================================================


async def test_list_cards_empty(api_client, store):
    """Empty store returns empty list."""
    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cards"] == []
    assert data["total"] == 0


async def test_list_cards_returns_all_statuses(api_client, store):
    """List returns all cards regardless of status (no governance filter at API level)."""
    c1 = make_card(
        store,
        status=WorkExperienceStatus.CANDIDATE,
        title="Card 1",
    )
    c2 = make_card(store, status=WorkExperienceStatus.APPROVED, title="Card 2")
    c3 = make_card(store, status=WorkExperienceStatus.REJECTED, title="Card 3")

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    titles = {card["title"] for card in data["cards"]}
    assert titles == {"Card 1", "Card 2", "Card 3"}


async def test_list_cards_filter_by_status(api_client, store):
    """Filter by status returns only matching cards."""
    make_card(
        store,
        status=WorkExperienceStatus.CANDIDATE,
        title="Candidate 1",
    )
    make_card(store, status=WorkExperienceStatus.APPROVED, title="Approved 1")

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards?status=approved")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["cards"][0]["title"] == "Approved 1"
    assert data["cards"][0]["status"] == "approved"


async def test_list_cards_filter_by_scope(api_client, store):
    """Filter by scope returns only matching cards."""
    make_card(store, scope=WorkExperienceScope.GLOBAL, title="Global Card")
    make_card(store, scope=WorkExperienceScope.PROJECT, title="Project Card")

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards?scope=global")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["cards"][0]["title"] == "Global Card"


async def test_list_cards_pagination(api_client, store):
    """Pagination works correctly."""
    for i in range(5):
        make_card(store, title=f"Card {i}")

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards?limit=2&offset=1")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["cards"]) == 2


async def test_list_cards_includes_governance_fields(api_client, store):
    """Listed cards include governance fields (quality_score, hit_count, effective_count)."""
    make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="Rich Card",
        confidence=0.9,
        hit_count=5,
        effective_count=2,
    )

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards")

    assert resp.status_code == 200
    card = resp.json()["cards"][0]
    assert "quality_score" in card
    assert "hit_count" in card
    assert "effective_count" in card
    assert "status" in card
    assert "last_used_at" in card
    # quality_score = 0.9 * (1 + 5/10 + 2/5) = 0.9 * 1.9 = 1.71
    assert card["quality_score"] == 1.71


async def test_list_cards_invalid_status(api_client, store):
    """Invalid status returns 400."""
    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get(
                "/api/work-experience/cards?status=invalid_status",
            )
    assert resp.status_code == 400
    assert "Invalid status" in resp.json()["detail"]


# =============================================================================
# GET /api/work-experience/cards/{card_id}
# =============================================================================


async def test_get_card(api_client, store):
    """Get single card by ID."""
    card = make_card(store, status=WorkExperienceStatus.APPROVED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get(
                f"/api/work-experience/cards/{card.experience_id}",
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "Test Card"
    assert "quality_score" in data


async def test_get_card_not_found(api_client, store):
    """Get non-existent card returns 404."""
    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get(
                "/api/work-experience/cards/00000000-0000-0000-0000-000000000000",
            )
    assert resp.status_code == 404


async def test_get_card_invalid_uuid(api_client, store):
    """Get with invalid UUID returns 400."""
    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards/not-a-uuid")
    assert resp.status_code == 400
    assert "Invalid UUID" in resp.json()["detail"]


# =============================================================================
# POST /api/work-experience/cards/{card_id}/approve
# =============================================================================


async def test_approve_candidate(api_client, store):
    """Approve a candidate card."""
    card = make_card(store, status=WorkExperienceStatus.CANDIDATE)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/approve",
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["new_status"] == "approved"

    # Verify in store
    updated = store.get(card.experience_id)
    assert updated.status == WorkExperienceStatus.APPROVED


async def test_approve_already_approved(api_client, store):
    """Approve an already-approved card returns 409."""
    card = make_card(store, status=WorkExperienceStatus.APPROVED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/approve",
            )
    assert resp.status_code == 409


# =============================================================================
# POST /api/work-experience/cards/{card_id}/reject
# =============================================================================


async def test_reject_approved(api_client, store):
    """Reject an approved card."""
    card = make_card(store, status=WorkExperienceStatus.APPROVED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/reject",
            )

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "rejected"

    updated = store.get(card.experience_id)
    assert updated.status == WorkExperienceStatus.REJECTED


async def test_reject_candidate(api_client, store):
    """Reject a candidate card."""
    card = make_card(store, status=WorkExperienceStatus.CANDIDATE)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/reject",
            )

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "rejected"


# =============================================================================
# POST /api/work-experience/cards/{card_id}/archive
# =============================================================================


async def test_archive_approved(api_client, store):
    """Archive an approved card."""
    card = make_card(store, status=WorkExperienceStatus.APPROVED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/archive",
            )

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "archived"


async def test_archive_rejected(api_client, store):
    """Archive a rejected card."""
    card = make_card(store, status=WorkExperienceStatus.REJECTED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/archive",
            )

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "archived"


# =============================================================================
# POST /api/work-experience/cards/{card_id}/reactivate
# =============================================================================


async def test_reactivate_rejected(api_client, store):
    """Reactivate a rejected card back to candidate."""
    card = make_card(store, status=WorkExperienceStatus.REJECTED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/reactivate",
            )

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "candidate"


async def test_reactivate_approved_fails(api_client, store):
    """Cannot reactivate an approved card (409)."""
    card = make_card(store, status=WorkExperienceStatus.APPROVED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                f"/api/work-experience/cards/{card.experience_id}/reactivate",
            )
    assert resp.status_code == 409


# =============================================================================
# PATCH /api/work-experience/cards/{card_id}/status
# =============================================================================


async def test_transition_status_valid(api_client, store):
    """Valid status transition via PATCH works."""
    card = make_card(store, status=WorkExperienceStatus.CANDIDATE)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.patch(
                f"/api/work-experience/cards/{card.experience_id}/status",
                json={"status": "approved"},
            )

    assert resp.status_code == 200
    assert resp.json()["new_status"] == "approved"


async def test_transition_status_invalid(api_client, store):
    """Invalid status transition returns 409."""
    card = make_card(store, status=WorkExperienceStatus.ARCHIVED)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.patch(
                f"/api/work-experience/cards/{card.experience_id}/status",
                json={"status": "approved"},
            )
    assert resp.status_code == 409
    assert "Invalid transition" in resp.json()["detail"]


# =============================================================================
# GET /api/work-experience/cards/{card_id}/quality-score
# =============================================================================


async def test_quality_score_breakdown(api_client, store):
    """Quality score endpoint returns formula breakdown."""
    make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        confidence=0.9,
        hit_count=10,
        effective_count=5,
    )
    # Get the card ID from the store
    cards = store.list_all()
    card_id = cards[0].experience_id

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get(
                f"/api/work-experience/cards/{card_id}/quality-score",
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["confidence"] == 0.9
    assert data["hit_count"] == 10
    assert data["effective_count"] == 5
    # score = 0.9 * (1 + 10/10 + 5/5) = 0.9 * 3.0 = 2.7
    assert data["quality_score"] == 2.7
    assert "formula" in data


# =============================================================================
# GET /api/work-experience/cards/{card_id}/duplicates
# =============================================================================


async def test_find_duplicates(api_client, store):
    """Find duplicates returns similar cards."""
    card1 = make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="CSV Handler",
        keywords=["csv", "file", "pandas"],
        hint="type:csv",
    )
    card2 = make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="CSV Handler 2",
        keywords=["csv", "file", "encoding"],
        hint="type:csv",
    )
    make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="Unrelated",
        keywords=["web", "api"],
        hint="type:web",
    )

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get(
                f"/api/work-experience/cards/{card1.experience_id}/duplicates?threshold=0.3",
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["reference_card_id"] == str(card1.experience_id)
    assert data["count"] == 1
    assert data["duplicates"][0]["title"] == "CSV Handler 2"


# =============================================================================
# POST /api/work-experience/merge
# =============================================================================


async def test_merge_cards(api_client, store):
    """Merge source into target, source gets archived."""
    target = make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="Target Card",
        hit_count=2,
        effective_count=1,
    )
    source = make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="Source Card",
        keywords=["csv"],
        hit_count=3,
        effective_count=2,
    )

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                "/api/work-experience/merge",
                json={
                    "source_id": str(source.experience_id),
                    "target_id": str(target.experience_id),
                },
            )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["archived"] == str(source.experience_id)
    assert data["merged_into"] == str(target.experience_id)

    # Source should be archived
    assert (
        store.get(source.experience_id).status == WorkExperienceStatus.ARCHIVED
    )
    # Target should have merged hit_count
    merged = store.get(target.experience_id)
    assert merged.hit_count == target.hit_count + source.hit_count


async def test_merge_not_found_source(api_client, store):
    """Merge with non-existent source returns 404."""
    target = make_card(store)

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.post(
                "/api/work-experience/merge",
                json={
                    "source_id": "00000000-0000-0000-0000-000000000000",
                    "target_id": str(target.experience_id),
                },
            )
    assert resp.status_code == 404


# =============================================================================
# GET /api/work-experience/stats
# =============================================================================


async def test_stats_empty(api_client, store):
    """Empty store returns zero stats."""
    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cards"] == 0


async def test_stats_populated(api_client, store):
    """Stats returns correct counts and top card."""
    make_card(store, status=WorkExperienceStatus.CANDIDATE, confidence=0.5)
    make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        confidence=0.9,
        hit_count=10,
        effective_count=5,
    )

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/stats")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cards"] == 2
    assert data["by_status"]["candidate"] == 1
    assert data["by_status"]["approved"] == 1
    assert data["total_hits"] == 10
    assert data["total_effective_uses"] == 5
    assert data["top_scoring_card"] is not None
    assert data["top_scoring_card"]["confidence"] == 0.9


# =============================================================================
# GET /api/work-experience/candidates
# =============================================================================


async def test_list_candidates(api_client, store):
    """List only candidate cards."""
    make_card(store, status=WorkExperienceStatus.CANDIDATE, title="Cand 1")
    make_card(store, status=WorkExperienceStatus.APPROVED, title="Appr 1")

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/candidates")

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["cards"][0]["title"] == "Cand 1"
    assert data["cards"][0]["status"] == "candidate"


# =============================================================================
# GET /api/work-experience/top-cards
# =============================================================================


async def test_top_cards_returns_all_non_deprecated_sorted_by_maturity(
    api_client,
    store,
):
    """Top cards returns all non-deprecated cards sorted by maturity score (new maturity model)."""
    from hubos.core.work_experience.schemas import ExperienceLevel

    # Lower quality (NEW level, low confidence)
    make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="Low Quality",
        confidence=0.5,
        experience_level=ExperienceLevel.NEW,
    )
    # Higher quality (MATURE level, high confidence + hits)
    make_card(
        store,
        status=WorkExperienceStatus.APPROVED,
        title="High Quality",
        confidence=0.95,
        hit_count=10,
        experience_level=ExperienceLevel.MATURE,
    )
    # CANDIDATE card — appears in new model (not filtered out by status)
    # MATURE level but lower confidence → ranks between Low and High
    make_card(
        store,
        status=WorkExperienceStatus.CANDIDATE,
        title="Candidate Card",
        confidence=0.7,
        hit_count=3,
        experience_level=ExperienceLevel.MATURE,
    )

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/top-cards?top_k=5")

    assert resp.status_code == 200
    data = resp.json()
    titles = [card["title"] for card in data["cards"]]
    assert "High Quality" in titles
    assert "Low Quality" in titles
    # CANDIDATE appears (new maturity model: all non-deprecated participate)
    assert "Candidate Card" in titles
    # High Quality should be first (highest maturity score due to MATURE level)
    assert data["cards"][0]["title"] == "High Quality"


# =============================================================================
# Governance: approved-only retrieval is at service level, not API level
# =============================================================================


async def test_api_returns_all_statuses_not_just_approved(api_client, store):
    """
    The API returns all statuses (for admin visibility).
    The approved-only filter is enforced at the retriever/execution level.
    """
    make_card(store, status=WorkExperienceStatus.CANDIDATE, title="Candidate")
    make_card(store, status=WorkExperienceStatus.APPROVED, title="Approved")
    make_card(store, status=WorkExperienceStatus.REJECTED, title="Rejected")

    with patch(
        "hubos.app.routers.work_experience.LocalWorkExperienceStore",
        return_value=store,
    ):
        async with api_client as c:
            resp = await c.get("/api/work-experience/cards")

    assert resp.status_code == 200
    data = resp.json()
    statuses = {card["status"] for card in data["cards"]}
    assert "candidate" in statuses
    assert "approved" in statuses
    assert "rejected" in statuses
