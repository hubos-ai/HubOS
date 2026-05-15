# -*- coding: utf-8 -*-
"""Context variable for agent workspace directory.

This module provides a context variable to pass the agent's workspace
directory to tool functions, allowing them to resolve relative paths
correctly in a multi-agent environment.
"""
from contextvars import ContextVar
from pathlib import Path

# Context variable to store the current agent's workspace directory
current_workspace_dir: ContextVar[Path | None] = ContextVar(
    "current_workspace_dir",
    default=None,
)


def get_current_workspace_dir() -> Path | None:
    """Get the current agent's workspace directory from context.

    Returns:
        Path to the current agent's workspace directory, or None if not set.
    """
    return current_workspace_dir.get()


def set_current_workspace_dir(workspace_dir: Path | None) -> None:
    """Set the current agent's workspace directory in context.

    Args:
        workspace_dir: Path to the agent's workspace directory.
    """
    current_workspace_dir.set(workspace_dir)


# Context variable to store the recent_max_bytes limit
current_recent_max_bytes: ContextVar[int | None] = ContextVar(
    "current_recent_max_bytes",
    default=None,
)


def get_current_recent_max_bytes() -> int | None:
    """Get the current agent's recent_max_bytes limit from context.

    Returns:
        Byte limit for recent tool output truncation, or None if not set.
    """
    return current_recent_max_bytes.get()


def set_current_recent_max_bytes(max_bytes: int | None) -> None:
    """Set the current agent's recent_max_bytes limit in context.

    Args:
        max_bytes: Byte limit for recent tool output truncation.
    """
    current_recent_max_bytes.set(max_bytes)


# ---------------------------------------------------------------------------
# Sub-agent write scope
# ---------------------------------------------------------------------------
# When a host agent is running as a sibling sub-agent on behalf of the GM
# (via spawn_subagents / coordinate_workflow), the GM's prompt is relayed
# directly into the sub-agent's LLM — it's semantically "user input" from an
# audit standpoint. To prevent a rogue / creative sub-agent prompt from
# writing arbitrary files into the workspace root (the pattern we observed
# with auto-generated "多代理系统介绍.md" files), write-type tools will
# re-root every relative write under this scope when it is set, and reject
# absolute writes that escape it.
#
# `None` means "unconstrained" — the normal GM-owned agent path.
current_subagent_write_scope: ContextVar[Path | None] = ContextVar(
    "current_subagent_write_scope",
    default=None,
)


def get_current_subagent_write_scope() -> Path | None:
    """Return the sub-agent write-scope directory, or ``None`` if unset."""
    return current_subagent_write_scope.get()


def set_current_subagent_write_scope(scope: Path | None):
    """Set the sub-agent write-scope directory.

    Returns the ``Token`` so callers can ``reset()`` it deterministically in a
    ``finally`` block (important: ContextVar values survive past function
    returns on the current task).
    """
    return current_subagent_write_scope.set(scope)


# ---------------------------------------------------------------------------
# Session ID context
# ---------------------------------------------------------------------------
# Set once per query by the Runner's query_handler so that downstream
# modules (tool output archival, etc.) can resolve the current session
# without threading parameters through every function call.
current_session_id: ContextVar[str | None] = ContextVar(
    "current_session_id",
    default=None,
)


def get_current_session_id() -> str | None:
    """Get the current session ID from context.

    Returns:
        Session ID string, or None if not set (e.g. outside a query).
    """
    return current_session_id.get()


def set_current_session_id(session_id: str | None) -> None:
    """Set the current session ID in context.

    Args:
        session_id: Session ID to set, or None to clear.
    """
    current_session_id.set(session_id)


# ---------------------------------------------------------------------------
# Model override (for cron tasks)
# ---------------------------------------------------------------------------
# When set, model_factory will use this instead of the agent's active_model.
# This lets cron jobs use a different model (e.g. DeepSeek V4 Flash) without
# changing the default agent's interactive model.
from typing import Optional as _Optional

# Forward declaration to avoid circular import at module level.
# The actual type is providers.models.ModelSlotConfig.
current_model_override: ContextVar[_Optional[object]] = ContextVar(
    "current_model_override",
    default=None,
)


def get_current_model_override() -> _Optional[object]:
    """Get the model override from context (a ModelSlotConfig or None)."""
    return current_model_override.get()


def set_current_model_override(
    model_override: _Optional[object],
) -> None:
    """Set the model override in context.

    Args:
        model_override: A ModelSlotConfig instance, or None to clear.
    """
    current_model_override.set(model_override)
