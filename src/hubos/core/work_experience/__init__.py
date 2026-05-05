# -*- coding: utf-8 -*-
"""Work Experience Layer — post-task experience card storage and retrieval.

Phase 0-3 provides:
- WorkExperience data model
- LocalWorkExperienceStore (file-based)
- WorkExperienceExtractor (from ReflectionReport + TaskContext)
- WorkExperienceRetriever (scope + keyword + trigger matching)

Phase 4 provides:
- WorkExperienceInterceptor: bypass-read integration with ExecutionOrchestrator

Phase 5 provides:
- Prompt injection of compressed experience hints into LLM prompts

Phase 6 provides:
- Governance state machine: candidate / approved / rejected / archived
- Quality-based ranking (confidence * usage)
- WorkExperienceService: administrative API (list/approve/reject/archive/merge)
- Deduplication and merge logic

All capabilities require ENABLE_WORK_EXPERIENCE_LAYER=true to be active.
"""

# v3 imports (kept for backward compat — orchestrator.py still uses these)
from hubos.core.work_experience.integration import (
    get_work_experience_interceptor as _get_v3_interceptor,
)

# v4 imports — the new system
from hubos.core.work_experience.integration_v4 import (
    WorkExperienceInterceptor as V4WorkExperienceInterceptor,
    get_work_experience_interceptor,
)
from hubos.core.work_experience.schemas_v4 import WorkflowCard
from hubos.core.work_experience.store_v4 import CardStore
from hubos.core.work_experience.retriever_v4 import CardRetriever

# Auto-seed v4 cards on first import (idempotent)
try:
    from hubos.core.work_experience.seed_v4 import seed_v4_cards
    seed_v4_cards()
except Exception:
    pass

__all__ = [
    # v4 (active)
    "WorkflowCard",
    "CardStore",
    "CardRetriever",
    "V4WorkExperienceInterceptor",
    "get_work_experience_interceptor",
    # v3 (backward compat)
    "_get_v3_interceptor",
]
