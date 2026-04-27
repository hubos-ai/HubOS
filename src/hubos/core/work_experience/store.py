# -*- coding: utf-8 -*-
"""Local file-based WorkExperience store.

Follows the same patterns as LocalMemoryStore:
- Atomic JSON writes via temp-file swap
- Append-only JSONL index
- Directory layout organised by scope
"""

import dataclasses
import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from uuid import UUID

from hubos.core.work_experience.schemas import (
    ExperienceLevel,
    WorkExperience,
    WorkExperienceScope,
)

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path.home() / ".hubos" / "work_experience"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _serialize_value(v: Any) -> Any:
    """Serialize dataclass field values to JSON-compatible types."""
    if isinstance(v, UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return [_serialize_value(i) for i in v]
    if isinstance(v, dict):
        return {kk: _serialize_value(vv) for kk, vv in v.items()}
    return v


def _card_to_dict(card: WorkExperience) -> dict:
    """Convert a WorkExperience dataclass to a JSON-serializable dict."""
    data = dataclasses.asdict(card)
    return _serialize_value(data)


def _dict_to_card(data: dict) -> WorkExperience:
    """Reconstruct a WorkExperience from a JSON-loaded dict."""
    from hubos.core.work_experience.schemas import (
        ExperienceLevel,
        WorkExperienceStatus,
    )

    data = dict(data)
    # Restore WorkExperienceScope enum
    if isinstance(data.get("scope"), str):
        data["scope"] = WorkExperienceScope(data["scope"])
    # Restore WorkExperienceStatus enum
    if isinstance(data.get("status"), str):
        data["status"] = WorkExperienceStatus(data["status"])
    # Restore ExperienceLevel enum (with fallback for old cards)
    if isinstance(data.get("experience_level"), str):
        try:
            data["experience_level"] = ExperienceLevel(
                data["experience_level"],
            )
        except ValueError:
            data["experience_level"] = ExperienceLevel.NEW
    # Restore UUID fields
    for uuid_field in ("experience_id", "supersedes_experience_id"):
        if isinstance(data.get(uuid_field), str):
            data[uuid_field] = UUID(data[uuid_field])
    # Restore datetime fields
    for dt_field in (
        "created_at",
        "updated_at",
        "last_retrieved_at",
        "last_used_at",
    ):
        if isinstance(data.get(dt_field), str):
            data[dt_field] = datetime.fromisoformat(data[dt_field])
    return WorkExperience(**data)


class LocalWorkExperienceStore:
    """
    File-based WorkExperience store.

    Directory layout::

        {root}/
        ├── index.jsonl                      # append-only card metadata index
        └── by_scope/
            ├── global/{id}.json
            ├── user/{id}.json
            ├── project/{id}.json
            └── session/{id}.json

    Atomic writes via rename-from-temp pattern.
    """

    def __init__(self, root: Optional[Path] = None) -> None:
        """
        Initialize the store.

        Args:
            root: Override the root directory. Defaults to ~/.hubos/work_experience.
        """
        self._root = (root or _DEFAULT_ROOT).expanduser().resolve()
        self._scope_dir = self._root / "by_scope"
        self._ensure_dirs()

    # ---- Directory layout ----

    def _ensure_dirs(self) -> None:
        for scope in WorkExperienceScope:
            (self._scope_dir / scope.value).mkdir(parents=True, exist_ok=True)

    def _card_path(
        self,
        experience_id: UUID,
        scope: WorkExperienceScope,
    ) -> Path:
        return self._scope_dir / scope.value / f"{experience_id}.json"

    # ---- Low-level JSON ops (same pattern as LocalMemoryStore) ----

    @staticmethod
    def _read_json(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _write_json_atomic(path: Path, data: dict) -> None:
        """Atomic write: write to .tmp then rename."""
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        shutil.move(str(tmp), str(path))

    # ---- Store interface ----

    def save(self, experience: WorkExperience) -> None:
        """Persist or update a work experience card."""
        experience.updated_at = _utcnow()
        path = self._card_path(experience.experience_id, experience.scope)
        self._write_json_atomic(path, _card_to_dict(experience))

        # Append to index (id, scope, title, keywords, created_at)
        self._append_index(experience)

    def _append_index(self, experience: WorkExperience) -> None:
        """Append a lightweight entry to the append-only index."""
        index_path = self._root / "index.jsonl"
        entry = {
            "experience_id": str(experience.experience_id),
            "scope": experience.scope.value,
            "status": experience.status.value,
            "experience_level": experience.experience_level.value,
            "maturity_score": experience.maturity_score,
            "title": experience.title,
            "trigger_keywords": experience.trigger_keywords,
            "trigger_hint": experience.trigger_hint,
            "confidence": experience.confidence,
            "hit_count": experience.hit_count,
            "effective_count": experience.effective_count,
            "disabled": experience.disabled,
            "created_at": experience.created_at.isoformat(),
            "updated_at": experience.updated_at.isoformat(),
        }
        with index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get(self, experience_id: UUID) -> Optional[WorkExperience]:
        """Retrieve a card by ID, scanning all scope directories."""
        for scope in WorkExperienceScope:
            path = self._card_path(experience_id, scope)
            if path.exists():
                data = self._read_json(path)
                return _dict_to_card(data)
        return None

    def list_all(self, include_disabled: bool = False) -> list[WorkExperience]:
        """List all cards across all scopes. Pass include_disabled=True to include disabled cards."""
        results: list[WorkExperience] = []
        for scope in WorkExperienceScope:
            results.extend(
                self._list_scope_dir(scope, include_disabled=include_disabled),
            )
        return results

    def list_by_scope(
        self,
        scope: WorkExperienceScope,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List cards for a given scope. Pass include_disabled=True to include disabled cards."""
        return self._list_scope_dir(scope, include_disabled=include_disabled)

    def _list_scope_dir(
        self,
        scope: WorkExperienceScope,
        include_disabled: bool = False,
    ) -> list[WorkExperience]:
        """List cards in a scope directory."""
        scope_dir = self._scope_dir / scope.value
        if not scope_dir.exists():
            return []
        results: list[WorkExperience] = []
        for path in scope_dir.glob("*.json"):
            try:
                data = self._read_json(path)
                exp = _dict_to_card(data)
                if include_disabled or not exp.disabled:
                    results.append(exp)
            except Exception:
                # Skip corrupted files
                continue
        return results

    def disable(self, experience_id: UUID) -> bool:
        """Mark a card as disabled. Returns True if found."""
        exp = self.get(experience_id)
        if exp is None:
            return False
        exp.disabled = True
        self.save(exp)
        return True

    def increment_hit(self, experience_id: UUID) -> None:
        """Increment hit_count and update last_retrieved_at."""
        exp = self.get(experience_id)
        if exp is None:
            return
        exp.hit_count += 1
        exp.last_retrieved_at = _utcnow()
        self.save(exp)
        logger.debug(
            "WE_HIT_COUNT incremented",
            extra={
                "experience_id": str(experience_id),
                "title": exp.title[:60],
                "hit_count": exp.hit_count,
                "last_retrieved_at": exp.last_retrieved_at.isoformat()
                if exp.last_retrieved_at
                else None,
            },
        )

    def update_status(self, experience_id: UUID, status) -> bool:
        """Update a card's governance status. Returns True if found and updated."""
        from hubos.core.work_experience.schemas import WorkExperienceStatus

        exp = self.get(experience_id)
        if exp is None:
            return False
        if not exp.status.can_transition_to(status):
            return False
        exp.status = status
        self.save(exp)
        return True

    def record_effective_use(self, experience_id: UUID) -> None:
        """Record a successful prompt injection (increments effective_count and sets last_used_at)."""
        exp = self.get(experience_id)
        if exp is None:
            return
        exp.effective_count += 1
        exp.last_used_at = _utcnow()
        self.save(exp)
        logger.debug(
            "WE_EFFECTIVE_USE recorded",
            extra={
                "experience_id": str(experience_id),
                "title": exp.title[:60],
                "effective_count": exp.effective_count,
                "last_used_at": exp.last_used_at.isoformat()
                if exp.last_used_at
                else None,
            },
        )

    def update_experience_level(
        self,
        experience_id: UUID,
        level: ExperienceLevel,
    ) -> bool:
        """Update a card's experience level. Returns True if found and updated."""
        exp = self.get(experience_id)
        if exp is None:
            return False
        if isinstance(level, str):
            level = ExperienceLevel(level)
        if not exp.experience_level.can_transition_to(level):
            return False
        exp.experience_level = level
        self.save(exp)
        logger.info(
            "WE_EXPERIENCE_LEVEL updated",
            extra={
                "experience_id": str(experience_id),
                "new_level": level.value,
            },
        )
        return True

    def update_maturity_score(self, experience_id: UUID, score: float) -> bool:
        """Update a card's maturity score. Returns True if found and updated."""
        exp = self.get(experience_id)
        if exp is None:
            return False
        exp.maturity_score = max(0.0, min(100.0, score))
        self.save(exp)
        return True

    def find_similar(
        self,
        trigger_hint_prefix: str,
        keywords: Optional[list[str]] = None,
        exclude_id: Optional[UUID] = None,
    ) -> list[WorkExperience]:
        """
        Find similar experiences by trigger hint prefix and keyword overlap.

        Used for experience merging/updating when a new card is created.
        Returns experiences with same trigger_hint prefix and keyword overlap.
        """
        all_cards = self.list_all(include_disabled=False)
        results = []

        kw_set = {k.lower() for k in (keywords or [])}

        for card in all_cards:
            if exclude_id and card.experience_id == exclude_id:
                continue
            # Check trigger_hint prefix match
            if not card.trigger_hint.startswith(trigger_hint_prefix):
                continue
            # Check keyword overlap
            if kw_set:
                card_kw = {k.lower() for k in card.trigger_keywords}
                overlap = len(kw_set & card_kw)
                if overlap == 0:
                    continue
            results.append(card)

        return results

    # ---- Maintenance utilities ----

    def rebuild_keyword_index(self) -> None:
        """Rebuild the keyword index by re-scanning all scope directories.

        This is a maintenance utility. The keyword index is currently
        rebuilt in-memory on every retrieve call to keep complexity low.
        A future phase may persist the keyword index.
        """
        # In Phase 0-3, keyword matching is done in-memory on retrieve.
        # This method is a placeholder for future index persistence.
        pass

    # ---- Count helpers (used by tests) ----

    def count_all(self) -> int:
        """Return total number of non-disabled cards."""
        return len(self.list_all())

    def count_by_scope(self, scope: WorkExperienceScope) -> int:
        """Return number of non-disabled cards in a scope."""
        return len(self.list_by_scope(scope))

    # ---- Governance observability ----

    def get_all_stats(self) -> dict[str, Any]:
        """
        Return aggregate statistics across all non-disabled cards.

        Used by the console stats bar and governance dashboards.
        """
        cards = self.list_all()
        if not cards:
            return {
                "total_cards": 0,
                "total_hits": 0,
                "total_effective_uses": 0,
                "avg_confidence": 0.0,
                "avg_quality_score": 0.0,
            }

        total_hits = sum(c.hit_count for c in cards)
        total_effective = sum(c.effective_count for c in cards)

        def quality_score(c: WorkExperience) -> float:
            return c.confidence * (
                1.0 + c.hit_count / 10.0 + c.effective_count / 5.0
            )

        return {
            "total_cards": len(cards),
            "total_hits": total_hits,
            "total_effective_uses": total_effective,
            "avg_confidence": sum(c.confidence for c in cards) / len(cards),
            "avg_quality_score": sum(quality_score(c) for c in cards)
            / len(cards),
            "hit_rate": total_effective / total_hits
            if total_hits > 0
            else 0.0,
        }

    def get_top_effective_cards(self, n: int = 10) -> list[WorkExperience]:
        """Return top N cards by effective_count (for console stats / auto-approve)."""
        cards = self.list_all()
        return sorted(cards, key=lambda c: c.effective_count, reverse=True)[:n]

    def get_high_hit_low_effective_cards(
        self,
        min_hits: int = 5,
        effective_ratio_threshold: float = 0.3,
    ) -> list[WorkExperience]:
        """
        Return cards that are frequently retrieved but rarely effective.

        These are governance-signal cards: they get picked up (high hit_count)
        but don't improve outcomes (low effective_count / hit_count ratio).

        Args:
            min_hits: Minimum hit_count to consider
            effective_ratio_threshold: Flag cards where effective_count/hit_count
                is below this ratio (e.g. 0.3 = fewer than 30% of retrievals are effective)

        Returns:
            List of cards that may need review or disabling.
        """
        cards = self.list_all()
        alerts: list[WorkExperience] = []
        for c in cards:
            if c.hit_count < min_hits:
                continue
            ratio = c.effective_count / c.hit_count if c.hit_count > 0 else 0.0
            if ratio < effective_ratio_threshold:
                alerts.append(c)

        if alerts:
            logger.warning(
                "WE_GOVERNANCE_ALERT high_hit_low_effective",
                extra={
                    "alert_card_count": len(alerts),
                    "card_ids": [str(c.experience_id) for c in alerts],
                    "card_titles": [c.title[:60] for c in alerts],
                    "min_hits": min_hits,
                    "effective_ratio_threshold": effective_ratio_threshold,
                },
            )

        return alerts
