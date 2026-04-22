"""Process-wide registry for the host application's ``HostAgentRunner``.

The host wires its own runner (typically backed by a multi-agent manager) at
startup; tools running inside the same process then look it up via
``get_host_agent_runner()`` without having to plumb the manager through every
call site.

This module is *one-way*: hubos.core only exposes the registry. The actual
``HostAgentRunner`` implementation lives in the host (so hubos.core never imports
host classes).

Thread-safe; the registry is just an atomic reference swap.
"""
from __future__ import annotations

import threading
from typing import Optional

from hubos.core.workers.providers.host_agent import HostAgentRunner

_lock = threading.Lock()
_runner: Optional[HostAgentRunner] = None


def set_host_agent_runner(runner: Optional[HostAgentRunner]) -> None:
    """Register (or clear, with ``None``) the process-wide HostAgentRunner.

    Idempotent. Safe to call from host startup or tests.
    """
    global _runner
    with _lock:
        _runner = runner


def get_host_agent_runner() -> Optional[HostAgentRunner]:
    """Return the currently registered HostAgentRunner, or ``None`` if unset."""
    return _runner


def clear_host_agent_runner() -> None:
    """Convenience alias for ``set_host_agent_runner(None)``."""
    set_host_agent_runner(None)


__all__ = [
    "set_host_agent_runner",
    "get_host_agent_runner",
    "clear_host_agent_runner",
]
