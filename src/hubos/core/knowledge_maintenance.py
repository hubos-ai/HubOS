# -*- coding: utf-8 -*-
"""Knowledge maintenance helpers.

This module writes *candidate* factual knowledge extracted during Work
Experience reflection. It intentionally does not merge candidates into the
formal `memory/knowledge/*.md` files; a scheduled maintenance job should review
and merge them later.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class KnowledgeCandidate:
    """A pending factual knowledge item."""

    title: str
    summary: str
    domain: str = "general"
    type: str = "fact"
    entities: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    confidence: str = "medium"
    evidence: list[str] = field(default_factory=list)
    use_when: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    source_session_id: str = ""
    source_agent_id: str = ""
    created_at: str = ""


_ALLOWED_DOMAINS = {"business", "tools", "dev", "ui", "system", "general"}
_ALLOWED_CONFIDENCE = {"high", "medium", "low"}
_SENSITIVE_PATTERNS = re.compile(
    r"(?i)(api[_-]?key|secret|token|password|passwd|cookie|authorization|bearer|sk-[a-z0-9]|AKIA[0-9A-Z]{16})"
)


def write_pending_candidates(
    candidates: list[dict[str, Any] | KnowledgeCandidate],
    *,
    workspace_dir: str | Path,
    session_id: str = "",
    agent_id: str = "default",
) -> list[Path]:
    """Sanitize and write candidate markdown files.

    Returns written file paths. Invalid/sensitive candidates are skipped.
    """
    written: list[Path] = []
    for raw in candidates:
        candidate = sanitize_candidate(raw, session_id=session_id, agent_id=agent_id)
        if candidate is None:
            continue
        pending_dir = Path(workspace_dir) / "memory" / "knowledge_pending"
        pending_dir.mkdir(parents=True, exist_ok=True)
        slug = _slugify(candidate.title) or "knowledge-candidate"
        path = pending_dir / f"{_today()}-{slug}-{uuid.uuid4().hex[:8]}.md"
        path.write_text(format_candidate(candidate), encoding="utf-8")
        written.append(path)
    return written


def sanitize_candidate(
    raw: dict[str, Any] | KnowledgeCandidate,
    *,
    session_id: str = "",
    agent_id: str = "default",
) -> KnowledgeCandidate | None:
    """Validate, normalize and redact a candidate."""
    data = raw.__dict__ if isinstance(raw, KnowledgeCandidate) else dict(raw or {})
    title = _clean_str(data.get("title", ""))[:80]
    summary = _clean_str(data.get("summary", ""))[:600]
    if not title or not summary:
        return None

    combined = "\n".join(str(v) for v in data.values())
    if _SENSITIVE_PATTERNS.search(combined):
        return None

    domain = _clean_str(data.get("domain", "general")) or "general"
    if domain not in _ALLOWED_DOMAINS:
        domain = "general"
    confidence = _clean_str(data.get("confidence", "medium")) or "medium"
    if confidence not in _ALLOWED_CONFIDENCE:
        confidence = "medium"

    return KnowledgeCandidate(
        title=title,
        summary=summary,
        domain=domain,
        type=_clean_str(data.get("type", "fact")) or "fact",
        entities=_clean_list(data.get("entities", []), limit=8),
        tags=_clean_list(data.get("tags", []), limit=10),
        links=_clean_list(data.get("links", []), limit=8),
        confidence=confidence,
        evidence=_clean_list(data.get("evidence", []), limit=6),
        use_when=_clean_list(data.get("use_when", data.get("use when", [])), limit=6),
        details=_clean_list(data.get("details", []), limit=8),
        source_session_id=session_id or _clean_str(data.get("source_session_id", "")),
        source_agent_id=agent_id or _clean_str(data.get("source_agent_id", "")),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def format_candidate(candidate: KnowledgeCandidate) -> str:
    """Format a candidate as Obsidian-like markdown."""
    links = ", ".join(candidate.links)
    lines = [
        f"## {candidate.title}",
        "---",
        f"type: {candidate.type}",
        f"domain: {candidate.domain}",
        f"entities: {', '.join(candidate.entities)}",
        f"tags: {', '.join(candidate.tags)}",
        f"links: {links}",
        f"confidence: {candidate.confidence}",
        f"created: {candidate.created_at}",
        f"source_session: {candidate.source_session_id}",
        f"source_agent: {candidate.source_agent_id}",
        "status: pending",
        "---",
        "",
        "Summary:",
        candidate.summary,
        "",
    ]
    if candidate.evidence:
        lines.extend(["Evidence:", *[f"- {item}" for item in candidate.evidence], ""])
    if candidate.use_when:
        lines.extend(["Use when:", *[f"- {item}" for item in candidate.use_when], ""])
    if candidate.details:
        lines.extend(["Details:", *[f"- {item}" for item in candidate.details], ""])
    return "\n".join(lines).rstrip() + "\n"


def _clean_str(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_list(value: Any, *, limit: int) -> list[str]:
    if isinstance(value, str):
        items = [v.strip() for v in value.split(",")]
    elif isinstance(value, list):
        items = [_clean_str(v) for v in value]
    else:
        items = []
    result: list[str] = []
    for item in items:
        if item and item not in result and not _SENSITIVE_PATTERNS.search(item):
            result.append(item[:160])
    return result[:limit]


def _slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\u4e00-\u9fff-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60]


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
