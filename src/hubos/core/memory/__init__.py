"""hubos.core memory layer.

Layered memory model (see docs/architecture-memory-layers.md when written):

  L1  Working memory       per-turn   (in-process scratch, e.g. InMemoryMemory)
  L2  Short-term memory    per-chat   (host-app session manager + safe JSON session)
  L3  Mid-term memory      per-agent  (host-app agent profile + MemoryService)
  L4  Long-term semantic   cross      (this package: every backend implements
                                       :class:`MemoryStore`; ``local_store`` is
                                       the file-backed first implementation,
                                       remote/embedding backends plug in behind
                                       the same protocol.)

The contract lives in :mod:`hubos.core.memory.base` (Protocol-based, structurally
typed). Probe optional capabilities via ``isinstance(store, ArchivableMemoryStore)``
or ``isinstance(store, SummarizableMemoryStore)``.
"""

from hubos.core.memory.base import (
    ArchivableMemoryStore,
    MemoryStore,
    SummarizableMemoryStore,
)
from hubos.core.memory.local_store import (
    DailySummaryGenerator,
    LocalMemoryStore,
    get_memory_root,
)

__all__ = [
    "MemoryStore",
    "ArchivableMemoryStore",
    "SummarizableMemoryStore",
    "LocalMemoryStore",
    "DailySummaryGenerator",
    "get_memory_root",
]
