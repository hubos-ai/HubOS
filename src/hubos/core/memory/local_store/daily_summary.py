"""Daily summary generator for the local memory store.

Walks today's session metadata + messages, extracts decisions / projects /
preferences / todos / context with simple keyword heuristics, and writes a
human-readable markdown digest under ``daily/YYYY-MM-DD.md``. Intended as a
deterministic baseline; an LLM-based refinement step can plug in later.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

from hubos.core.memory.local_store.store import LocalMemoryStore, get_memory_root


class DailySummaryGenerator:
    """Generate a daily markdown digest from sessions in a memory store."""

    def __init__(self, store: Optional[LocalMemoryStore] = None) -> None:
        self.store = store or LocalMemoryStore()

    @property
    def sessions_dir(self) -> Path:
        return self.store.sessions_dir

    @property
    def daily_dir(self) -> Path:
        return self.store.daily_dir

    def generate(self, target_date: Optional[str] = None) -> str:
        if target_date is None:
            target_date = date.today().isoformat()

        sessions = self._get_sessions_for_date(target_date)
        stats = self._compute_stats(sessions)
        decisions = self._extract_decisions(sessions)
        projects = self._extract_projects(sessions)
        preferences = self._extract_preferences(sessions)
        todos = self._extract_todos(sessions)
        context = self._extract_context(sessions)

        lines: List[str] = [
            f"# {target_date} 日志摘要",
            "",
            "## 会话统计",
            f"- 会话数：{stats['session_count']}",
            f"- 总消息：{stats['message_count']}",
            f"- 总工具调用：{stats['tool_call_count']}",
            f"- 消耗 tokens：{self._format_tokens(stats['total_tokens'])}",
            "",
            "## 重要决策",
        ]
        if decisions:
            for d in decisions:
                lines.append(f"- {d}")
        else:
            lines.append("- （暂无）")

        lines.extend(["", "## 项目进展"])
        if projects:
            for p in projects:
                lines.append(f"- **{p['name']}**：{p['status']}")
        else:
            lines.append("- （暂无）")

        lines.extend(["", "## 用户偏好"])
        if preferences:
            for pref in preferences:
                lines.append(f"- {pref}")
        else:
            lines.append("- （暂无）")

        lines.extend(["", "## 待处理"])
        if todos:
            for t in todos:
                lines.append(f"- [ ] {t}")
        else:
            lines.append("- （暂无）")

        lines.extend(["", "## 关键上下文"])
        if context:
            for c in context:
                lines.append(f"- {c}")
        else:
            lines.append("- （暂无）")

        return "\n".join(lines)

    def save(self, target_date: Optional[str] = None) -> str:
        content = self.generate(target_date)
        target_date = target_date or date.today().isoformat()
        path = self.daily_dir / f"{target_date}.md"
        path.write_text(content, encoding="utf-8")
        return content

    # ─── extractors ────────────────────────────────────────────────────

    def _get_sessions_for_date(self, target_date: str) -> List[Dict[str, Any]]:
        sessions: List[Dict[str, Any]] = []
        if not self.sessions_dir.exists():
            return sessions
        for session_dir in self.sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue
            metadata_path = session_dir / "metadata.json"
            if not metadata_path.exists():
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                started = metadata.get("started_at", "")[:10]
                if started == target_date:
                    messages: List[Dict[str, Any]] = []
                    msg_path = session_dir / "messages.jsonl"
                    if msg_path.exists():
                        with msg_path.open(encoding="utf-8") as f:
                            messages = [json.loads(line) for line in f if line.strip()]
                    sessions.append({"metadata": metadata, "messages": messages})
            except (json.JSONDecodeError, OSError):
                continue
        return sessions

    def _compute_stats(self, sessions: List[Dict[str, Any]]) -> Dict[str, int]:
        stats = {
            "session_count": len(sessions),
            "message_count": 0,
            "tool_call_count": 0,
            "total_tokens": 0,
        }
        for s in sessions:
            m = s["metadata"]
            stats["message_count"] += m.get("message_count", 0)
            stats["tool_call_count"] += m.get("tool_call_count", 0)
            stats["total_tokens"] += m.get("input_tokens", 0) + m.get("output_tokens", 0)
        return stats

    def _extract_decisions(self, sessions: List[Dict[str, Any]]) -> List[str]:
        decisions: List[str] = []
        for s in sessions:
            for msg in s.get("messages", []):
                content = msg.get("content", "")
                if "决定" in content or "确认" in content or "采用" in content:
                    if len(content) < 200:
                        decisions.append(content.strip())
        return list(dict.fromkeys(decisions))[:10]

    def _extract_projects(self, sessions: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        projects: List[Dict[str, str]] = []
        for s in sessions:
            tags = s["metadata"].get("tags", [])
            for tag in tags:
                if "项目" in tag or "project" in tag.lower():
                    projects.append({"name": tag, "status": "进行中"})
        return projects[:10]

    def _extract_preferences(self, sessions: List[Dict[str, Any]]) -> List[str]:
        prefs: List[str] = []
        for s in sessions:
            for msg in s.get("messages", []):
                content = msg.get("content", "")
                if "偏好" in content or "喜欢" in content or "要求" in content:
                    if len(content) < 150:
                        prefs.append(content.strip())
        return list(dict.fromkeys(prefs))[:5]

    def _extract_todos(self, sessions: List[Dict[str, Any]]) -> List[str]:
        todos: List[str] = []
        for s in sessions:
            for msg in s.get("messages", []):
                content = msg.get("content", "")
                if "待处理" in content or "TODO" in content or "[ ]" in content:
                    todos.append(content.strip()[:100])
        return list(dict.fromkeys(todos))[:10]

    def _extract_context(self, sessions: List[Dict[str, Any]]) -> List[str]:
        contexts: List[str] = []
        for s in sessions:
            title = s["metadata"].get("title", "")
            if title:
                contexts.append(f"会话主题：{title}")
        return contexts[:5]

    @staticmethod
    def _format_tokens(tokens: int) -> str:
        if tokens >= 1_000_000:
            return f"{tokens / 1_000_000:.1f}M"
        if tokens >= 1_000:
            return f"{tokens / 1_000:.1f}K"
        return str(tokens)


if __name__ == "__main__":
    gen = DailySummaryGenerator()
    print(gen.generate())
    print(f"\n(Memory root: {get_memory_root()})")
