# -*- coding: utf-8 -*-
"""Tool for sub-agents to retrieve relevant context from their parent session.

When a sub-agent is spawned via spawn_subagents / coordinate_workflow, it
receives a ``parent_session_id`` in its context.  This tool lets the sub-agent
query the parent's conversation history on demand, so it does not need to
re-discover information that the parent has already processed.

The search is deliberately simple (keyword-based with optional semantic boost)
to avoid adding heavy dependencies.  It runs purely on the session JSON file.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximum characters per returned snippet to keep the tool output manageable.
_MAX_SNIPPET_CHARS = 2000
# Maximum total characters across all returned snippets.
_MAX_TOTAL_CHARS = 6000


def _extract_text_from_content(content: Any) -> str:
    """Extract plain text from an agentscope Msg content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                # For tool_result blocks, extract the output text
                elif block.get("type") == "tool_result":
                    output = block.get("output", [])
                    if isinstance(output, list):
                        for o in output:
                            if isinstance(o, dict) and o.get("type") == "text":
                                parts.append(o.get("text", ""))
                    elif isinstance(output, str):
                        parts.append(output)
                # For tool_use blocks, show name + input summary
                elif block.get("type") == "tool_use":
                    name = block.get("name", "")
                    inp = block.get("input", {})
                    inp_str = json.dumps(inp, ensure_ascii=False)[:200]
                    parts.append(f"[tool_use: {name}] {inp_str}")
        return "\n".join(parts)
    return str(content) if content else ""


def _load_parent_messages(
    parent_session_id: str,
    sessions_dir: Path,
) -> list[dict[str, Any]]:
    """Load messages from the parent session file.

    Returns a list of dicts with keys: role, name, text, timestamp.
    """
    # Session filename follows SafeJSONSession._get_save_path convention:
    # {agent_id}_{session_id}.json — but the parent session is for the
    # *default* agent in the default workspace.
    # We need to find the right file.  The session_id we have is the raw
    # session_id, and the file is named with sanitized version.
    from ..app.runner.session import sanitize_filename

    # Try exact match patterns
    candidates = list(sessions_dir.glob("*.json"))
    for candidate in candidates:
        # File format: {agent_id}_{session_id}.json
        # We match by checking if the filename contains the session_id
        safe_sid = sanitize_filename(parent_session_id)
        if safe_sid in candidate.name:
            try:
                with open(candidate) as f:
                    data = json.load(f)
                agent_data = data.get("agent", {})
                memory = agent_data.get("memory", {})
                raw_content = memory.get("content", [])
                messages: list[dict[str, Any]] = []
                compressed_summary = (
                    memory.get("_compressed_summary")
                    or memory.get("compressed_summary")
                    or ""
                )
                if compressed_summary:
                    messages.append(
                        {
                            "role": "system",
                            "name": "compressed_summary",
                            "text": str(compressed_summary),
                            "timestamp": "",
                        },
                    )
                for item in raw_content:
                    # Each item is [msg_dict, marks_list]
                    if isinstance(item, (list, tuple)) and len(item) >= 1:
                        msg = item[0]
                    elif isinstance(item, dict):
                        msg = item
                    else:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    role = msg.get("role", "")
                    name = msg.get("name", "")
                    content = msg.get("content", "")
                    timestamp = msg.get("timestamp", "")
                    text = _extract_text_from_content(content)
                    if text.strip():
                        messages.append(
                            {
                                "role": role,
                                "name": name,
                                "text": text,
                                "timestamp": timestamp,
                            },
                        )
                return messages
            except Exception:
                logger.warning(
                    "Failed to load parent session %s from %s",
                    parent_session_id,
                    candidate,
                    exc_info=True,
                )

    # Fallback: try compressed summary if available
    logger.debug(
        "Parent session file not found for %s in %s",
        parent_session_id,
        sessions_dir,
    )
    return []


def _keyword_search(
    messages: list[dict[str, Any]],
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Simple keyword-based search over messages.

    Returns the top_k most relevant message snippets.  Relevance is based on
    the number of query keywords found in the message text.
    """
    # Tokenize query into keywords (split on whitespace, lower-case)
    keywords = [
        w.lower() for w in re.split(r"[\s,;:.!?/\\]+", query) if len(w) >= 2
    ]
    if not keywords:
        # If no meaningful keywords, return last few assistant messages
        assistant_msgs = [m for m in messages if m["role"] == "assistant"]
        return assistant_msgs[-top_k:]

    scored: list[tuple[int, dict[str, Any]]] = []
    for msg in messages:
        text_lower = msg["text"].lower()
        hits = sum(1 for kw in keywords if kw in text_lower)
        if hits > 0:
            scored.append((hits, msg))

    # Sort by hit count descending, take top_k
    scored.sort(key=lambda x: x[0], reverse=True)
    return [msg for _, msg in scored[:top_k]]


def recall_parent_context(
    query: str,
    top_k: int = 5,
) -> str:
    """Search the parent agent's conversation history for relevant context.

    Use this when you need information that the parent agent (HubOS) already
    knows from its current conversation — file contents, analysis results,
    technical conclusions, etc.  This avoids re-reading files or re-testing
    APIs.

    Args:
        query: Search keywords describing what you need (e.g.
            "stock_live.py functions and factor list", "akshare test results").
        top_k: Maximum number of relevant message snippets to return.

    Returns:
        Relevant excerpts from the parent conversation, or a message explaining
        why no context was found.
    """
    from ..config.context import current_runtime_context

    ctx = current_runtime_context.get(None)
    if ctx is None:
        return (
            "Error: No runtime context available. "
            "This tool only works for sub-agents."
        )

    parent_session_id = ctx.get("parent_session_id", "")
    if not parent_session_id:
        return (
            "Error: No parent_session_id found. "
            "This tool only works for sub-agents spawned from a parent."
        )

    workspace_dir = ctx.get("workspace_dir", "")
    if not workspace_dir:
        from ..constant import WORKING_DIR

        workspace_dir = str(WORKING_DIR)

    sessions_dir = Path(workspace_dir) / "sessions"
    if not sessions_dir.exists():
        return (
            f"No sessions directory found at {sessions_dir}. "
            "Parent context is not available."
        )

    messages = _load_parent_messages(parent_session_id, sessions_dir)
    if not messages:
        return (
            f"No messages found for parent session {parent_session_id}. "
            "The parent may not have a saved session yet."
        )

    results = _keyword_search(messages, query, top_k=top_k)
    if not results:
        return (
            f"No relevant messages found for query: '{query}'. "
            f"Parent session has {len(messages)} messages but none matched."
        )

    # Format results
    total_chars = 0
    output_parts: list[str] = []
    for i, msg in enumerate(results):
        role = msg["role"]
        text = msg["text"]

        # Truncate individual snippet if needed
        if len(text) > _MAX_SNIPPET_CHARS:
            text = text[:_MAX_SNIPPET_CHARS] + "\n...[truncated]"

        snippet = f"--- [{role}] ---\n{text}"
        if total_chars + len(snippet) > _MAX_TOTAL_CHARS:
            remaining = _MAX_TOTAL_CHARS - total_chars
            if remaining > 200:
                snippet = snippet[:remaining] + "\n...[truncated]"
                output_parts.append(snippet)
            break
        output_parts.append(snippet)
        total_chars += len(snippet)

    if not output_parts:
        return "No relevant context found within size limits."

    header = (
        f"Parent context for query '{query}' "
        f"({len(results)} results from {len(messages)} messages):\n\n"
    )
    return header + "\n\n".join(output_parts)


def create_parent_context_tool(
    parent_session_id: str,
    workspace_dir: str | None = None,
):
    """Create a recall_parent_context function bound to specific parameters.

    This is used to register the tool with the correct parent_session_id
    baked in, so the sub-agent doesn't need to know it.
    """
    from ..constant import WORKING_DIR

    ws_dir = workspace_dir or str(WORKING_DIR)

    def _recall_parent_context(
        query: str,
        top_k: int = 5,
    ) -> str:
        """Search the parent agent's conversation history for relevant context.

        Use this when you need information that the parent agent already
        knows from its conversation — file contents, analysis results,
        technical conclusions, architecture understanding, etc.

        This helps you avoid re-reading files or re-discovering information
        that the parent has already processed.

        Args:
            query: Search keywords describing what you need. Examples:
                - "stock_live.py functions and factor definitions"
                - "akshare API test results"
                - "database schema for stock_history_v2"
                - "error messages about httpx timeout"
            top_k: Maximum number of relevant message snippets to return.
                Default is 5.
        """
        sessions_dir = Path(ws_dir) / "sessions"
        if not sessions_dir.exists():
            return f"No sessions directory found at {sessions_dir}."

        messages = _load_parent_messages(
            parent_session_id,
            sessions_dir,
        )
        if not messages:
            return (
                f"No messages found for parent session "
                f"{parent_session_id}."
            )

        results = _keyword_search(messages, query, top_k=top_k)
        if not results:
            return (
                f"No relevant messages found for query: '{query}'. "
                f"Parent session has {len(messages)} messages."
            )

        # Format results
        total_chars = 0
        output_parts: list[str] = []
        for msg in results:
            role = msg["role"]
            text = msg["text"]
            if len(text) > _MAX_SNIPPET_CHARS:
                text = text[:_MAX_SNIPPET_CHARS] + "\n...[truncated]"

            snippet = f"--- [{role}] ---\n{text}"
            if total_chars + len(snippet) > _MAX_TOTAL_CHARS:
                remaining = _MAX_TOTAL_CHARS - total_chars
                if remaining > 200:
                    output_parts.append(
                        snippet[:remaining] + "\n...[truncated]",
                    )
                break
            output_parts.append(snippet)
            total_chars += len(snippet)

        if not output_parts:
            return "No relevant context found within size limits."

        header = (
            f"Parent context for '{query}' "
            f"({len(results)} of {len(messages)} messages):\n\n"
        )
        return header + "\n\n".join(output_parts)

    # Preserve function metadata for tool registration
    _recall_parent_context.__name__ = "recall_parent_context"
    _recall_parent_context.__doc__ = (
        "Search the parent agent's conversation history for relevant "
        "context. Use this to access information the parent agent already "
        "knows — file contents, analysis results, technical conclusions. "
        "This avoids re-reading files or re-discovering information.\n\n"
        "Args:\n"
        "    query: Search keywords (e.g. 'stock_live.py functions', "
        "'akshare test results', 'database schema').\n"
        "    top_k: Max snippets to return (default 5).\n\n"
        "Returns:\n"
        "    Relevant excerpts from the parent conversation."
    )
    return _recall_parent_context


# ---------------------------------------------------------------------------
# Auto-briefing: inject relevant context when spawning sub-agents
# ---------------------------------------------------------------------------

# Maximum characters for the auto-briefing section.
_BRIEFING_MAX_CHARS = 3000


def auto_briefing(
    prompt: str,
    parent_session_id: str,
    workspace_dir: str,
    handoff: dict[str, Any] | None = None,
) -> str:
    """Prepend a brief summary of relevant parent context to the task prompt.

    This is called by ``host_agent_runner`` when a sub-agent is spawned.
    It searches the parent's conversation for messages relevant to the task
    prompt, extracts key information, and prepends it as a "briefing" section.

    The briefing is intentionally small (capped at ``_BRIEFING_MAX_CHARS``)
    so it doesn't bloat the sub-agent's initial context.

    Args:
        prompt: The original task prompt from the parent.
        parent_session_id: Session ID of the parent conversation.
        workspace_dir: Path to the workspace containing session files.

    Returns:
        The prompt with a prepended briefing section, or the original
        prompt unchanged if no relevant context is found.
    """
    handoff_section = _format_handoff_section(prompt, handoff)
    sessions_dir = Path(workspace_dir) / "sessions"
    if not sessions_dir.exists():
        return handoff_section + prompt

    messages = _load_parent_messages(parent_session_id, sessions_dir)
    if not messages:
        return handoff_section + prompt

    # Use the task prompt itself as the search query to find relevant context
    results = _keyword_search(messages, prompt, top_k=5)
    if not results:
        return handoff_section + prompt

    # Build briefing from search results
    briefing_parts: list[str] = []
    total_chars = 0
    for msg in results:
        role = msg["role"]
        text = msg["text"]

        # Truncate individual snippets aggressively for briefing
        if len(text) > 600:
            text = text[:600] + "..."

        snippet = f"[{role}]: {text}"
        if total_chars + len(snippet) > _BRIEFING_MAX_CHARS:
            remaining = _BRIEFING_MAX_CHARS - total_chars
            if remaining > 100:
                briefing_parts.append(snippet[:remaining] + "...")
            break
        briefing_parts.append(snippet)
        total_chars += len(snippet)

    if not briefing_parts:
        return handoff_section + prompt

    briefing = (
        "## 父会话上下文（自动注入）\n\n"
        "以下是父 agent 会话中与此任务相关的上下文摘要，供你参考：\n\n"
        + "\n\n".join(briefing_parts)
        + (
            "\n\n> 你也可以随时调用 `recall_parent_context(query)` "
            "工具搜索更多上下文。\n\n"
            "---\n\n"
        )
    )
    return handoff_section + briefing + prompt


def _format_handoff_section(
    prompt: str,
    handoff: dict[str, Any] | None,
) -> str:
    """Render a bounded, explicit parent-to-child task packet."""
    if not isinstance(handoff, dict):
        return ""
    objective = str(handoff.get("objective") or prompt).strip()[:1500]
    known_context = str(handoff.get("known_context") or "").strip()[:2500]
    constraints = [
        str(item).strip()[:500]
        for item in (handoff.get("constraints") or [])
        if str(item).strip()
    ][:12]
    artifacts = [
        str(item).strip()[:500]
        for item in (handoff.get("artifacts") or [])
        if str(item).strip()
    ][:12]

    parts = ["## 父代理交接包", f"目标：{objective}"]
    if known_context:
        parts.append(f"已知上下文：{known_context}")
    if constraints:
        parts.append("约束：\n" + "\n".join(f"- {item}" for item in constraints))
    if artifacts:
        parts.append("相关产物：\n" + "\n".join(f"- {item}" for item in artifacts))
    parts.append(
        "交接规则：优先使用以上信息；缺少关键事实时再调用 "
        "`recall_parent_context(query)`，不要重新执行已经完成的工作。",
    )
    return "\n\n".join(parts) + "\n\n---\n\n"
