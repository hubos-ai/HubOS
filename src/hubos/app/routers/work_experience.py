# -*- coding: utf-8 -*-
"""Work Experience admin API backed by v4 WorkflowCards.

The runtime now learns and retrieves v4 cards. This router keeps the legacy
response shape expected by the console, but reads/writes the v4 CardStore so the
UI, API, and prompt injection all look at the same source of truth.
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query
from pydantic import BaseModel, Field

from hubos.core.work_experience.schemas_v4 import WorkflowCard
from hubos.core.work_experience.store_v4 import CardStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/work-experience", tags=["work-experience"])

VALID_STATUSES = {"candidate", "approved", "rejected", "archived"}
VALID_LEVELS = {"new", "observed", "mature", "deprecated"}
VALID_SCOPES = {"global", "user", "project", "session"}


# =============================================================================
# Response models kept compatible with console/src/api/modules/workExperience.ts
# =============================================================================


class WorkExperienceCard(BaseModel):
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
    usage_pattern_summary: str = ""
    recommended_tool_order: list[str] = Field(default_factory=list)
    recommended_workflow: list[str] = Field(default_factory=list)
    applicable_task_types: list[str] = Field(default_factory=list)
    success_rate_estimate: float = 0.0
    supersedes_experience_id: Optional[str] = None
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
    status: str
    experience_level: str
    maturity_score: float = 0.0
    quality_score: float
    created_at: str
    updated_at: str


class CardListResponse(BaseModel):
    cards: list[WorkExperienceCard]
    total: int


class CardSummary(BaseModel):
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


class DuplicateDetectionResponse(BaseModel):
    reference_card_id: str
    duplicates: list[CardSummary]
    count: int


class MergeRequest(BaseModel):
    source_id: str
    target_id: str


class MergeResponse(BaseModel):
    success: bool
    merged_into: str
    archived: str
    message: str


class StatusTransitionRequest(BaseModel):
    status: str = Field(
        ...,
        description="candidate, approved, rejected, archived",
    )


class StatusTransitionResponse(BaseModel):
    success: bool
    card_id: str
    new_status: str


class LevelTransitionRequest(BaseModel):
    level: str = Field(..., description="new, observed, mature, deprecated")


class LevelTransitionResponse(BaseModel):
    success: bool
    card_id: str
    new_level: str


class WorkExperienceStats(BaseModel):
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


def _get_store() -> CardStore:
    return CardStore()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_status(status: str) -> str:
    value = status.lower()
    if value not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{status}'. Must be one of: {sorted(VALID_STATUSES)}",
        )
    return value


def _validate_level(level: str) -> str:
    value = level.lower()
    if value not in VALID_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid experience level '{level}'. Must be one of: {sorted(VALID_LEVELS)}",
        )
    return value


def _validate_scope(scope: str) -> str:
    value = scope.lower()
    if value not in VALID_SCOPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scope '{scope}'. Must be one of: {sorted(VALID_SCOPES)}",
        )
    return value


def _card_or_404(store: CardStore, card_id: str) -> WorkflowCard:
    card = store.get(card_id)
    if not card:
        raise HTTPException(
            status_code=404,
            detail=f"Card not found: {card_id}",
        )
    return card


def _keywords(card: WorkflowCard) -> list[str]:
    tokens: list[str] = [card.card_id, card.task_type]
    tokens.extend(card.tools.keys())
    tokens.extend(card.workflow[:3])
    tokens.extend(card.success_patterns[:3])
    seen: set[str] = set()
    result: list[str] = []
    for raw in tokens:
        for part in str(raw).replace("/", " ").replace("_", " ").split():
            word = part.strip("，。,.:-()[]{} ").lower()
            if len(word) < 2 or word in seen:
                continue
            seen.add(word)
            result.append(word)
            if len(result) >= 24:
                return result
    return result


def _maturity_score(card: WorkflowCard) -> float:
    base_by_level = {
        "new": 10.0,
        "observed": 40.0,
        "mature": 70.0,
        "deprecated": 0.0,
    }
    base = base_by_level.get(card.experience_level, 40.0)
    content_bonus = min(
        20.0,
        len(card.workflow) * 2.0
        + len(card.tools) * 1.5
        + len(card.success_patterns) * 1.0,
    )
    execution_bonus = min(10.0, card.executions * 1.0)
    if card.disabled or card.status in {"rejected", "archived"}:
        return min(base, 20.0)
    return round(min(100.0, base + content_bonus + execution_bonus), 2)


def _success_rate(card: WorkflowCard) -> float:
    if card.experience_level == "deprecated" or card.status in {
        "rejected",
        "archived",
    }:
        return 0.1
    signal_count = (
        len(card.success_patterns) + len(card.workflow) + len(card.tools)
    )
    return round(
        min(0.95, 0.55 + signal_count * 0.03 + card.executions * 0.02),
        2,
    )


def _quality_score(card: WorkflowCard) -> float:
    confidence = _confidence(card)
    effective = _effective_count(card)
    return round(
        confidence * (1.0 + card.executions / 10.0 + effective / 5.0),
        4,
    )


def _confidence(card: WorkflowCard) -> float:
    if card.status in {"rejected", "archived"} or card.disabled:
        return 0.35
    if card.experience_level == "deprecated":
        return 0.25
    completeness = (
        len(card.workflow) + len(card.tools) + len(card.success_patterns)
    )
    return round(min(0.95, 0.65 + completeness * 0.025), 2)


def _effective_count(card: WorkflowCard) -> int:
    # V4 records executions rather than separate hit/effective counters. For UI
    # compatibility, treat successful executions as effective uses.
    if (
        card.status in {"rejected", "archived"}
        or card.experience_level == "deprecated"
    ):
        return 0
    return max(0, card.executions)


def _to_response(card: WorkflowCard) -> WorkExperienceCard:
    source_session = card.source_sessions[-1] if card.source_sessions else ""
    tools = list(card.tools.keys())
    return WorkExperienceCard(
        experience_id=card.card_id,
        scope="global",
        trigger_keywords=_keywords(card),
        trigger_hint=f"task_type:{card.task_type}",
        title=card.task_type or card.card_id,
        what_happened=card.description,
        what_worked=card.success_patterns,
        what_failed=card.pitfalls,
        guidance=card.formatted_for_injection(),
        avoidance="\n".join(card.pitfalls),
        usage_pattern_summary=card.description,
        recommended_tool_order=tools,
        recommended_workflow=card.workflow,
        applicable_task_types=[card.task_type] if card.task_type else [],
        success_rate_estimate=_success_rate(card),
        supersedes_experience_id=None,
        confidence=_confidence(card),
        source_task_id="work_experience_v4",
        source_session_id=source_session,
        source_trace_id="work_experience_v4",
        applicability_tags=_keywords(card)[:12],
        hit_count=card.executions,
        effective_count=_effective_count(card),
        last_retrieved_at=None,
        last_used_at=card.last_executed_at or None,
        disabled=card.disabled,
        status=card.status,
        experience_level=card.experience_level,
        maturity_score=_maturity_score(card),
        quality_score=_quality_score(card),
        created_at=card.created_at,
        updated_at=card.updated_at,
    )


def _to_summary(card: WorkflowCard) -> CardSummary:
    return CardSummary(
        experience_id=card.card_id,
        title=card.task_type or card.card_id,
        scope="global",
        status=card.status,
        experience_level=card.experience_level,
        confidence=_confidence(card),
        hit_count=card.executions,
        effective_count=_effective_count(card),
        maturity_score=_maturity_score(card),
        quality_score=_quality_score(card),
        trigger_hint=f"task_type:{card.task_type}",
    )


def _filtered_cards(
    *,
    status: Optional[str] = None,
    level: Optional[str] = None,
    scope: Optional[str] = None,
    include_disabled: bool = False,
) -> list[WorkflowCard]:
    store = _get_store()
    cards = store.list_all()
    if not include_disabled:
        cards = [c for c in cards if not c.disabled]
    if status:
        filter_status = _validate_status(status)
        cards = [c for c in cards if c.status == filter_status]
    if level:
        filter_level = _validate_level(level)
        cards = [c for c in cards if c.experience_level == filter_level]
    if scope:
        # V4 method cards are global by design, but keep validation for UI filters.
        filter_scope = _validate_scope(scope)
        cards = cards if filter_scope == "global" else []
    return cards


def _save_card(store: CardStore, card: WorkflowCard) -> None:
    card.updated_at = _utcnow()
    store.save(card)


def _merge_unique(left: list[str], right: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for item in [*left, *right]:
        value = str(item).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


# =============================================================================
# Card endpoints
# =============================================================================


@router.get(
    "/cards",
    response_model=CardListResponse,
    summary="List v4 work experience cards",
)
async def list_cards(
    status: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    include_disabled: bool = Query(False),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CardListResponse:
    cards = _filtered_cards(
        status=status,
        level=level,
        scope=scope,
        include_disabled=include_disabled,
    )
    cards.sort(
        key=lambda c: (_maturity_score(c), _quality_score(c)),
        reverse=True,
    )
    total = len(cards)
    return CardListResponse(
        cards=[_to_response(c) for c in cards[offset : offset + limit]],
        total=total,
    )


@router.get(
    "/cards/{card_id}",
    response_model=WorkExperienceCard,
    summary="Get a v4 card",
)
async def get_card(
    card_id: str = ApiPath(..., description="V4 card slug"),
) -> WorkExperienceCard:
    return _to_response(_card_or_404(_get_store(), card_id))


@router.get("/cards/{card_id}/quality-score", summary="Get quality score")
async def get_quality_score(card_id: str = ApiPath(...)) -> dict[str, Any]:
    card = _card_or_404(_get_store(), card_id)
    return {
        "experience_id": card.card_id,
        "confidence": _confidence(card),
        "hit_count": card.executions,
        "effective_count": _effective_count(card),
        "quality_score": _quality_score(card),
        "formula": "confidence * (1 + executions/10 + effective_uses/5)",
    }


@router.get("/cards/{card_id}/maturity", summary="Get maturity details")
async def get_maturity(card_id: str = ApiPath(...)) -> dict[str, Any]:
    card = _card_or_404(_get_store(), card_id)
    effective = _effective_count(card)
    ratio = 0.0 if card.executions == 0 else effective / card.executions
    level_weight = {
        "new": 0.4,
        "observed": 0.7,
        "mature": 1.0,
        "deprecated": 0.0,
    }.get(
        card.experience_level,
        0.7,
    )
    return {
        "experience_id": card.card_id,
        "experience_level": card.experience_level,
        "maturity_score": _maturity_score(card),
        "success_rate_estimate": _success_rate(card),
        "effective_ratio": round(ratio, 4),
        "hit_count": card.executions,
        "effective_count": effective,
        "level_weight": level_weight,
    }


@router.patch(
    "/cards/{card_id}/status",
    response_model=StatusTransitionResponse,
)
async def transition_status(
    card_id: str = ApiPath(...),
    body: StatusTransitionRequest = ...,
) -> StatusTransitionResponse:
    store = _get_store()
    card = _card_or_404(store, card_id)
    card.status = _validate_status(body.status)
    card.disabled = card.status in {"rejected", "archived"}
    _save_card(store, card)
    return StatusTransitionResponse(
        success=True,
        card_id=card_id,
        new_status=card.status,
    )


@router.patch("/cards/{card_id}/level", response_model=LevelTransitionResponse)
async def transition_level(
    card_id: str = ApiPath(...),
    body: LevelTransitionRequest = ...,
) -> LevelTransitionResponse:
    store = _get_store()
    card = _card_or_404(store, card_id)
    card.experience_level = _validate_level(body.level)
    card.disabled = card.experience_level == "deprecated" or card.disabled
    _save_card(store, card)
    return LevelTransitionResponse(
        success=True,
        card_id=card_id,
        new_level=card.experience_level,
    )


@router.post(
    "/cards/{card_id}/approve",
    response_model=StatusTransitionResponse,
)
async def approve_card(
    card_id: str = ApiPath(...),
) -> StatusTransitionResponse:
    return await transition_status(
        card_id,
        StatusTransitionRequest(status="approved"),
    )


@router.post(
    "/cards/{card_id}/reject",
    response_model=StatusTransitionResponse,
)
async def reject_card(card_id: str = ApiPath(...)) -> StatusTransitionResponse:
    return await transition_status(
        card_id,
        StatusTransitionRequest(status="rejected"),
    )


@router.post(
    "/cards/{card_id}/archive",
    response_model=StatusTransitionResponse,
)
async def archive_card(
    card_id: str = ApiPath(...),
) -> StatusTransitionResponse:
    return await transition_status(
        card_id,
        StatusTransitionRequest(status="archived"),
    )


@router.post(
    "/cards/{card_id}/reactivate",
    response_model=StatusTransitionResponse,
)
async def reactivate_card(
    card_id: str = ApiPath(...),
) -> StatusTransitionResponse:
    store = _get_store()
    card = _card_or_404(store, card_id)
    card.status = "candidate"
    card.disabled = False
    if card.experience_level == "deprecated":
        card.experience_level = "observed"
    _save_card(store, card)
    return StatusTransitionResponse(
        success=True,
        card_id=card_id,
        new_status="candidate",
    )


@router.post(
    "/cards/{card_id}/promote",
    response_model=LevelTransitionResponse,
)
async def promote_card(card_id: str = ApiPath(...)) -> LevelTransitionResponse:
    store = _get_store()
    card = _card_or_404(store, card_id)
    order = ["new", "observed", "mature"]
    if card.experience_level == "deprecated":
        raise HTTPException(
            status_code=409,
            detail="Cannot promote deprecated card",
        )
    idx = (
        order.index(card.experience_level)
        if card.experience_level in order
        else 1
    )
    card.experience_level = order[min(idx + 1, len(order) - 1)]
    card.disabled = False
    _save_card(store, card)
    return LevelTransitionResponse(
        success=True,
        card_id=card_id,
        new_level=card.experience_level,
    )


@router.post("/cards/{card_id}/demote", response_model=LevelTransitionResponse)
async def demote_card(card_id: str = ApiPath(...)) -> LevelTransitionResponse:
    store = _get_store()
    card = _card_or_404(store, card_id)
    order = ["new", "observed", "mature"]
    if card.experience_level == "deprecated":
        raise HTTPException(
            status_code=409,
            detail="Cannot demote deprecated card",
        )
    idx = (
        order.index(card.experience_level)
        if card.experience_level in order
        else 1
    )
    card.experience_level = order[max(idx - 1, 0)]
    _save_card(store, card)
    return LevelTransitionResponse(
        success=True,
        card_id=card_id,
        new_level=card.experience_level,
    )


@router.post(
    "/cards/{card_id}/deprecate",
    response_model=LevelTransitionResponse,
)
async def deprecate_card(
    card_id: str = ApiPath(...),
) -> LevelTransitionResponse:
    return await transition_level(
        card_id,
        LevelTransitionRequest(level="deprecated"),
    )


@router.get(
    "/cards/{card_id}/duplicates",
    response_model=DuplicateDetectionResponse,
)
async def find_duplicates(
    card_id: str = ApiPath(...),
    threshold: float = Query(0.5, ge=0.0, le=1.0),
) -> DuplicateDetectionResponse:
    store = _get_store()
    reference = _card_or_404(store, card_id)
    ref_words = set(_keywords(reference))
    duplicates: list[WorkflowCard] = []
    for card in store.list_all():
        if card.card_id == reference.card_id:
            continue
        words = set(_keywords(card))
        union = ref_words | words
        similarity = 0.0 if not union else len(ref_words & words) / len(union)
        if similarity >= threshold:
            duplicates.append(card)
    return DuplicateDetectionResponse(
        reference_card_id=card_id,
        duplicates=[_to_summary(c) for c in duplicates],
        count=len(duplicates),
    )


@router.post("/merge", response_model=MergeResponse)
async def merge_cards(body: MergeRequest = ...) -> MergeResponse:
    store = _get_store()
    source = _card_or_404(store, body.source_id)
    target = _card_or_404(store, body.target_id)
    if source.card_id == target.card_id:
        raise HTTPException(
            status_code=400,
            detail="source_id and target_id must differ",
        )

    target.workflow = _merge_unique(target.workflow, source.workflow)
    target.pitfalls = _merge_unique(target.pitfalls, source.pitfalls)
    target.success_patterns = _merge_unique(
        target.success_patterns,
        source.success_patterns,
    )
    target.tools = {**source.tools, **target.tools}
    target.executions += source.executions
    target.source_sessions = _merge_unique(
        target.source_sessions,
        source.source_sessions,
    )[-20:]
    _save_card(store, target)

    source.status = "archived"
    source.disabled = True
    _save_card(store, source)

    return MergeResponse(
        success=True,
        merged_into=target.card_id,
        archived=source.card_id,
        message=f"Card '{source.task_type}' merged into '{target.task_type}'",
    )


@router.get("/stats", response_model=WorkExperienceStats)
async def get_stats(
    status: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
) -> WorkExperienceStats:
    cards = _filtered_cards(status=status, level=level, include_disabled=True)
    if not cards:
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

    total = len(cards)
    by_status = dict(Counter(c.status for c in cards))
    by_level = dict(Counter(c.experience_level for c in cards))
    by_scope = {"global": total}
    total_hits = sum(c.executions for c in cards)
    total_effective = sum(_effective_count(c) for c in cards)
    avg_confidence = sum(_confidence(c) for c in cards) / total
    avg_quality = sum(_quality_score(c) for c in cards) / total
    avg_maturity = sum(_maturity_score(c) for c in cards) / total
    top = max(cards, key=lambda c: _quality_score(c))

    return WorkExperienceStats(
        total_cards=total,
        by_status=by_status,
        by_level=by_level,
        by_scope=by_scope,
        total_hits=total_hits,
        total_effective_uses=total_effective,
        avg_confidence=round(avg_confidence, 4),
        avg_quality_score=round(avg_quality, 4),
        avg_maturity_score=round(avg_maturity, 2),
        top_scoring_card=_to_summary(top),
    )


@router.get("/candidates", response_model=CardListResponse)
async def list_candidates(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CardListResponse:
    cards = _filtered_cards(status="candidate", include_disabled=True)
    total = len(cards)
    return CardListResponse(
        cards=[_to_response(c) for c in cards[offset : offset + limit]],
        total=total,
    )


@router.get("/top-cards", response_model=CardListResponse)
async def list_top_cards(
    status: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    top_k: int = Query(10, ge=1, le=50),
    scope: Optional[str] = Query(None),
) -> CardListResponse:
    cards = _filtered_cards(status=status, level=level, scope=scope)
    cards.sort(
        key=lambda c: (_quality_score(c), _maturity_score(c)),
        reverse=True,
    )
    top = cards[:top_k]
    return CardListResponse(
        cards=[_to_response(c) for c in top],
        total=len(top),
    )


@router.get("/by-level", summary="Count cards by v4 experience level")
async def list_by_level() -> dict[str, int]:
    cards = _get_store().list_all()
    counts = Counter(c.experience_level for c in cards)
    return {
        level: counts.get(level, 0)
        for level in ["new", "observed", "mature", "deprecated"]
    }


# =============================================================================
# Settings — reflection model selection
# =============================================================================

_SETTINGS_PATH = (
    Path.home() / ".hubos" / "work_experience_v4" / "settings.json"
)


class WorkExperienceSettings(BaseModel):
    """Settings for the Work Experience system."""

    reflection_provider_id: str = ""
    reflection_model: str = ""


class WorkExperienceSettingsResponse(BaseModel):
    reflection_provider_id: str = ""
    reflection_model: str = ""
    available_providers: list[dict[str, Any]] = []


def _load_settings() -> WorkExperienceSettings:
    if _SETTINGS_PATH.exists():
        try:
            data = json.loads(_SETTINGS_PATH.read_text("utf-8"))
            return WorkExperienceSettings(
                reflection_provider_id=data.get("reflection_provider_id", ""),
                reflection_model=data.get("reflection_model", ""),
            )
        except Exception:
            pass
    return WorkExperienceSettings()


def _save_settings(settings: WorkExperienceSettings) -> None:
    _SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _SETTINGS_PATH.write_text(
        json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _collect_available_providers() -> list[dict[str, Any]]:
    """Gather all providers with api_key configured and their models."""
    from hubos.constant import SECRET_DIR

    result: list[dict[str, Any]] = []
    providers_dir = SECRET_DIR / "providers"

    for subdir in ("builtin", "custom"):
        pdir = providers_dir / subdir
        if not pdir.exists():
            continue
        for pfile in sorted(pdir.glob("*.json")):
            try:
                cfg = json.loads(pfile.read_text("utf-8"))
            except Exception:
                continue
            api_key = cfg.get("api_key", "")
            if not api_key:
                continue
            provider_id = cfg.get("id", pfile.stem)
            models = []
            for m in cfg.get("models", []):
                mid = m if isinstance(m, str) else m.get("id", "")
                mname = m.get("name", mid) if isinstance(m, dict) else mid
                if mid:
                    models.append({"id": mid, "name": mname})
            # Also include chat_model as a model option
            chat_model = cfg.get("chat_model", "")
            if chat_model and not any(m["id"] == chat_model for m in models):
                models.append({"id": chat_model, "name": chat_model})
            result.append(
                {
                    "provider_id": provider_id,
                    "name": cfg.get("name", provider_id),
                    "base_url": cfg.get("base_url", ""),
                    "models": models,
                },
            )
    return result


@router.get("/settings", summary="Get Work Experience settings")
async def get_settings() -> WorkExperienceSettingsResponse:
    settings = _load_settings()
    providers = _collect_available_providers()
    return WorkExperienceSettingsResponse(
        reflection_provider_id=settings.reflection_provider_id,
        reflection_model=settings.reflection_model,
        available_providers=providers,
    )


class UpdateSettingsRequest(BaseModel):
    reflection_provider_id: str
    reflection_model: str


@router.put("/settings", summary="Update Work Experience settings")
async def update_settings(
    body: UpdateSettingsRequest,
) -> WorkExperienceSettingsResponse:
    settings = WorkExperienceSettings(
        reflection_provider_id=body.reflection_provider_id,
        reflection_model=body.reflection_model,
    )
    _save_settings(settings)
    providers = _collect_available_providers()
    return WorkExperienceSettingsResponse(
        reflection_provider_id=settings.reflection_provider_id,
        reflection_model=settings.reflection_model,
        available_providers=providers,
    )
