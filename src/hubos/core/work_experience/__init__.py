# -*- coding: utf-8 -*-
"""Work Experience Layer.

V4 WorkflowCard is the active runtime/admin model. V3 classes are still
exported for older imports and tests that have not moved to v4 yet.
"""

# v3 imports (kept for backward compatibility only)
from hubos.core.work_experience.extractor import WorkExperienceExtractor
from hubos.core.work_experience.retriever import WorkExperienceRetriever
from hubos.core.work_experience.schemas import ExperienceLevel
from hubos.core.work_experience.service import WorkExperienceService
from hubos.core.work_experience.store import LocalWorkExperienceStore
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
    "ExperienceLevel",
    "LocalWorkExperienceStore",
    "WorkExperienceExtractor",
    "WorkExperienceRetriever",
    "WorkExperienceService",
]
