# -*- coding: utf-8 -*-
"""Work Experience v4 — CardStore.

Flat JSON files, one per card. Simple index for fast lookup.
No more append-only index.jsonl, no more by_scope subdirectories.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

from .schemas_v4 import WorkflowCard

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = Path.home() / ".hubos" / "work_experience_v4"


class CardStore:
    """Manages WorkflowCards as flat JSON files."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = root or _DEFAULT_ROOT
        self._cards_dir = self._root / "cards"
        self._index_path = self._root / "index.json"
        self._write_lock = threading.RLock()
        self._cards_dir.mkdir(parents=True, exist_ok=True)

    # ---- Index management ----

    def _load_index(self) -> dict[str, str]:
        """Load index: {task_type: card_id}. Returns {} if missing."""
        if not self._index_path.exists():
            return {}
        try:
            return json.loads(self._index_path.read_text("utf-8"))
        except Exception:
            logger.debug("store_v4: failed to load index", exc_info=True)
            return {}

    def _save_index(self, index: dict[str, str]) -> None:
        temp_path = self._index_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self._index_path)

    # ---- CRUD ----

    def save(self, card: WorkflowCard) -> None:
        """Save or update a card. Updates index."""
        with self._write_lock:
            path = self._cards_dir / f"{card.card_id}.json"
            temp_path = path.with_suffix(".json.tmp")
            temp_path.write_text(card.to_json(), encoding="utf-8")
            temp_path.replace(path)
            index = self._load_index()
            index[card.task_type] = card.card_id
            self._save_index(index)

    def get(self, card_id: str) -> Optional[WorkflowCard]:
        """Get card by card_id (slug)."""
        path = self._cards_dir / f"{card_id}.json"
        if not path.exists():
            return None
        try:
            return WorkflowCard.from_json(path.read_text("utf-8"))
        except Exception:
            logger.debug(
                "store_v4: failed to load card %s",
                card_id,
                exc_info=True,
            )
            return None

    def get_by_task_type(self, task_type: str) -> Optional[WorkflowCard]:
        """Get card by task_type (human-readable)."""
        index = self._load_index()
        card_id = index.get(task_type)
        if not card_id:
            return None
        return self.get(card_id)

    def get_by_topic_key(self, topic_key: str) -> Optional[WorkflowCard]:
        """Get card by topic_key (normalised merge key)."""
        if not topic_key:
            return None
        for card in self.list_all():
            if card.topic_key == topic_key:
                return card
        return None

    def list_all(self) -> list[WorkflowCard]:
        """List all cards."""
        results = []
        for path in self._cards_dir.glob("*.json"):
            try:
                results.append(WorkflowCard.from_json(path.read_text("utf-8")))
            except Exception:
                logger.debug(
                    "store_v4: skipping invalid card file %s",
                    path.name,
                    exc_info=True,
                )
                continue
        return results

    def list_index(self) -> list[dict[str, Any]]:
        """Lightweight listing: [{task_type, card_id, description}]."""
        cards = self.list_all()
        return [
            {
                "task_type": c.task_type,
                "card_id": c.card_id,
                "description": c.description,
                "entities": list(c.entities),
                "executions": c.executions,
            }
            for c in cards
        ]

    def delete(self, card_id: str) -> None:
        """Delete a card by card_id."""
        path = self._cards_dir / f"{card_id}.json"
        if path.exists():
            path.unlink()
        # Clean index
        index = self._load_index()
        to_remove = [k for k, v in index.items() if v == card_id]
        for k in to_remove:
            del index[k]
        if to_remove:
            self._save_index(index)

    def count(self) -> int:
        return len(list(self._cards_dir.glob("*.json")))
