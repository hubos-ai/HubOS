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

from hubos.core.work_experience.extractor import WorkExperienceExtractor
from hubos.core.work_experience.integration import (
    get_work_experience_interceptor,
)
from hubos.core.work_experience.prompt_injector import (
    build_experience_injection,
    compress_experience_card,
    inject_experience_into_prompt,
)
from hubos.core.work_experience.retriever import WorkExperienceRetriever
from hubos.core.work_experience.schemas import (
    ExperienceLevel,
    WorkExperience,
    WorkExperienceScope,
    WorkExperienceStatus,
    WorkExperienceStore,
)
from hubos.core.work_experience.service import WorkExperienceService
from hubos.core.work_experience.store import LocalWorkExperienceStore

__all__ = [
    "ExperienceLevel",
    "WorkExperience",
    "WorkExperienceExtractor",
    "WorkExperienceRetriever",
    "WorkExperienceScope",
    "WorkExperienceStatus",
    "WorkExperienceStore",
    "LocalWorkExperienceStore",
    "WorkExperienceService",
    "get_work_experience_interceptor",
    "compress_experience_card",
    "build_experience_injection",
    "inject_experience_into_prompt",
]
