# -*- coding: utf-8 -*-
"""Seed data loader for Work Experience cards.

Bundled methodology cards that are installed on first run. These represent
hard-won operational knowledge that should be available on any fresh deploy.
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_SEED_DIR = Path(__file__).parent / "seed_data"


def seed_work_experience_cards(store=None) -> int:
    """
    Load seed methodology cards into the work experience store.

    Skips cards whose title already exists (idempotent).

    Args:
        store: A LocalWorkExperienceStore instance. If None, creates one.

    Returns:
        Number of new cards seeded.
    """
    if store is None:
        from hubos.core.work_experience.store import LocalWorkExperienceStore

        store = LocalWorkExperienceStore()

    if not _SEED_DIR.exists():
        logger.debug("No seed_data directory found, skipping")
        return 0

    # Collect existing titles to avoid duplicates
    existing = store.list_all(include_disabled=True)
    existing_titles = {c.title for c in existing}

    seeded = 0
    for path in sorted(_SEED_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                logger.debug("Skipping non-v3 seed file: %s", path.name)
                continue
            title = data.get("title", "")
            if title and title in existing_titles:
                logger.debug("Seed card already exists: %s", title[:60])
                continue

            from hubos.core.work_experience.store import _dict_to_card

            card = _dict_to_card(data)
            store.save(card)
            seeded += 1
            logger.info(
                "Seeded work experience card: %s",
                card.title[:60],
            )
        except Exception as exc:
            logger.warning(
                "Failed to seed card from %s: %s",
                path.name,
                exc,
            )

    if seeded:
        logger.info("Seeded %d new work experience cards", seeded)
    return seeded
