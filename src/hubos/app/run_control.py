# -*- coding: utf-8 -*-
"""RunControlStore — unified index overlay for all long-running tasks.

Maps ``run_id → RunEntry`` and delegates cancel to the correct subsystem.
Thin index — owns no state beyond the mapping.  If registration silently
fails, everything still works.

RunTypes tracked:
  CHAT           — normal LLM chat stream via TaskTracker
  SPAWN          — spawn_subagents via TaskMonitor
  WORKFLOW       — coordinate_workflow DAG via TaskMonitor + _workflows
  DELEGATE       — delegate_task via TaskMonitor
  PLAN           — task_plan via TaskPlanExecutor + TaskMonitor
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# Terminal statuses — cancel is a no-op for these.
_TERMINAL = frozenset({"done", "failed", "cancelled"})

# TTL eviction threshold (seconds).
_EVICT_TTL = 3600  # 1 hour


class RunType(str, Enum):
    CHAT = "chat"
    SPAWN = "spawn"
    WORKFLOW = "workflow"
    DELEGATE = "delegate"
    PLAN = "plan"


@dataclass
class RunEntry:
    """Metadata for a single run tracked by RunControlStore."""

    run_id: str
    run_type: RunType
    session_id: str
    status: str = "running"  # "running" | "done" | "failed" | "cancelled"
    created_at: float = field(default_factory=time.time)

    # Links into existing stores — at least one is populated per entry.
    monitor_task_id: Optional[str] = None
    plan_id: Optional[str] = None
    workflow_id: Optional[str] = None
    chat_id: Optional[str] = None

    # Parent-child run tree.
    parent_run_id: Optional[str] = None
    child_run_ids: List[str] = field(default_factory=list)

    # Guidance messages (latest user guidance text).
    guidance_messages: List[str] = field(default_factory=list)

    # Cancel metadata (P0-5).
    cancellable: bool = True
    cancel_behavior: str = "real"  # "real" | "mark_only"

    # Guidance tracking (P0-3).
    guided_from_run_id: Optional[str] = None
    guidance_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Context variable for current run_id propagation
# ---------------------------------------------------------------------------

_current_run_id_var: ContextVar[Optional[str]] = ContextVar(
    "hubos_current_run_id",
    default=None,
)


def set_current_run_id(run_id: Optional[str]) -> None:
    """Set the current run_id in the execution context."""
    _current_run_id_var.set(run_id)


def get_current_run_id() -> Optional[str]:
    """Get the current run_id from the execution context."""
    return _current_run_id_var.get()


# ---------------------------------------------------------------------------
# Chat cancel handler registry
# ---------------------------------------------------------------------------
# RunControlStore doesn't own the TaskTracker, so chat cancel is done via
# a registered callback.  The /console router registers the handler at
# startup.

_CHAT_CANCEL_HANDLER: Optional[Callable[[str], Awaitable[bool]]] = None


def register_chat_cancel_handler(
    handler: Callable[[str], Awaitable[bool]],
) -> None:
    """Register the async callback used to cancel a chat run.

    ``handler(chat_id) -> bool`` should call ``TaskTracker.request_stop``
    and return whether it succeeded.
    """
    global _CHAT_CANCEL_HANDLER
    _CHAT_CANCEL_HANDLER = handler


async def _do_chat_cancel(chat_id: str) -> bool:
    """Invoke the registered chat cancel handler (best-effort)."""
    if _CHAT_CANCEL_HANDLER is None:
        logger.debug("run_control: no chat cancel handler registered")
        return False
    try:
        return await _CHAT_CANCEL_HANDLER(chat_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "run_control: chat cancel handler failed for %s", chat_id
        )
        return False


class RunControlStore:
    """Index overlay: maps run_id → RunEntry. No ownership of underlying tasks."""

    def __init__(self) -> None:
        self._runs: Dict[str, RunEntry] = {}
        self._session_index: Dict[str, List[str]] = {}  # session_id → [run_id]
        self._lock = asyncio.Lock()

    # -- public API ----------------------------------------------------------

    async def register(self, entry: RunEntry) -> str:
        """Register a new run. Returns the run_id (uses entry.run_id or generates one)."""
        if not entry.run_id:
            entry.run_id = uuid.uuid4().hex
        async with self._lock:
            self._evict_old()
            self._runs[entry.run_id] = entry
            self._session_index.setdefault(entry.session_id, []).append(
                entry.run_id
            )
            # Link to parent if specified.
            if entry.parent_run_id and entry.parent_run_id in self._runs:
                parent = self._runs[entry.parent_run_id]
                if entry.run_id not in parent.child_run_ids:
                    parent.child_run_ids.append(entry.run_id)
        return entry.run_id

    async def update_status(self, run_id: str, status: str) -> None:
        """Update run status."""
        async with self._lock:
            entry = self._runs.get(run_id)
            if entry is not None:
                entry.status = status

    async def unregister(self, run_id: str) -> None:
        """Remove from index (run finished)."""
        async with self._lock:
            entry = self._runs.pop(run_id, None)
            if entry is not None:
                self._remove_from_session_index(entry)
                # Unlink from parent.
                if entry.parent_run_id and entry.parent_run_id in self._runs:
                    parent = self._runs[entry.parent_run_id]
                    try:
                        parent.child_run_ids.remove(run_id)
                    except ValueError:
                        pass

    async def get_run(self, run_id: str) -> Optional[RunEntry]:
        """Look up a single run."""
        async with self._lock:
            return self._runs.get(run_id)

    async def list_runs(
        self,
        session_id: Optional[str] = None,
        active_only: bool = True,
    ) -> List[RunEntry]:
        """List runs, optionally filtered by session.

        ``session_id=None`` intentionally means "all sessions".  The chat UI
        uses this as a fallback because some tool-layer runs are created with
        backend session ids while the visible AgentScope tab may use a local
        timestamp id.
        """
        async with self._lock:
            if session_id:
                run_ids = self._session_index.get(session_id, [])
                entries = [
                    self._runs[rid] for rid in run_ids if rid in self._runs
                ]
            else:
                entries = list(self._runs.values())
        if active_only:
            entries = [e for e in entries if e.status not in _TERMINAL]
        return sorted(entries, key=lambda e: e.created_at, reverse=True)

    async def get_run_tree(self, run_id: str) -> List[RunEntry]:
        """Get run + all descendants (BFS). Returns list including root."""
        async with self._lock:
            result: List[RunEntry] = []
            queue = [run_id]
            while queue:
                rid = queue.pop(0)
                entry = self._runs.get(rid)
                if entry is None:
                    continue
                result.append(entry)
                queue.extend(entry.child_run_ids)
        return result

    async def cancel_run(self, run_id: str) -> bool:
        """Unified cancel: delegates to the correct mechanism.

        Cancels the run **and all children** recursively.

        Returns True if a cancel was attempted on a non-terminal run.
        """
        # Gather tree while holding lock, then cancel outside lock.
        async with self._lock:
            entry = self._runs.get(run_id)
        if entry is None:
            return False
        if entry.status in _TERMINAL:
            return False

        cancelled_any = False

        # Cancel children first (depth-first).
        tree = await self.get_run_tree(run_id)
        # Process leaves first (reverse BFS order gives children before parent).
        for child in reversed(tree):
            if child.run_id == run_id:
                continue
            if child.status not in _TERMINAL:
                ok = await self._cancel_single(child)
                if ok:
                    cancelled_any = True

        # Cancel the requested run itself.
        ok = await self._cancel_single(entry)
        if ok:
            cancelled_any = True

        return cancelled_any

    async def _cancel_single(self, entry: RunEntry) -> bool:
        """Cancel a single run entry (no child recursion)."""
        if entry.status in _TERMINAL:
            return False

        if not entry.cancellable:
            # mark_only: just set status, no real cancel.
            entry.status = "cancelled"
            return True

        cancelled = False

        if entry.monitor_task_id:
            try:
                from .task_monitor_helpers import request_cancel_task

                await request_cancel_task(entry.monitor_task_id)
                cancelled = True
            except Exception:  # noqa: BLE001
                logger.warning(
                    "run_control: monitor cancel failed for %s",
                    entry.monitor_task_id,
                )

        if entry.plan_id:
            try:
                from .task_plan_executor import get_plan_executor

                await get_plan_executor().cancel_plan(entry.plan_id)
                cancelled = True
            except Exception:  # noqa: BLE001
                logger.warning(
                    "run_control: plan cancel failed for %s", entry.plan_id
                )

        if entry.workflow_id:
            try:
                from ..agents.tools.agent_workforce import cancel_workflow

                await cancel_workflow(entry.workflow_id)
                cancelled = True
            except Exception:  # noqa: BLE001
                logger.warning(
                    "run_control: workflow cancel failed for %s",
                    entry.workflow_id,
                )

        if entry.chat_id:
            ok = await _do_chat_cancel(entry.chat_id)
            if ok:
                cancelled = True

        if cancelled:
            entry.status = "cancelled"
        return cancelled

    async def cancel_all(self, session_id: str) -> List[str]:
        """Cancel all active runs for a session. Returns list of cancelled run_ids."""
        runs = await self.list_runs(session_id, active_only=True)
        cancelled: List[str] = []
        for run in runs:
            ok = await self.cancel_run(run.run_id)
            if ok:
                cancelled.append(run.run_id)
        return cancelled

    async def request_guidance(
        self,
        run_id: str,
        text: str,
    ) -> Optional[Dict[str, Any]]:
        """Attach guidance text to a run and cancel it.

        Returns dict with guidance_ack, guidance_text, cancelled_run_id
        or None if the run doesn't exist or is already terminal.
        """
        async with self._lock:
            entry = self._runs.get(run_id)
        if entry is None or entry.status in _TERMINAL:
            return None

        entry.guidance_messages.append(text)
        entry.guidance_text = text
        ack_id = f"guidance-{uuid.uuid4().hex[:12]}"

        # Cancel the run so the user can restart with guidance.
        await self.cancel_run(run_id)
        return {
            "guidance_ack": ack_id,
            "guidance_text": text,
            "cancelled_run_id": run_id,
        }

    # -- internal ------------------------------------------------------------

    def _remove_from_session_index(self, entry: RunEntry) -> None:
        """Remove run_id from session index. Caller holds _lock."""
        sids = self._session_index.get(entry.session_id)
        if sids is not None:
            try:
                sids.remove(entry.run_id)
            except ValueError:
                pass
            if not sids:
                self._session_index.pop(entry.session_id, None)

    def _evict_old(self) -> None:
        """Prune entries older than _EVICT_TTL that are in terminal state.

        Called inside _lock. Best-effort — skip on error.
        """
        try:
            now = time.time()
            cutoff = now - _EVICT_TTL
            to_remove: List[str] = []
            for rid, entry in self._runs.items():
                if entry.status in _TERMINAL and entry.created_at < cutoff:
                    to_remove.append(rid)
            for rid in to_remove:
                entry = self._runs.pop(rid, None)
                if entry is not None:
                    self._remove_from_session_index(entry)
        except Exception:  # noqa: BLE001
            logger.warning("run_control: _evict_old failed", exc_info=True)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def _serialise_entry(entry: RunEntry) -> Dict[str, Any]:
    return {
        "run_id": entry.run_id,
        "run_type": entry.run_type.value,
        "session_id": entry.session_id,
        "status": entry.status,
        "created_at": entry.created_at,
        "monitor_task_id": entry.monitor_task_id,
        "plan_id": entry.plan_id,
        "workflow_id": entry.workflow_id,
        "chat_id": entry.chat_id,
        "parent_run_id": entry.parent_run_id,
        "child_run_ids": entry.child_run_ids,
        "guidance_messages": entry.guidance_messages,
        "cancellable": entry.cancellable,
        "cancel_behavior": entry.cancel_behavior,
        "guided_from_run_id": entry.guided_from_run_id,
        "guidance_text": entry.guidance_text,
    }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[RunControlStore] = None


def get_run_control_store() -> RunControlStore:
    """Return (and lazily create) the global RunControlStore."""
    global _store
    if _store is None:
        _store = RunControlStore()
    return _store
