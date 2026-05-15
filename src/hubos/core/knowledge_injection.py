# -*- coding: utf-8 -*-
"""Knowledge Injection Layer — unified guidance injection before task execution.

Combines Work Experience v4 cards + structured knowledge files into a single
injection prompt for the main model.

Usage:
    from hubos.core.knowledge_injection import build_relevant_guidance
    text, meta = build_relevant_guidance(user_message, card, workspace_dir)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeHit:
    """A single scored knowledge item."""

    source: str  # "work_experience" | "knowledge"
    title: str
    summary: str
    score: float
    type: str = "general"
    entities: list[str] = field(default_factory=list)
    confidence: str = "medium"  # high / medium / low
    domain: str = ""
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)


@dataclass
class KnowledgeInjectionConfig:
    """Tunable parameters for knowledge injection."""

    max_items: int = 3
    default_max_tokens: int = 300
    complex_max_tokens: int = 600
    explicit_max_tokens: int = 1000
    min_score: float = 0.35


_DEFAULT_CONFIG = KnowledgeInjectionConfig()

# Patterns that indicate user explicitly wants historical knowledge
_EXPLICIT_REQUEST_PATTERNS = re.compile(
    r"参考历史经验|查历史知识|根据之前经验|按照之前方法|参照上次|按历史|复用之前|follow previous",
    re.IGNORECASE,
)

# Patterns that indicate a complex task (warrants more token budget)
_COMPLEX_TASK_PATTERNS = re.compile(
    r"开发|实现|修复|优化|部署|重构|调研|客户开发|批量|自动化|多agent"
    r"|multi.?agent|automat|batch|refactor|deploy|implement|optimize",
    re.IGNORECASE,
)

# Multi-step connectors suggesting a complex workflow
_STEP_CONNECTOR_PATTERNS = re.compile(
    r"先.{2,}然后|先.{2,}再|然后.{2,}最后|并且|接着",
)
_COMPLEX_MSG_CHAR_THRESHOLD = 80

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_relevant_guidance(
    user_message: str,
    experience_card: Any = None,
    workspace_dir: Path | str | None = None,
    config: KnowledgeInjectionConfig = _DEFAULT_CONFIG,
    *,
    task_type_hint: str = "",
) -> tuple[str, dict[str, Any]]:
    """
    Build a unified guidance string from WE cards + knowledge files.

    Returns (guidance_text, metadata_dict). guidance_text is empty string
    when nothing is relevant.
    """
    hits: list[KnowledgeHit] = []

    # 1. WE card → KnowledgeHit
    if experience_card is not None:
        hits.append(_card_to_hit(experience_card))

    # 2. Scan knowledge files
    if workspace_dir is not None:
        ws = Path(workspace_dir)
        knowledge_hits = _scan_knowledge_files(
            ws, user_message, task_type_hint,
        )
        hits.extend(knowledge_hits)

    if not hits:
        return "", {"item_count": 0, "estimated_tokens": 0, "budget_tokens": 0, "sources": {}, "titles": [], "scores": []}

    # 3. Score, sort, filter
    hits.sort(key=lambda h: h.score, reverse=True)
    hits = [h for h in hits if h.score >= config.min_score]
    hits = hits[: config.max_items]

    if not hits:
        return "", {"item_count": 0, "estimated_tokens": 0, "budget_tokens": 0, "sources": {}, "titles": [], "scores": []}

    # 4. Token budget: explicit(1000) > complex(600) > default(300)
    if _EXPLICIT_REQUEST_PATTERNS.search(user_message):
        max_tokens = config.explicit_max_tokens
    elif _is_complex_task(user_message):
        max_tokens = config.complex_max_tokens
    else:
        max_tokens = config.default_max_tokens

    # 5. Format output
    guidance_text = _format_hits(hits, max_tokens)

    # 5. Build metadata
    source_counts: dict[str, int] = {}
    for h in hits:
        source_counts[h.source] = source_counts.get(h.source, 0) + 1

    meta = {
        "item_count": len(hits),
        "estimated_tokens": len(guidance_text) // 4,
        "budget_tokens": max_tokens,
        "sources": source_counts,
        "titles": [h.title for h in hits],
        "scores": [round(h.score, 2) for h in hits],
    }
    return guidance_text, meta


# ---------------------------------------------------------------------------
# WE card → KnowledgeHit
# ---------------------------------------------------------------------------


def _card_to_hit(card: Any) -> KnowledgeHit:
    """Convert a WorkflowCard into a KnowledgeHit."""
    summary = card.formatted_for_injection()
    return KnowledgeHit(
        source="work_experience",
        title=getattr(card, "task_type", "unknown"),
        summary=summary,
        score=0.95,  # pre-matched by retriever, high confidence
        type=getattr(card, "experience_type", "general"),
        entities=getattr(card, "entities", []),
        confidence="high",
    )


# ---------------------------------------------------------------------------
# Knowledge file scanning
# ---------------------------------------------------------------------------


def _scan_knowledge_files(
    workspace_dir: Path,
    user_message: str,
    task_type_hint: str,
) -> list[KnowledgeHit]:
    """Parse memory/knowledge/*.md and score against user message."""
    knowledge_dir = workspace_dir / "memory" / "knowledge"
    if not knowledge_dir.is_dir():
        return []

    hits: list[KnowledgeHit] = []
    for md_file in sorted(knowledge_dir.glob("*.md")):
        entries = _parse_knowledge_file(md_file)
        for entry in entries:
            score = _score_hit(entry, user_message, task_type_hint)
            entry.score = score
            hits.append(entry)

    return hits


def _parse_knowledge_file(path: Path) -> list[KnowledgeHit]:
    """
    Parse a knowledge markdown file into KnowledgeHit entries.

    Expected format per entry (YAML-like or Obsidian-style frontmatter):
        ## Title

        type: customer_development
        domain: business
        entities: Brazil, CNPJ
        tags: brazil, procurement
        links: [[巴西教育采购客户开发]]
        confidence: high

        Summary:
        ...

        Evidence:
        - ...

        Use when:
        - ...
    """
    text = path.read_text(encoding="utf-8")
    entries: list[KnowledgeHit] = []

    # Split on ## headings
    sections = re.split(r"^##\s+", text, flags=re.MULTILINE)

    for section in sections[1:]:  # skip preamble before first ##
        lines = section.strip().split("\n")
        if not lines:
            continue

        title = lines[0].strip()
        body = "\n".join(lines[1:])

        # Extract YAML-like front matter (type/entities/confidence/updated).
        # Supports both plain key lines and an optional `---` frontmatter block
        # under each `##` heading, Obsidian-style.
        entry_type = "general"
        domain = ""
        entities: list[str] = []
        tags: list[str] = []
        links: list[str] = []
        confidence = "medium"
        _KNOWN_KEYS = {
            "type:",
            "domain:",
            "entities:",
            "tags:",
            "links:",
            "confidence:",
            "updated:",
            "source:",
        }

        in_frontmatter = False
        for line in lines[1:]:
            stripped = line.strip()
            if not stripped:
                continue  # skip blank lines between keys
            if stripped == "---":
                in_frontmatter = not in_frontmatter
                continue
            if in_frontmatter or any(stripped.startswith(k) for k in _KNOWN_KEYS):
                if stripped.startswith("type:"):
                    entry_type = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("domain:"):
                    domain = stripped.split(":", 1)[1].strip()
                elif stripped.startswith("entities:"):
                    raw = stripped.split(":", 1)[1].strip()
                    entities = _parse_csv_or_list(raw)
                elif stripped.startswith("tags:"):
                    raw = stripped.split(":", 1)[1].strip()
                    tags = _parse_csv_or_list(raw)
                elif stripped.startswith("links:"):
                    raw = stripped.split(":", 1)[1].strip()
                    links = _parse_csv_or_list(raw)
                elif stripped.startswith("confidence:"):
                    confidence = stripped.split(":", 1)[1].strip()
                # updated: ignored
            else:
                break  # hit body content (Summary:, Use when:, etc.)

        # Extract Summary
        summary = ""
        summary_match = re.search(
            r"Summary:\s*\n(.*?)(?=\n(?:Use when|Details|##)|\Z)",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if summary_match:
            summary = summary_match.group(1).strip()

        # Extract Use when
        use_when = ""
        use_when_match = re.search(
            r"Use when:\s*\n(.*?)(?=\n(?:Details|Summary|##)|\Z)",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if use_when_match:
            use_when = use_when_match.group(1).strip()

        combined = summary
        if use_when:
            combined = f"{combined}\nUse when: {use_when}" if combined else use_when

        evidence = ""
        evidence_match = re.search(
            r"Evidence:\s*\n(.*?)(?=\n(?:Use when|Details|Summary|##)|\Z)",
            body,
            re.DOTALL | re.IGNORECASE,
        )
        if evidence_match:
            evidence = evidence_match.group(1).strip()
        if evidence:
            combined = (
                f"{combined}\nEvidence: {evidence}"
                if combined
                else f"Evidence: {evidence}"
            )

        if title:
            entries.append(
                KnowledgeHit(
                    source="knowledge",
                    title=title,
                    summary=combined or title,
                    score=0.0,  # will be scored later
                    type=entry_type,
                    entities=entities,
                    confidence=confidence,
                    domain=domain,
                    tags=tags,
                    links=links,
                ),
            )

    return entries


def _parse_csv_or_list(raw: str) -> list[str]:
    """Parse `a, b`, `[a, b]`, or `[[A]], [[B]]` into clean strings."""
    value = raw.strip()
    if value.startswith("[") and value.endswith("]") and "[[" not in value:
        value = value[1:-1]
    items = [item.strip().strip('"').strip("'") for item in value.split(",")]
    return [item for item in items if item]


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score_hit(
    hit: KnowledgeHit,
    user_message: str,
    task_type_hint: str,
) -> float:
    """Score a knowledge hit against user message. Returns 0-1."""
    score = 0.0
    msg_lower = user_message.lower()

    # 1. Type match: +0.15
    if task_type_hint and hit.type and hit.type == task_type_hint:
        score += 0.15
    elif hit.type and hit.type != "general" and hit.type in msg_lower:
        score += 0.15

    # 2. Title overlap: +0.25
    #    Check if meaningful substrings from title appear in message.
    title_lower = hit.title.lower()
    title_tokens = _extract_tokens(title_lower)
    if title_tokens:
        title_hits = sum(1 for t in title_tokens if t in msg_lower)
        # Strong match if 2+ tokens or >30% match
        if title_hits >= 2:
            score += 0.25
        elif title_hits >= 1 and title_hits / len(title_tokens) >= 0.2:
            score += 0.15

    # 3. Entity overlap: +0.25
    if hit.entities:
        matched = 0
        for entity in hit.entities:
            entity_lower = entity.lower().strip()
            if not entity_lower:
                continue
            if entity_lower in msg_lower:
                matched += 1
            else:
                # Partial match via sub-segments
                segs = re.findall(r"[a-zA-Z]{2,}|[\u4e00-\u9fff]{1,}", entity_lower)
                if any(s in msg_lower for s in segs if len(s) >= 2):
                    matched += 0.5
        if matched >= 1:
            score += 0.15
        if matched / len(hit.entities) >= 0.5:
            score += 0.10  # bonus for strong entity overlap

    # 4. Summary/Use-when keyword overlap: +0.15
    summary_lower = hit.summary.lower()
    msg_tokens = _extract_tokens(msg_lower)
    if msg_tokens:
        kw_hits = sum(1 for t in msg_tokens if t in summary_lower)
        kw_ratio = kw_hits / len(msg_tokens)
        if kw_ratio >= 0.3:
            score += 0.15
        elif kw_ratio >= 0.15:
            score += 0.08

    # 5. Tag/domain/link overlap: +0.10
    linked_terms = " ".join([hit.domain, *hit.tags, *hit.links]).lower()
    if linked_terms and msg_tokens:
        link_hits = sum(1 for t in msg_tokens if t in linked_terms)
        if link_hits >= 2:
            score += 0.10
        elif link_hits >= 1:
            score += 0.05

    # 6. Confidence bonus
    if hit.confidence == "high":
        score += 0.1
    elif hit.confidence == "medium":
        score += 0.05

    return min(score, 1.0)


def _is_complex_task(user_message: str) -> bool:
    """Heuristic: is this a complex task that warrants more guidance budget?"""
    if len(user_message) >= _COMPLEX_MSG_CHAR_THRESHOLD:
        return True
    if _COMPLEX_TASK_PATTERNS.search(user_message):
        return True
    if _STEP_CONNECTOR_PATTERNS.search(user_message):
        return True
    return False


def _extract_tokens(text: str) -> list[str]:
    """Extract meaningful tokens from text for matching.

    Returns English words (3+ chars) and Chinese 2-char bigrams.
    """
    tokens: list[str] = []
    for w in re.findall(r"[a-zA-Z]{3,}", text):
        tokens.append(w)
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        tokens.append(run)
        for i in range(len(run) - 1):
            tokens.append(run[i : i + 2])
    return tokens


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def _format_hits(hits: list[KnowledgeHit], max_tokens: int) -> str:
    """Format hits into injection text within token budget."""
    # Rough token estimate: 1 token ≈ 4 chars (mixed CN/EN)
    max_chars = max_tokens * 4

    header = "📌 Relevant guidance（参考以下经验和知识执行，不要逐字复述）:\n"
    result = header

    for i, hit in enumerate(hits, 1):
        entry = (
            f"[{i}] [{hit.source}] {hit.title} (score: {hit.score:.2f})\n"
            f"    {hit.summary}\n"
        )
        # Check if adding this entry exceeds budget
        if len(result) + len(entry) > max_chars:
            # Truncate this entry to fit
            remaining = max_chars - len(result) - 20
            if remaining > 50:
                truncated = hit.summary[:remaining]
                result += (
                    f"[{i}] [{hit.source}] {hit.title} (score: {hit.score:.2f})\n"
                    f"    {truncated}...\n"
                )
            break
        result += entry

    return result.rstrip()
