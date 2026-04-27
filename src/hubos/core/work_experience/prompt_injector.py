# -*- coding: utf-8 -*-
"""Work Experience Layer — Work Guidance Prompt Injection.

Compresses WorkExperience cards into compact work guidance hints and injects them into
the LLM prompt at generate_for_stage() call time.

Work guidance format:
- Recommended tool order
- Recommended workflow steps
- Key pitfalls to avoid
- Stable practices that worked

Hard constraints enforced here:
- Does NOT modify prompts, tools, or skills
- Does NOT auto-select tools or publish skills
- Controlled by ENABLE_WORK_EXPERIENCE_PROMPT_INJECTION flag
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# =============================================================================
# Configuration
# =============================================================================

DEFAULT_MAX_K = 2
DEFAULT_MAX_CHARS_PER_CARD = 200
DEFAULT_MAX_TOTAL_CHARS = 400

_INJECTION_HEADER = "\n\n[Work Guidance]\n"
_INJECTION_FOOTER = "\n[/Work Guidance]\n"


# =============================================================================
# Work Guidance Compression
# =============================================================================


def compress_experience_card(
    card: dict[str, Any],
    max_chars: int = DEFAULT_MAX_CHARS_PER_CARD,
) -> str:
    """
    Compress a work experience card into a compact work guidance string.

    Output format prioritizes actionable guidance:
    - Pattern: what kind of task this applies to
    - Tools: recommended tool order (if available)
    - Steps: recommended workflow
    - Pitfalls: what to avoid
    - Stable practices: what worked well

    Format:
    "[Pattern] Tool: Step1 → Step2 → Pitfall: Avoid X | Stable: Y"

    Examples:
    - "CSV files: Use encoding detection → pandas read_csv | Pitfall: Don't assume UTF-8"
    - "Web crawl: Check robots.txt → curl fetch → parse | Pitfall: Timeout handling"

    Args:
        card: WorkExperience card dict (from task.work_experience_cards)
        max_chars: Hard character limit for the entire compressed string

    Returns:
        Compact work guidance string
    """
    _lines: list[str] = []

    # Pattern summary (what kind of task this applies to)
    pattern = (card.get("usage_pattern_summary") or card.get("title") or "")[
        :50
    ].strip()

    # Recommended tool order
    tool_order = card.get("recommended_tool_order") or []
    tools_str = ""
    if tool_order:
        tools_str = " → ".join(str(t)[:15] for t in tool_order[:3])

    # Recommended workflow
    workflow = card.get("recommended_workflow") or []
    workflow_str = ""
    if workflow:
        workflow_str = " → ".join(str(s)[:20] for s in workflow[:3])

    # What to avoid (pitfalls)
    avoidance_list = card.get("avoidance") or []
    pitfalls: list[str] = []
    if avoidance_list:
        for a in avoidance_list[:2]:
            a_str = str(a)[:40].strip()
            if a_str:
                pitfalls.append(f"Avoid: {a_str}")
    # Also check what_failed
    failed_list = card.get("what_failed") or []
    for f in failed_list[:2]:
        f_str = str(f)[:40].strip()
        if f_str and not any(f_str in p for p in pitfalls):
            pitfalls.append(f"Avoid: {f_str}")

    # Stable practices (what worked)
    worked_list = card.get("what_worked") or []
    stable: list[str] = []
    for w in worked_list[:2]:
        w_str = str(w)[:40].strip()
        if w_str:
            stable.append(f"Do: {w_str}")

    # Build guidance string
    parts: list[str] = []

    # Pattern and tools
    if pattern and tools_str:
        parts.append(f"{pattern}: {tools_str}")
    elif tools_str:
        parts.append(f"Tools: {tools_str}")
    elif pattern:
        parts.append(pattern)

    # Workflow steps
    if workflow_str:
        parts.append(f"Steps: {workflow_str}")

    # Pitfalls
    if pitfalls:
        parts.append(" | ".join(pitfalls[:2]))

    # Stable practices
    if stable:
        parts.append(" | ".join(stable[:2]))

    # Join with separator
    guidance = " | ".join(parts)

    # Hard truncate to max_chars
    if len(guidance) > max_chars:
        guidance = guidance[: max_chars - 3] + "..."

    return guidance


def _estimate_tokens(chars: int) -> int:
    """Rough character-to-token estimate (1 token ≈ 4 chars)."""
    return (chars + 3) // 4


def _trim_to_budget(
    hints: list[str],
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> list[str]:
    """
    Remove hints from the end until total fits within max_total_chars budget.

    Includes the overhead of the injection header/footer markers.
    """
    header_footer_chars = len(_INJECTION_HEADER) + len(_INJECTION_FOOTER)
    budget = max_total_chars - header_footer_chars

    result = []
    total = 0
    for hint in hints:
        needed = (
            total + len(hint) + (1 if result else 0)
        )  # +1 for newline between hints
        if needed <= budget:
            result.append(hint)
            total = needed
        else:
            break

    return result


# =============================================================================
# Injection Builder
# =============================================================================


def build_experience_injection(
    cards: list[dict[str, Any]],
    max_k: int = DEFAULT_MAX_K,
    max_chars_per_card: int = DEFAULT_MAX_CHARS_PER_CARD,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """
    Build a compact work guidance injection string for a prompt.

    Applies:
    1. Take at most max_k cards (default 2)
    2. Compress each card to work guidance format
    3. Trim total length to max_total_chars
    4. Return empty string if no cards survive

    Args:
        cards: List of experience card dicts (from task.work_experience_cards)
        max_k: Maximum number of cards to inject (default 2)
        max_chars_per_card: Per-card character limit (default 200)
        max_total_chars: Total injection budget including markers (default 400)

    Returns:
        Injection string like:
        "\n\n[Work Guidance]\nPattern: Tools → Steps | Pitfall: X | Stable: Y\n[/Work Guidance]\n"
        or empty string if no cards or injection disabled.
    """
    if not cards:
        return ""

    # Take top-k (already sorted by maturity in retriever)
    selected = cards[:max_k]

    # Compress each card to work guidance
    hints = [compress_experience_card(c, max_chars_per_card) for c in selected]

    # Trim to budget
    hints = _trim_to_budget(hints, max_total_chars)

    if not hints:
        return ""

    # Assemble with markers
    body = "\n".join(hints)
    return f"{_INJECTION_HEADER}{body}{_INJECTION_FOOTER}"


def inject_experience_into_prompt(
    user_prompt: str,
    cards: Optional[list[dict[str, Any]]] = None,
    max_k: int = DEFAULT_MAX_K,
    max_chars_per_card: int = DEFAULT_MAX_CHARS_PER_CARD,
    max_total_chars: int = DEFAULT_MAX_TOTAL_CHARS,
) -> str:
    """
    Inject compressed work guidance into a user prompt.

    Injects BEFORE the main user content (after the stage template prefix)
    so that experience context is available before the LLM processes the task.

    Args:
        user_prompt: The assembled user prompt (from stage template)
        cards: List of experience card dicts (from task.work_experience_cards)
        max_k: Maximum cards to inject
        max_chars_per_card: Per-card char limit
        max_total_chars: Total injection budget

    Returns:
        Original prompt if no cards, otherwise prompt with work guidance prepended.
    """
    if not cards:
        return user_prompt

    injection = build_experience_injection(
        cards,
        max_k=max_k,
        max_chars_per_card=max_chars_per_card,
        max_total_chars=max_total_chars,
    )

    if not injection:
        return user_prompt

    # Log injection details for observability
    selected = cards[:max_k]
    logger.debug(
        "WE_PROMPT_INJECTION",
        extra={
            "card_count": len(selected),
            "card_ids": [c.get("experience_id") for c in selected],
            "card_titles": [c.get("title", "")[:60] for c in selected],
            "injection_chars": len(injection),
            "injection_preview": injection[:300],
            "original_prompt_chars": len(user_prompt),
        },
    )

    return injection + user_prompt
