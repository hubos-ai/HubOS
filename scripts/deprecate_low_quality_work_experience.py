#!/usr/bin/env python3
"""Deprecate low-quality chat-summary Work Experience cards.

This is intentionally conservative:
- dry-run by default
- marks matching cards as ExperienceLevel.DEPRECATED instead of deleting them
- targets only the old generic "Handled chat request" style cards
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hubos.core.work_experience.schemas import ExperienceLevel
from hubos.core.work_experience.store import LocalWorkExperienceStore


GENERIC_PREFIXES = (
    "handled chat request",
    "delivered a response",
    "response summary",
)


def _is_low_quality_chat_card(card) -> bool:
    title = (card.title or "").strip().lower()
    guidance = (card.guidance or "").strip().lower()
    worked = " ".join(card.what_worked or []).strip().lower()

    if title.startswith("handled chat request"):
        return True

    generic_hits = sum(
        1
        for prefix in GENERIC_PREFIXES
        if prefix in guidance or prefix in worked
    )
    if generic_hits >= 2:
        return True

    if (
        card.scope.value == "session"
        and not card.recommended_tool_order
        and not card.what_failed
        and any(prefix in worked for prefix in GENERIC_PREFIXES)
    ):
        return True

    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        default=str(Path.home() / ".hubos" / "work_experience"),
        help="Work Experience store root",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mark matching cards deprecated. Default is dry-run.",
    )
    args = parser.parse_args()

    store = LocalWorkExperienceStore(root=Path(args.root))
    cards = store.list_all(include_disabled=True)
    matches = [c for c in cards if _is_low_quality_chat_card(c)]

    print(f"Scanned {len(cards)} cards")
    print(f"Matched {len(matches)} low-quality chat-summary cards")

    for card in matches[:20]:
        print(f"- {card.experience_id} [{card.scope.value}/{card.experience_level.value}] {card.title[:100]}")
    if len(matches) > 20:
        print(f"... and {len(matches) - 20} more")

    if not args.apply:
        print("\nDry run only. Re-run with --apply to mark them deprecated.")
        return 0

    updated = 0
    for card in matches:
        if card.experience_level == ExperienceLevel.DEPRECATED:
            continue
        card.experience_level = ExperienceLevel.DEPRECATED
        store.save(card)
        updated += 1

    print(f"\nDeprecated {updated} cards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
