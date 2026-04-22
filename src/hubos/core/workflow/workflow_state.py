"""Workflow State Store — persisted enabled/default state for workflow presets.

This store holds the runtime state (enabled, is_default) for each workflow preset.
The preset definitions themselves live in preset.py (in-process, not persisted here).

State file: ~/.xclaw/workflow_state.json (atomic write, same pattern as WeChatAccountStore)
"""

import json
import logging
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)

_STATE_FILE = Path.home() / ".xclaw" / "workflow_state.json"

# Default state applied to each built-in preset on first load
_DEFAULT_STATES = {
    "one_person_default": {"enabled": True, "is_default": True},
    "parallel_dynamic_v1": {"enabled": False, "is_default": False},
}


@dataclass
class WorkflowState:
    """Runtime state for a workflow preset."""
    id: str
    enabled: bool
    is_default: bool
    updated_at: str  # ISO8601


class WorkflowStateStore:
    """In-memory + file-persisted workflow state.

    Thread-safe. Uses atomic write (temp+rename) for crash safety.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._states: dict[str, WorkflowState] = {}
        self._load()

    # ── Persistence ─────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load state from disk, merging with defaults for any missing presets."""
        if not _STATE_FILE.exists():
            self._states = self._default_states()
            self._save()
            return

        try:
            with open(_STATE_FILE) as f:
                raw = json.load(f)
            self._states = {
                wid: WorkflowState(**v) for wid, v in raw.items()
            }
            # Ensure all built-in presets have a state entry
            for wid, defaults in _DEFAULT_STATES.items():
                if wid not in self._states:
                    self._states[wid] = WorkflowState(id=wid, **defaults)
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.warning(f"Failed to load workflow state: {e}. Using defaults.")
            self._states = self._default_states()
            self._save()

    def _save(self) -> None:
        """Atomic write: write to temp file then rename to target."""
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _STATE_FILE.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                json.dump({wid: asdict(s) for wid, s in self._states.items()}, f, indent=2)
            os.replace(tmp, _STATE_FILE)
        except OSError as e:
            logger.error(f"Failed to write workflow state: {e}")

    def _default_states(self) -> dict[str, WorkflowState]:
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
        return {
            wid: WorkflowState(id=wid, **defaults, updated_at=now)
            for wid, defaults in _DEFAULT_STATES.items()
        }

    # ── Public API ───────────────────────────────────────────────────────────

    def list_states(self) -> list[WorkflowState]:
        """Return all workflow states."""
        with self._lock:
            return list(self._states.values())

    def get_state(self, workflow_id: str) -> WorkflowState | None:
        """Get state for a specific workflow."""
        with self._lock:
            return self._states.get(workflow_id)

    def set_enabled(self, workflow_id: str, enabled: bool) -> WorkflowState:
        """Enable or disable a workflow. Returns updated state."""
        with self._lock:
            if workflow_id not in self._states:
                raise KeyError(f"Unknown workflow: {workflow_id}")
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            self._states[workflow_id].enabled = enabled
            self._states[workflow_id].updated_at = now
            state = self._states[workflow_id]
            self._save()
            return state

    def set_default(self, workflow_id: str) -> WorkflowState:
        """Set a workflow as the default. Unsets default on all others."""
        with self._lock:
            if workflow_id not in self._states:
                raise KeyError(f"Unknown workflow: {workflow_id}")
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            for wid, state in self._states.items():
                state.is_default = (wid == workflow_id)
                state.updated_at = now
            self._save()
            return self._states[workflow_id]


# ── Singleton ────────────────────────────────────────────────────────────────

_store: WorkflowStateStore | None = None


def get_workflow_state_store() -> WorkflowStateStore:
    """Return the global WorkflowStateStore singleton."""
    global _store
    if _store is None:
        _store = WorkflowStateStore()
    return _store
