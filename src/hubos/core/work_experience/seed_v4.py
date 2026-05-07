# -*- coding: utf-8 -*-
"""Seed v4 cards from seed_cards_v4.json into the v4 CardStore.

Idempotent: skips cards that already exist.
"""
import json
import logging
from pathlib import Path

from .store_v4 import CardStore
from .schemas_v4 import WorkflowCard

logger = logging.getLogger(__name__)

_SEED_FILE = Path(__file__).parent / "seed_data" / "seed_cards_v4.json"


def seed_v4_cards(store: CardStore | None = None) -> int:
    """Load seed cards into the store. Returns count of new cards created."""
    if not _SEED_FILE.exists():
        logger.debug("Seed file not found: %s", _SEED_FILE)
        return 0

    store = store or CardStore()
    data = json.loads(_SEED_FILE.read_text("utf-8"))

    created = 0
    for entry in data:
        card_id = entry.get("card_id", "")
        existing = store.get(card_id)
        if existing:
            continue  # Skip if already exists (idempotent)

        card = WorkflowCard.from_dict(entry)
        store.save(card)
        created += 1
        logger.info("Seeded card: %s (%s)", card.task_type, card.card_id)

    if created:
        logger.info("Seeded %d new v4 cards", created)
    return created
