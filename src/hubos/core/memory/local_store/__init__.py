"""Local file-backed long-term memory store for hubos.core.

A direct port of the Hermes-style "session-saver" pattern: append-only JSONL
messages + per-session metadata + daily summary markdown + per-month archive.
Pure standard-library; no external services. Suitable as the L4 default
provider before a remote/semantic backend is wired up.

Storage root resolution (first match wins):
  1. ``HUBOS_MEMORY_ROOT`` env var (if set)
  2. ``~/.hubos/memory``
"""
from hubos.core.memory.local_store.store import LocalMemoryStore, get_memory_root
from hubos.core.memory.local_store.daily_summary import DailySummaryGenerator

__all__ = [
    "LocalMemoryStore",
    "DailySummaryGenerator",
    "get_memory_root",
]
