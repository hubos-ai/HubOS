# -*- coding: utf-8 -*-
"""Adapt this app's agent runtime as a ``HostAgentRunner`` for hubos.core.

This is the ONLY file that:
- knows how to obtain a ``Workspace`` from the host's ``MultiAgentManager``
- knows the host framework's ``Msg`` / ``AgentRequest`` shape
- pulls assistant text out of the host's streaming runner

It produces a closure matching ``hubos.core.workers.providers.host_agent.HostAgentRunner``::

    runner = build_host_agent_runner(workspace_provider)
    text = await runner(agent_id, prompt, context)

``hubos.core`` itself imports nothing from here — the wiring is done by the host
at startup time (see ``Coordinator(worker_registry={...})``).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Protocol

from ..config.context import (
    current_subagent_write_scope,
)
from ..constant import WORKING_DIR

logger = logging.getLogger(__name__)


DEFAULT_USER_ID = "_hubos_internal"
DEFAULT_CHANNEL = "hubos.core"

# Channels that mark the running agent as a sub-agent delegated from the GM.
# These come from agent_workforce (spawn_subagents / coordinate_workflow).
_SUBAGENT_CHANNELS = {"hubos_core_subagent", "hubos_core_workflow"}

# Filesystem-safe character class for deriving directory names from free-form
# labels / ids coming in from the GM (which may contain CJK, spaces, etc.).
_LABEL_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]+")


def _sanitize_label(value: str, fallback: str = "sub") -> str:
    if not value:
        return fallback
    cleaned = _LABEL_SAFE_RE.sub("_", value).strip("_.")
    return cleaned or fallback


# Identity / config files that sub-agents must NOT overwrite.
# These define the agent's personality and role — only the GM or human can change them.
_PROTECTED_FILES = frozenset(
    {
        "AGENTS.md",
        "SOUL.md",
        "PROFILE.md",
        "BOOTSTRAP.md",
        "agent.json",
        "skill.json",
        "skills.json",
    },
)


def _derive_subagent_scope(
    agent_id: str,
    context: dict[str, Any],
) -> Path | None:
    """Pick the write scope for this sub-agent run, or ``None`` for GM runs.

    Sub-agents may write anywhere under their **own** workspace root, except
    for protected identity files (AGENTS.md, PROFILE.md, SOUL.md, etc.).
    The actual permission check lives in ``file_io._resolve_write_path``.
    """
    channel = str(context.get("channel") or "")
    wf_id = context.get("workflow_id")
    _step_id = context.get("step_id")
    _label = context.get("label")
    parent_session = context.get("parent_session_id")

    is_subagent = (
        channel in _SUBAGENT_CHANNELS or bool(wf_id) or bool(parent_session)
    )
    if not is_subagent:
        return None

    # Allow writes to the agent's own workspace root (not just outputs/).
    # Protected files are blocked by _resolve_write_path in file_io.py.
    base = (WORKING_DIR / "workspaces" / agent_id).resolve()
    return base


def _audit_log_path(parent_session: str | None) -> Path:
    """Resolve the JSONL audit log path for this parent session.

    The directory is created lazily on first write in ``_write_audit`` so
    callers in restricted environments (e.g. sandboxed tests) can still
    derive the path without filesystem side-effects.
    """
    root = WORKING_DIR / "audit" / "subagents"
    name = (
        _sanitize_label(parent_session or "unknown", fallback="unknown")
        + ".jsonl"
    )
    return root / name


def _extract_tool_use_blocks(msg: Any) -> Iterable[dict[str, Any]]:
    """Pull any ``tool_use`` blocks out of an agentscope ``Msg`` / dict."""
    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")
    if not isinstance(content, list):
        return []
    out: list[dict[str, Any]] = []
    for block in content:
        btype = getattr(block, "type", None) or (
            block.get("type") if isinstance(block, dict) else None
        )
        if btype != "tool_use":
            continue
        name = getattr(block, "name", None) or (
            block.get("name") if isinstance(block, dict) else None
        )
        inp = getattr(block, "input", None) or (
            block.get("input") if isinstance(block, dict) else None
        )
        tid = getattr(block, "id", None) or (
            block.get("id") if isinstance(block, dict) else None
        )
        out.append({"tool_id": tid, "name": name, "input": inp})
    return out


def _write_audit(path: Path, record: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str))
            f.write("\n")
    except Exception as e:  # noqa: BLE001
        logger.warning("sub-agent audit write failed: %s", e)


class _AgentWorkspaceLike(Protocol):
    """Minimal contract a workspace must satisfy for this adapter."""

    @property
    def runner(self) -> Any:  # pragma: no cover - protocol stub
        ...


WorkspaceProvider = Callable[[str], Awaitable[_AgentWorkspaceLike]]


def build_host_agent_runner(
    workspace_provider: WorkspaceProvider,
) -> Callable[[str, str, dict[str, Any]], Awaitable[str]]:
    """Build a ``HostAgentRunner`` closure backed by ``workspace_provider``.

    Args:
        workspace_provider: async callable that returns a started Workspace
            for a given agent_id (typically ``MultiAgentManager.get_agent``).

    Returns:
        ``async (agent_id, prompt, context) -> response_text`` matching
        :data:`hubos.core.workers.providers.host_agent.HostAgentRunner`.
    """
    if workspace_provider is None or not callable(workspace_provider):
        raise ValueError("workspace_provider must be an async callable")

    async def _run(agent_id: str, prompt: str, context: dict[str, Any]) -> str:
        from agentscope.message import Msg, TextBlock  # late import: heavy dep
        from agentscope_runtime.engine.schemas.agent_schemas import (
            AgentRequest,
            ContentType,
            Message,
            MessageType,
            Role,
            TextContent,
        )

        workspace = await workspace_provider(agent_id)
        runner = getattr(workspace, "runner", None)
        if runner is None:
            raise RuntimeError(
                f"Workspace for agent {agent_id!r} has no runner attached",
            )

        ctx = context or {}
        session_id = str(
            ctx.get("session_id") or f"hubos.core:{agent_id}:{id(prompt):x}",
        )
        user_id = str(ctx.get("user_id") or DEFAULT_USER_ID)
        channel = str(ctx.get("channel") or DEFAULT_CHANNEL)

        # `query_handler` reads the actual query from `msgs` (agentscope's
        # `Msg`), while `AgentRequest.input` is a pydantic-validated field
        # that expects the runtime-schema `Message` — not agentscope's `Msg`.
        # Build both so both contracts are satisfied.
        msgs = [
            Msg(
                name="user",
                role="user",
                content=[TextBlock(type="text", text=prompt)],
            ),
        ]

        request_input = [
            Message(
                type=MessageType.MESSAGE,
                role=Role.USER,
                content=[TextContent(type=ContentType.TEXT, text=prompt)],
            ),
        ]

        try:
            request = AgentRequest(
                session_id=session_id,
                user_id=user_id,
                input=request_input,
                channel=channel,
            )
        except TypeError:
            # Older/newer AgentRequest signatures may not accept `input` /
            # `channel`. Fall back to the bare minimum and let the runner
            # treat msgs as the source of truth.
            request = AgentRequest(  # type: ignore[call-arg]
                session_id=session_id,
                user_id=user_id,
            )

        # ---- sub-agent write scope (C) + audit trail (B) ------------------
        scope = _derive_subagent_scope(agent_id, ctx)
        parent_session = str(
            ctx.get("parent_session_id") or ctx.get("session_id") or "",
        )
        scope_token = None
        audit_path: Path | None = None
        if scope is not None:
            scope_token = current_subagent_write_scope.set(scope)
            audit_path = _audit_log_path(parent_session)
            _write_audit(
                audit_path,
                {
                    "ts": datetime.now(tz=timezone.utc).isoformat(),
                    "event": "subagent_started",
                    "agent_id": agent_id,
                    "parent_session_id": parent_session,
                    "workflow_id": ctx.get("workflow_id"),
                    "step_id": ctx.get("step_id"),
                    "label": ctx.get("label"),
                    "channel": channel,
                    "session_id": session_id,
                    "write_scope": str(scope),
                    "prompt_preview": prompt[:240],
                },
            )
            logger.info(
                "sub-agent run started: agent=%s parent_session=%s workflow=%s "
                "step=%s label=%s scope=%s",
                agent_id,
                parent_session,
                ctx.get("workflow_id"),
                ctx.get("step_id"),
                ctx.get("label"),
                scope,
            )

        last_assistant_text = ""
        all_chunks: list[str] = []
        tool_use_count = 0
        t0 = time.time()
        err_text: str | None = None

        try:
            async for msg, last in runner.query_handler(msgs, request=request):
                # (B) capture tool_use events from the sub-agent's stream
                if audit_path is not None:
                    for blk in _extract_tool_use_blocks(msg):
                        tool_use_count += 1
                        _write_audit(
                            audit_path,
                            {
                                "ts": datetime.now(
                                    tz=timezone.utc,
                                ).isoformat(),
                                "event": "subagent_tool_use",
                                "agent_id": agent_id,
                                "parent_session_id": parent_session,
                                "workflow_id": ctx.get("workflow_id"),
                                "step_id": ctx.get("step_id"),
                                "label": ctx.get("label"),
                                "session_id": session_id,
                                **blk,
                            },
                        )

                text = _extract_text(msg)
                if text:
                    role = getattr(msg, "role", None)
                    name = getattr(msg, "name", None)
                    if role == "assistant" or name in {"assistant", "Friday"}:
                        last_assistant_text = text
                    all_chunks.append(text)
                if last:
                    break
        except Exception as e:  # noqa: BLE001
            err_text = f"{type(e).__name__}: {e}"
            raise
        finally:
            if audit_path is not None:
                _write_audit(
                    audit_path,
                    {
                        "ts": datetime.now(tz=timezone.utc).isoformat(),
                        "event": "subagent_finished",
                        "agent_id": agent_id,
                        "parent_session_id": parent_session,
                        "workflow_id": ctx.get("workflow_id"),
                        "step_id": ctx.get("step_id"),
                        "label": ctx.get("label"),
                        "session_id": session_id,
                        "elapsed_ms": int((time.time() - t0) * 1000),
                        "tool_use_count": tool_use_count,
                        "error": err_text,
                    },
                )
                logger.info(
                    "sub-agent run finished: agent=%s parent_session=%s "
                    "tool_uses=%d elapsed_ms=%d error=%s",
                    agent_id,
                    parent_session,
                    tool_use_count,
                    int((time.time() - t0) * 1000),
                    err_text,
                )
            if scope_token is not None:
                current_subagent_write_scope.reset(scope_token)

        result = last_assistant_text or "\n".join(all_chunks).strip()
        if not result:
            logger.warning(
                "Host agent %r returned no extractable text for prompt=%r",
                agent_id,
                prompt[:80],
            )
        return result

    return _run


def _extract_text(msg: Any) -> str:
    """Best-effort: pull text out of an agentscope Msg / dict / object."""
    getter = getattr(msg, "get_text_content", None)
    if callable(getter):
        try:
            t = getter()
            if isinstance(t, str) and t:
                return t
        except Exception:  # noqa: BLE001
            pass

    content = getattr(msg, "content", None)
    if content is None and isinstance(msg, dict):
        content = msg.get("content")

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out: list[str] = []
        for block in content:
            block_type = getattr(block, "type", None) or (
                block.get("type") if isinstance(block, dict) else None
            )
            if block_type and block_type != "text":
                continue
            t = getattr(block, "text", None)
            if t is None and isinstance(block, dict):
                t = block.get("text")
            if isinstance(t, str) and t:
                out.append(t)
        if out:
            return "\n".join(out)
    return ""


__all__ = [
    "build_host_agent_runner",
    "WorkspaceProvider",
    "DEFAULT_CHANNEL",
    "DEFAULT_USER_ID",
]
