# -*- coding: utf-8 -*-
"""Work Experience v4 — WorkflowCard schema.

One card per task type. Updated, never duplicated.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slugify(text: str) -> str:
    """Create a URL-safe slug from text."""
    text = text.lower().strip()
    # Chinese chars → pinyin approximation: keep as-is for readability
    text = re.sub(r"[^\w\u4e00-\u9fff-]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:60]


@dataclass
class WorkflowCard:
    """
    A living experience card for a specific task type.

    Design principles:
    - One card per task type — updated after each completion, never duplicated
    - Contains workflow, tools, pitfalls, success patterns
    - Grows more comprehensive with each execution
    """

    # Identity
    card_id: str = ""  # slug: "gov-procurement-supplier"
    task_type: str = ""  # human-readable: "政府采购供应商开发"
    description: str = ""  # one-line summary

    # Core content
    workflow: list[str] = field(default_factory=list)  # ordered steps
    tools: dict[str, str] = field(
        default_factory=dict,
    )  # tool_name -> usage notes
    pitfalls: list[str] = field(
        default_factory=list,
    )  # known problems to avoid
    success_patterns: list[str] = field(
        default_factory=list,
    )  # what works well

    # Classification
    experience_type: str = "general"  # category tag
    entities: list[str] = field(default_factory=list)  # key entities

    # Traceability — link back to originating session
    ref_session_id: str = ""  # first session that created this card
    ref_agent_id: str = ""  # agent that created this card
    last_ref_session_id: str = ""  # most recent session that updated this card
    source_turn_count: int = 0  # turns processed when card was last updated

    # Metadata
    executions: int = 0
    last_executed_at: str = ""
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    source_sessions: list[str] = field(default_factory=list)

    # Admin/governance state. These mirror the legacy UI fields so the
    # Work Experience page can manage v4 cards without falling back to v3.
    status: str = "approved"  # candidate, approved, rejected, archived
    experience_level: str = "mature"  # new, observed, mature, deprecated
    disabled: bool = False

    def __post_init__(self) -> None:
        # Sanitize entities: list[str], no empties, deduplicated, max 8
        clean: list[str] = []
        for e in self.entities:
            if isinstance(e, str) and e.strip() and e.strip() not in clean:
                clean.append(e.strip())
        self.entities = clean[:8]

        if not self.card_id and self.task_type:
            self.card_id = _slugify(self.task_type)
        if not self.created_at:
            self.created_at = _utcnow()
        if not self.updated_at:
            self.updated_at = _utcnow()

    # ---- Serialisation ----

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "task_type": self.task_type,
            "description": self.description,
            "workflow": self.workflow,
            "tools": self.tools,
            "pitfalls": self.pitfalls,
            "success_patterns": self.success_patterns,
            "experience_type": self.experience_type,
            "entities": self.entities,
            "ref_session_id": self.ref_session_id,
            "ref_agent_id": self.ref_agent_id,
            "last_ref_session_id": self.last_ref_session_id,
            "source_turn_count": self.source_turn_count,
            "executions": self.executions,
            "last_executed_at": self.last_executed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_sessions": self.source_sessions[-20:],  # keep last 20
            "status": self.status,
            "experience_level": self.experience_level,
            "disabled": self.disabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowCard:
        return cls(
            card_id=data.get("card_id", ""),
            task_type=data.get("task_type", ""),
            description=data.get("description", ""),
            workflow=data.get("workflow", []),
            tools=data.get("tools", {}),
            pitfalls=data.get("pitfalls", []),
            success_patterns=data.get("success_patterns", []),
            experience_type=data.get("experience_type", "general"),
            entities=data.get("entities", []),
            ref_session_id=data.get("ref_session_id", ""),
            ref_agent_id=data.get("ref_agent_id", ""),
            last_ref_session_id=data.get("last_ref_session_id", ""),
            source_turn_count=data.get("source_turn_count", 0),
            executions=data.get("executions", 0),
            last_executed_at=data.get("last_executed_at", ""),
            created_at=data.get("created_at", _utcnow()),
            updated_at=data.get("updated_at", _utcnow()),
            source_sessions=data.get("source_sessions", []),
            status=data.get("status", "approved"),
            experience_level=data.get("experience_level", "mature"),
            disabled=data.get("disabled", False),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_json(cls, text: str) -> WorkflowCard:
        return cls.from_dict(json.loads(text))

    # ---- Content helpers ----

    def formatted_for_injection(self) -> str:
        """Format card content for prompt injection."""
        parts = [f"📋 任务类型：{self.task_type}"]
        parts.append(f"   描述：{self.description}")

        if self.workflow:
            parts.append("\n🔧 标准工作流程:")
            for i, step in enumerate(self.workflow, 1):
                parts.append(f"   {i}. {step}")

        if self.tools:
            parts.append("\n⚡ 工具使用要点:")
            for tool, notes in self.tools.items():
                parts.append(f"   - {tool}: {notes}")

        if self.pitfalls:
            parts.append("\n❌ 已知踩坑:")
            for pit in self.pitfalls:
                parts.append(f"   - {pit}")

        if self.success_patterns:
            parts.append("\n✅ 成功经验:")
            for pat in self.success_patterns:
                parts.append(f"   - {pat}")

        parts.append(f"\n📊 已执行 {self.executions} 次")
        return "\n".join(parts)
