# -*- coding: utf-8 -*-
"""Work Experience Layer — Admin REST API.

Provides RESTful API for managing work experience cards:
- List / filter by status, scope, or experience level
- Get individual card details
- Transition card status (approve / reject / archive / reactivate) — legacy
- Transition experience level (promote / demote / mark deprecated) — new maturity model
- Find duplicate cards
- Merge duplicate cards
- View quality and maturity stats

All card data is served from LocalWorkExperienceStore (file-based).

NOTE: The approved-only retrieval rule is replaced by maturity-based retrieval.
The API provides full visibility into all governance states and experience levels
for administrative review.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field

from hubos.core.work_experience import WorkExperienceService
from hubos.core.work_experience.schemas import (
    ExperienceLevel,
    WorkExperience,
    WorkExperienceScope,
    WorkExperienceStatus,
)
from hubos.core.work_experience.store import LocalWorkExperienceStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/work-experience", tags=["work-experience"])

# =============================================================================
# Store / Service factory
# =============================================================================

def _get_service() -> WorkExperienceService:
    """Create a WorkExperienceService backed by LocalWorkExperienceStore."""
    store = LocalWorkExperienceStore()
    return WorkExperienceService(store=store)


# =============================================================================
# Response Models
# =============================================================================

class WorkExperienceCard(BaseModel):
    """Full work experience card with computed governance fields."""

    experience_id: str
    scope: str
    trigger_keywords: list[str]
    trigger_hint: str
    title: str
    what_happened: str
    what_worked: list[str]
    what_failed: list[str]
    guidance: str
    avoidance: str
    # New work guidance fields
    usage_pattern_summary: str = ""
    recommended_tool_order: list[str] = Field(default_factory=list)
    recommended_workflow: list[str] = Field(default_factory=list)
    applicable_task_types: list[str] = Field(default_factory=list)
    success_rate_estimate: float = 0.0
    supersedes_experience_id: Optional[str] = None
    # Metadata
    confidence: float
    source_task_id: str
    source_session_id: str
    source_trace_id: str
    applicability_tags: list[str]
    hit_count: int
    effective_count: int
    last_retrieved_at: Optional[str] = None
    last_used_at: Optional[str] = None
    disabled: bool
    # Legacy governance state
    status: str
    # New maturity model fields
    experience_level: str
    maturity_score: float = 0.0
    quality_score: float = Field(description="Legacy quality score (for compatibility)")
    created_at: str
    updated_at: str

    @classmethod
    def from_card(cls, card: WorkExperience) -> "WorkExperienceCard":
        """Convert a WorkExperience dataclass to a response model."""
        return cls(
            experience_id=str(card.experience_id),
            scope=card.scope.value,
            trigger_keywords=card.trigger_keywords,
            trigger_hint=card.trigger_hint,
            title=card.title,
            what_happened=card.what_happened,
            what_worked=card.what_worked,
            what_failed=card.what_failed,
            guidance=card.guidance,
            avoidance=card.avoidance,
            # New fields
            usage_pattern_summary=card.usage_pattern_summary,
            recommended_tool_order=card.recommended_tool_order,
            recommended_workflow=card.recommended_workflow,
            applicable_task_types=card.applicable_task_types,
            success_rate_estimate=card.success_rate_estimate,
            supersedes_experience_id=(
                str(card.supersedes_experience_id)
                if card.supersedes_experience_id else None
            ),
            # Metadata
            confidence=card.confidence,
            source_task_id=card.source_task_id,
            source_session_id=card.source_session_id,
            source_trace_id=card.source_trace_id,
            applicability_tags=card.applicability_tags,
            hit_count=card.hit_count,
            effective_count=card.effective_count,
            last_retrieved_at=card.last_retrieved_at.isoformat() if card.last_retrieved_at else None,
            last_used_at=card.last_used_at.isoformat() if card.last_used_at else None,
            disabled=card.disabled,
            # Legacy
            status=card.status.value,
            # New maturity model
            experience_level=card.experience_level.value,
            maturity_score=card.maturity_score,
            quality_score=card.confidence * (1.0 + card.hit_count / 10.0 + card.effective_count / 5.0),
            created_at=card.created_at.isoformat(),
            updated_at=card.updated_at.isoformat(),
        )


class CardListResponse(BaseModel):
    """Response for listing work experience cards."""
    cards: list[WorkExperienceCard]
    total: int


class CardSummary(BaseModel):
    """Lightweight card summary for duplicate listings."""
    experience_id: str
    title: str
    scope: str
    status: str
    experience_level: str
    confidence: float
    hit_count: int
    effective_count: int
    maturity_score: float
    quality_score: float
    trigger_hint: str

    @classmethod
    def from_card(cls, card: WorkExperience) -> "CardSummary":
        return cls(
            experience_id=str(card.experience_id),
            title=card.title,
            scope=card.scope.value,
            status=card.status.value,
            experience_level=card.experience_level.value,
            confidence=card.confidence,
            hit_count=card.hit_count,
            effective_count=card.effective_count,
            maturity_score=card.maturity_score,
            quality_score=card.confidence * (1.0 + card.hit_count / 10.0 + card.effective_count / 5.0),
            trigger_hint=card.trigger_hint,
        )


class DuplicateDetectionResponse(BaseModel):
    """Response for duplicate detection."""
    reference_card_id: str
    duplicates: list[CardSummary]
    count: int


class MergeRequest(BaseModel):
    """Request to merge source card into target card."""
    source_id: str = Field(..., description="ID of the card to merge FROM (will be archived)")
    target_id: str = Field(..., description="ID of the card to merge INTO")


class MergeResponse(BaseModel):
    """Response from a merge operation."""
    success: bool
    merged_into: str
    archived: str
    message: str


class StatusTransitionRequest(BaseModel):
    """Request to transition a card's governance status."""
    status: str = Field(..., description="Target status: approved, rejected, archived, candidate")


class StatusTransitionResponse(BaseModel):
    """Response from a status transition."""
    success: bool
    card_id: str
    new_status: str


class LevelTransitionRequest(BaseModel):
    """Request to transition a card's experience level."""
    level: str = Field(..., description="Target level: new, observed, mature, deprecated")


class LevelTransitionResponse(BaseModel):
    """Response from an experience level transition."""
    success: bool
    card_id: str
    new_level: str


class WorkExperienceStats(BaseModel):
    """Summary statistics for work experience cards."""
    total_cards: int
    by_status: dict[str, int]
    by_level: dict[str, int]
    by_scope: dict[str, int]
    total_hits: int
    total_effective_uses: int
    avg_confidence: float
    avg_quality_score: float
    avg_maturity_score: float
    top_scoring_card: Optional[CardSummary] = None


# =============================================================================
# Helpers
# =============================================================================

def _uuid(s: str) -> UUID:
    """Parse a string to UUID or raise HTTP 400."""
    try:
        return UUID(s)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid UUID: {s}")


def _status(s: str) -> WorkExperienceStatus:
    """Parse a string to WorkExperienceStatus or raise HTTP 400."""
    try:
        return WorkExperienceStatus(s.lower())
    except ValueError:
        valid = [st.value for st in WorkExperienceStatus]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{s}'. Must be one of: {valid}",
        )


def _level(s: str) -> ExperienceLevel:
    """Parse a string to ExperienceLevel or raise HTTP 400."""
    try:
        return ExperienceLevel(s.lower())
    except ValueError:
        valid = [lv.value for lv in ExperienceLevel]
        raise HTTPException(
            status_code=400,
            detail=f"Invalid experience level '{s}'. Must be one of: {valid}",
        )


# =============================================================================
# Card Endpoints
# =============================================================================

@router.get(
    "/cards",
    summary="List work experience cards",
    response_model=CardListResponse,
)
async def list_cards(
    status: Optional[str] = Query(
        None,
        description="Filter by status: candidate, approved, rejected, archived",
    ),
    level: Optional[str] = Query(
        None,
        description="Filter by experience level: new, observed, mature, deprecated",
    ),
    scope: Optional[str] = Query(
        None,
        description="Filter by scope: global, user, project, session",
    ),
    include_disabled: bool = Query(False, description="Include disabled cards"),
    limit: int = Query(50, ge=1, le=200, description="Max cards to return"),
    offset: int = Query(0, ge=0, description="Skip first N cards"),
) -> CardListResponse:
    """
    List work experience cards with optional filters.

    Returns governance fields (status, level, maturity_score, hit_count, effective_count)
    for each card to support administrative review.
    """
    service = _get_service()

    # Determine status filter
    filter_status: Optional[WorkExperienceStatus] = None
    if status:
        filter_status = _status(status)

    # Determine level filter
    filter_level: Optional[ExperienceLevel] = None
    if level:
        filter_level = _level(level)

    # Determine scope filter
    filter_scope: Optional[WorkExperienceScope] = None
    if scope:
        try:
            filter_scope = WorkExperienceScope(scope.lower())
        except ValueError:
            valid = [s.value for s in WorkExperienceScope]
            raise HTTPException(
                status_code=400,
                detail=f"Invalid scope '{scope}'. Must be one of: {valid}",
            )

    # Primary filter: level (new maturity model), then additionally by status
    if filter_level:
        all_cards = service.list_by_level(filter_level, include_disabled=include_disabled)
    else:
        all_cards = service._store.list_all(include_disabled=include_disabled)

    # Secondary filter: status (when also specified, applied on top of level)
    if filter_status:
        all_cards = [c for c in all_cards if c.status == filter_status]

    # Scope filter
    if filter_scope:
        all_cards = [c for c in all_cards if c.scope == filter_scope]

    total = len(all_cards)
    paginated = all_cards[offset : offset + limit]

    cards = [WorkExperienceCard.from_card(c) for c in paginated]

    return CardListResponse(cards=cards, total=total)


@router.get(
    "/cards/{card_id}",
    summary="Get a single work experience card",
    response_model=WorkExperienceCard,
)
async def get_card(
    card_id: str = Path(..., description="Card UUID"),
) -> WorkExperienceCard:
    """Get a single work experience card by ID."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")
    return WorkExperienceCard.from_card(card)


@router.get(
    "/cards/{card_id}/quality-score",
    summary="Get quality score for a card",
)
async def get_quality_score(
    card_id: str = Path(..., description="Card UUID"),
) -> dict[str, Any]:
    """Compute and return the quality score breakdown for a card."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    score = service.quality_score(card)
    return {
        "experience_id": str(card.experience_id),
        "confidence": card.confidence,
        "hit_count": card.hit_count,
        "effective_count": card.effective_count,
        "quality_score": score,
        "formula": "confidence * (1 + hit_count/10 + effective_count/5)",
    }


@router.get(
    "/cards/{card_id}/maturity",
    summary="Get maturity info for a card",
)
async def get_maturity(
    card_id: str = Path(..., description="Card UUID"),
) -> dict[str, Any]:
    """Get maturity score breakdown for a card."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    return {
        "experience_id": str(card.experience_id),
        "experience_level": card.experience_level.value,
        "maturity_score": card.maturity_score,
        "success_rate_estimate": card.success_rate_estimate,
        "effective_ratio": card.effective_ratio(),
        "hit_count": card.hit_count,
        "effective_count": card.effective_count,
        "level_weight": card.experience_level.retrieval_weight(),
    }


@router.patch(
    "/cards/{card_id}/status",
    summary="Transition card status",
    response_model=StatusTransitionResponse,
)
async def transition_status(
    card_id: str = Path(..., description="Card UUID"),
    body: StatusTransitionRequest = ...,
) -> StatusTransitionResponse:
    """
    Transition a card to a new governance status (legacy model).

    Valid transitions:
    - candidate → approved, rejected, archived
    - approved → rejected, archived
    - rejected → candidate (re-review), archived
    - archived → (terminal, no transitions)
    """
    service = _get_service()
    target_status = _status(body.status)

    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    ok = service._store.update_status(card.experience_id, target_status)
    if not ok:
        current = card.status.value
        raise HTTPException(
            status_code=409,
            detail=f"Invalid transition from '{current}' to '{target_status.value}'",
        )

    return StatusTransitionResponse(
        success=True,
        card_id=card_id,
        new_status=target_status.value,
    )


@router.patch(
    "/cards/{card_id}/level",
    summary="Transition experience level",
    response_model=LevelTransitionResponse,
)
async def transition_level(
    card_id: str = Path(..., description="Card UUID"),
    body: LevelTransitionRequest = ...,
) -> LevelTransitionResponse:
    """
    Transition a card's experience level (new maturity model).

    Valid transitions:
    - new → observed, mature, deprecated
    - observed → new (regress), mature (promote), deprecated
    - mature → observed (regress), deprecated
    - deprecated → (terminal, no transitions)
    """
    service = _get_service()
    target_level = _level(body.level)

    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    ok = service._store.update_experience_level(card.experience_id, target_level)
    if not ok:
        current = card.experience_level.value
        raise HTTPException(
            status_code=409,
            detail=f"Invalid level transition from '{current}' to '{target_level.value}'",
        )

    return LevelTransitionResponse(
        success=True,
        card_id=card_id,
        new_level=target_level.value,
    )


# ---- Legacy status transition shortcuts ----

@router.post(
    "/cards/{card_id}/approve",
    summary="Approve a card",
    response_model=StatusTransitionResponse,
)
async def approve_card(
    card_id: str = Path(..., description="Card UUID"),
) -> StatusTransitionResponse:
    """Shortcut: approve a candidate card. Also promotes level to observed."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    ok = service.approve(card.experience_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve card in status '{card.status.value}'",
        )

    return StatusTransitionResponse(success=True, card_id=card_id, new_status="approved")


@router.post(
    "/cards/{card_id}/reject",
    summary="Reject a card",
    response_model=StatusTransitionResponse,
)
async def reject_card(
    card_id: str = Path(..., description="Card UUID"),
) -> StatusTransitionResponse:
    """Shortcut: reject a candidate or approved card. Marks as deprecated."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    ok = service.reject(card.experience_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reject card in status '{card.status.value}'",
        )

    return StatusTransitionResponse(success=True, card_id=card_id, new_status="rejected")


@router.post(
    "/cards/{card_id}/archive",
    summary="Archive a card",
    response_model=StatusTransitionResponse,
)
async def archive_card(
    card_id: str = Path(..., description="Card UUID"),
) -> StatusTransitionResponse:
    """Archive a card (any non-terminal status)."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    ok = service.archive(card.experience_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot archive card in status '{card.status.value}'",
        )

    return StatusTransitionResponse(success=True, card_id=card_id, new_status="archived")


@router.post(
    "/cards/{card_id}/reactivate",
    summary="Reactivate a rejected card for re-review",
    response_model=StatusTransitionResponse,
)
async def reactivate_card(
    card_id: str = Path(..., description="Card UUID"),
) -> StatusTransitionResponse:
    """Reactivate a rejected card back to candidate status."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    ok = service.reactivate(card.experience_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot reactivate card in status '{card.status.value}'",
        )

    return StatusTransitionResponse(success=True, card_id=card_id, new_status="candidate")


# ---- New maturity level shortcuts ----

@router.post(
    "/cards/{card_id}/promote",
    summary="Promote a card's experience level",
    response_model=LevelTransitionResponse,
)
async def promote_card(
    card_id: str = Path(..., description="Card UUID"),
) -> LevelTransitionResponse:
    """Promote: new → observed → mature. Fails if already at mature."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    if card.experience_level == ExperienceLevel.NEW:
        ok = service.promote_to_observed(card.experience_id)
        new_level = ExperienceLevel.OBSERVED
    elif card.experience_level == ExperienceLevel.OBSERVED:
        ok = service.promote_to_mature(card.experience_id)
        new_level = ExperienceLevel.MATURE
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot promote card at level '{card.experience_level.value}'",
        )

    if not ok:
        raise HTTPException(status_code=409, detail="Promotion failed")

    return LevelTransitionResponse(success=True, card_id=card_id, new_level=new_level.value)


@router.post(
    "/cards/{card_id}/demote",
    summary="Demote a card's experience level",
    response_model=LevelTransitionResponse,
)
async def demote_card(
    card_id: str = Path(..., description="Card UUID"),
) -> LevelTransitionResponse:
    """Demote: mature → observed, observed → new. Fails if already at new."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    if card.experience_level == ExperienceLevel.MATURE:
        ok = service.demote_to_observed(card.experience_id)
        new_level = ExperienceLevel.OBSERVED
    elif card.experience_level == ExperienceLevel.OBSERVED:
        ok = service.demote_to_new(card.experience_id)
        new_level = ExperienceLevel.NEW
    else:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot demote card at level '{card.experience_level.value}'",
        )

    if not ok:
        raise HTTPException(status_code=409, detail="Demotion failed")

    return LevelTransitionResponse(success=True, card_id=card_id, new_level=new_level.value)


@router.post(
    "/cards/{card_id}/deprecate",
    summary="Mark a card as deprecated",
    response_model=LevelTransitionResponse,
)
async def deprecate_card(
    card_id: str = Path(..., description="Card UUID"),
) -> LevelTransitionResponse:
    """Mark a card as deprecated (excluded from retrieval)."""
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    ok = service.mark_deprecated(card.experience_id)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot deprecate card at level '{card.experience_level.value}'",
        )

    return LevelTransitionResponse(success=True, card_id=card_id, new_level="deprecated")


# =============================================================================
# Duplicate Detection & Merge
# =============================================================================

@router.get(
    "/cards/{card_id}/duplicates",
    summary="Find duplicate cards",
    response_model=DuplicateDetectionResponse,
)
async def find_duplicates(
    card_id: str = Path(..., description="Reference card UUID"),
    threshold: float = Query(
        0.5,
        ge=0.0,
        le=1.0,
        description="Jaccard similarity threshold (0.0–1.0)",
    ),
) -> DuplicateDetectionResponse:
    """
    Find potential duplicate cards for a given reference card.

    Duplicates are identified by:
    - Same trigger_hint prefix (first 10 chars)
    - Jaccard keyword similarity >= threshold
    """
    service = _get_service()
    card = service._store.get(_uuid(card_id))
    if not card:
        raise HTTPException(status_code=404, detail=f"Card not found: {card_id}")

    dups = service.find_duplicates(card, similarity_threshold=threshold)
    summaries = [CardSummary.from_card(d) for d in dups]

    return DuplicateDetectionResponse(
        reference_card_id=card_id,
        duplicates=summaries,
        count=len(summaries),
    )


@router.post(
    "/merge",
    summary="Merge two cards",
    response_model=MergeResponse,
)
async def merge_cards(
    body: MergeRequest = ...,
) -> MergeResponse:
    """
    Merge source card into target card.

    The source card is archived after merging.
    Fields are combined: union of what_worked, what_failed, keywords, tags;
    longer guidance/avoidance wins; hit_count and effective_count are summed.
    """
    service = _get_service()
    source_uuid = _uuid(body.source_id)
    target_uuid = _uuid(body.target_id)

    source = service._store.get(source_uuid)
    target = service._store.get(target_uuid)

    if not source:
        raise HTTPException(status_code=404, detail=f"Source card not found: {body.source_id}")
    if not target:
        raise HTTPException(status_code=404, detail=f"Target card not found: {body.target_id}")

    ok = service.merge_into(source_uuid, target_uuid)
    if not ok:
        raise HTTPException(
            status_code=409,
            detail="Merge failed — check that source is not already archived",
        )

    return MergeResponse(
        success=True,
        merged_into=body.target_id,
        archived=body.source_id,
        message=f"Card '{source.title}' merged into '{target.title}'",
    )


# =============================================================================
# Quality & Maturity Stats
# =============================================================================

@router.get(
    "/stats",
    summary="Get work experience statistics",
    response_model=WorkExperienceStats,
)
async def get_stats(
    status: Optional[str] = Query(
        None,
        description="Filter stats by status",
    ),
    level: Optional[str] = Query(
        None,
        description="Filter stats by experience level",
    ),
) -> WorkExperienceStats:
    """
    Get summary statistics for work experience cards.

    Includes counts by status/level/scope, total usage metrics, and top-scoring card.
    """
    service = _get_service()

    if status:
        filter_status = _status(status)
        all_cards_by_status = service.list_by_status(filter_status)
    else:
        all_cards_by_status = None

    if level:
        filter_level = _level(level)
        all_cards = service.list_by_level(filter_level)
    else:
        all_cards = service._store.list_all()

    # Additionally filter by status if both are specified
    if all_cards_by_status is not None:
        if level:
            # Both filters: intersect
            card_ids = {c.experience_id for c in all_cards_by_status}
            all_cards = [c for c in all_cards if c.experience_id in card_ids]
        else:
            all_cards = all_cards_by_status

    total = len(all_cards)
    if total == 0:
        return WorkExperienceStats(
            total_cards=0,
            by_status={},
            by_level={},
            by_scope={},
            total_hits=0,
            total_effective_uses=0,
            avg_confidence=0.0,
            avg_quality_score=0.0,
            avg_maturity_score=0.0,
            top_scoring_card=None,
        )

    # Count by status
    by_status: dict[str, int] = {}
    for c in all_cards:
        by_status[c.status.value] = by_status.get(c.status.value, 0) + 1

    # Count by level
    by_level: dict[str, int] = {}
    for c in all_cards:
        by_level[c.experience_level.value] = by_level.get(c.experience_level.value, 0) + 1

    # Count by scope
    by_scope: dict[str, int] = {}
    for c in all_cards:
        by_scope[c.scope.value] = by_scope.get(c.scope.value, 0) + 1

    total_hits = sum(c.hit_count for c in all_cards)
    total_eff = sum(c.effective_count for c in all_cards)
    avg_conf = sum(c.confidence for c in all_cards) / total
    avg_maturity = sum(c.maturity_score for c in all_cards) / total

    scores = [service.quality_score(c) for c in all_cards]
    avg_score = sum(scores) / total

    # Top scoring card
    top_card = max(all_cards, key=lambda c: service.quality_score(c))
    top_summary = CardSummary.from_card(top_card)

    return WorkExperienceStats(
        total_cards=total,
        by_status=by_status,
        by_level=by_level,
        by_scope=by_scope,
        total_hits=total_hits,
        total_effective_uses=total_eff,
        avg_confidence=round(avg_conf, 4),
        avg_quality_score=round(avg_score, 4),
        avg_maturity_score=round(avg_maturity, 2),
        top_scoring_card=top_summary,
    )


@router.get(
    "/candidates",
    summary="List all candidate (unreviewed) cards",
    response_model=CardListResponse,
)
async def list_candidates(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CardListResponse:
    """List all cards in CANDIDATE status awaiting review."""
    service = _get_service()
    candidates = service.list_candidates()

    total = len(candidates)
    paginated = candidates[offset : offset + limit]
    cards = [WorkExperienceCard.from_card(c) for c in paginated]

    return CardListResponse(cards=cards, total=total)


@router.get(
    "/top-cards",
    summary="List top cards by quality score",
    response_model=CardListResponse,
)
async def list_top_cards(
    status: Optional[str] = Query(
        None,
        description="Filter by status",
    ),
    level: Optional[str] = Query(
        None,
        description="Filter by experience level",
    ),
    top_k: int = Query(10, ge=1, le=50, description="Number of cards to return"),
    scope: Optional[str] = Query(None, description="Filter by scope"),
) -> CardListResponse:
    """List the top-k highest quality cards, sorted by quality score."""
    service = _get_service()

    filter_status: Optional[WorkExperienceStatus] = None
    if status:
        filter_status = _status(status)

    filter_level: Optional[ExperienceLevel] = None
    if level:
        filter_level = _level(level)

    filter_scope: Optional[WorkExperienceScope] = None
    if scope:
        try:
            filter_scope = WorkExperienceScope(scope.lower())
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid scope '{scope}'")

    # Get cards filtered by level (primary) and/or status
    if filter_level:
        cards = service.list_by_level(filter_level)
    else:
        cards = service._store.list_all()

    if filter_status:
        card_ids = {c.experience_id for c in service.list_by_status(filter_status)}
        cards = [c for c in cards if c.experience_id in card_ids]

    # Scope filter
    if filter_scope:
        cards = [c for c in cards if c.scope == filter_scope]

    # Sort by quality and take top-k
    top = service.top_cards(cards, top_k=top_k)

    return CardListResponse(
        cards=[WorkExperienceCard.from_card(c) for c in top],
        total=len(top),
    )


@router.get(
    "/by-level",
    summary="List cards grouped by experience level",
)
async def list_by_level() -> dict[str, int]:
    """Get card counts grouped by experience level."""
    service = _get_service()
    return {
        "new": len(service.list_new()),
        "observed": len(service.list_observed()),
        "mature": len(service.list_mature()),
        "deprecated": len(service.list_deprecated()),
    }
