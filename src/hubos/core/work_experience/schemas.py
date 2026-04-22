"""Work Experience Layer schemas."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Protocol, runtime_checkable
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


class WorkExperienceScope(str, Enum):
    """Scope / granularity of a work experience card."""

    GLOBAL = "global"   # System-wide lesson
    USER = "user"       # Per-user lesson
    PROJECT = "project"  # Per-project lesson
    SESSION = "session"  # Per-session lesson

    @staticmethod
    def priority(scope: "WorkExperienceScope") -> int:
        """Lower number = higher priority when sorting."""
        return _SCOPE_PRIORITY.get(scope, 999)


_SCOPE_PRIORITY = {
    WorkExperienceScope.GLOBAL: 0,
    WorkExperienceScope.USER: 1,
    WorkExperienceScope.PROJECT: 2,
    WorkExperienceScope.SESSION: 3,
}


class ExperienceLevel(str, Enum):
    """
    Experience maturity level — reflects how well-established an experience is.

    Lifecycle: NEW -> OBSERVED -> MATURE -> DEPRECATED
                |-> DEPRECATED (direct, from any level)
    """
    NEW = "new"         # Just created, low weight in retrieval
    OBSERVED = "observed"  # Seen in a few tasks, medium weight
    MATURE = "mature"   # Proven over multiple tasks, high weight,优先注入
    DEPRECATED = "deprecated"  # Outdated or superseded, excluded from retrieval

    def retrieval_weight(self) -> float:
        """Relative weight for retrieval scoring."""
        return _LEVEL_WEIGHTS.get(self, 0.0)

    def can_transition_to(self, target: "ExperienceLevel") -> bool:
        """Check if a transition is valid."""
        return target in _LEVEL_TRANSITIONS.get(self, frozenset())


_LEVEL_WEIGHTS = {
    ExperienceLevel.NEW: 0.3,
    ExperienceLevel.OBSERVED: 0.6,
    ExperienceLevel.MATURE: 1.0,
    ExperienceLevel.DEPRECATED: 0.0,
}

_LEVEL_TRANSITIONS: dict[ExperienceLevel, frozenset[ExperienceLevel]] = {
    ExperienceLevel.NEW: frozenset({
        ExperienceLevel.OBSERVED,
        ExperienceLevel.MATURE,
        ExperienceLevel.DEPRECATED,
    }),
    ExperienceLevel.OBSERVED: frozenset({
        ExperienceLevel.MATURE,
        ExperienceLevel.DEPRECATED,
        ExperienceLevel.NEW,  # Can regress if proven wrong
    }),
    ExperienceLevel.MATURE: frozenset({
        ExperienceLevel.DEPRECATED,
        ExperienceLevel.OBSERVED,  # Can regress
    }),
    ExperienceLevel.DEPRECATED: frozenset(),  # Terminal
}


class WorkExperienceStatus(str, Enum):
    """
    Legacy governance state — kept for backward compatibility.

    For new cards, use ExperienceLevel instead.
    Status transitions still work but are secondary to experience_level.
    """
    CANDIDATE = "candidate"   # Newly extracted, not yet reviewed
    APPROVED = "approved"     # Reviewed and approved for injection
    REJECTED = "rejected"     # Reviewed and rejected
    ARCHIVED = "archived"     # Superseded or manual archive; retained but not used

    def can_transition_to(self, target: "WorkExperienceStatus") -> bool:
        """Check if a transition from self to target is valid."""
        return target in _STATUS_TRANSITIONS.get(self, frozenset())


_STATUS_TRANSITIONS: dict[WorkExperienceStatus, frozenset[WorkExperienceStatus]] = {
    WorkExperienceStatus.CANDIDATE: frozenset({
        WorkExperienceStatus.APPROVED,
        WorkExperienceStatus.REJECTED,
        WorkExperienceStatus.ARCHIVED,
    }),
    WorkExperienceStatus.APPROVED: frozenset({
        WorkExperienceStatus.REJECTED,
        WorkExperienceStatus.ARCHIVED,
    }),
    WorkExperienceStatus.REJECTED: frozenset({
        WorkExperienceStatus.CANDIDATE,   # Re-review
        WorkExperienceStatus.ARCHIVED,
    }),
    WorkExperienceStatus.ARCHIVED: frozenset(),  # Terminal state
}


@dataclass
class WorkExperience:
    """
    A post-task experience card capturing what happened and how to do it better.

    Produced by WorkExperienceExtractor from a ReflectionReport + TaskContext.
    Stored persistently by WorkExperienceStore implementations.
    Retrieved by WorkExperienceRetriever before future similar tasks.
    """

    experience_id: UUID = field(default_factory=uuid4)
    scope: WorkExperienceScope = WorkExperienceScope.SESSION

    # Retrieval fields
    trigger_keywords: list[str] = field(default_factory=list)
    trigger_hint: str = ""          # e.g. "file:python:csv"

    # Content fields
    title: str = ""                # One-line lesson title
    what_happened: str = ""       # Narrative description
    what_worked: list[str] = field(default_factory=list)
    what_failed: list[str] = field(default_factory=list)
    guidance: str = ""             # Actionable guidance for next time
    avoidance: str = ""            # What to avoid

    # ---- New fields for work guidance model ----
    # Pattern summary: concise description of the task type this experience applies to
    usage_pattern_summary: str = ""
    # Recommended tool order for similar tasks
    recommended_tool_order: list[str] = field(default_factory=list)
    # Recommended workflow steps
    recommended_workflow: list[str] = field(default_factory=list)
    # Task types this experience applies to
    applicable_task_types: list[str] = field(default_factory=list)
    # Estimated success rate (0.0-1.0) based on effective/hit ratio
    success_rate_estimate: float = 0.0
    # ID of experience this one supersedes (for merging/updating)
    supersedes_experience_id: Optional[UUID] = None

    # Metadata
    confidence: float = 0.5        # 0.0–1.0
    source_task_id: str = ""
    source_session_id: str = ""
    source_trace_id: str = ""
    applicability_tags: list[str] = field(default_factory=list)

    # Usage tracking
    hit_count: int = 0                   # Times retrieved for a task
    effective_count: int = 0             # Times successfully used in a prompt
    last_retrieved_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None   # Last time used in a prompt (approved + injected)
    disabled: bool = False

    # Legacy governance state — kept for backward compatibility
    # For new cards, use experience_level instead
    status: WorkExperienceStatus = WorkExperienceStatus.CANDIDATE

    # ---- New maturity model fields ----
    # Maturity level: new/observed/mature/deprecated
    # Defaults based on confidence for new cards, else uses explicit transitions
    experience_level: ExperienceLevel = ExperienceLevel.NEW
    # Numeric maturity score (0-100), computed from usage + confidence
    # Used for fine-grained ranking within the same experience_level
    maturity_score: float = 0.0

    # Timestamps
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def is_expired(self, ttl_seconds: Optional[int] = None) -> bool:
        """Check if the card has expired based on a TTL (none by default)."""
        if ttl_seconds is None:
            return False
        age = (datetime.now(timezone.utc) - self.updated_at).total_seconds()
        return age > ttl_seconds

    def effective_ratio(self) -> float:
        """Return effective_count / hit_count ratio (0.0 if no hits)."""
        if self.hit_count == 0:
            return 0.0
        return self.effective_count / self.hit_count


@runtime_checkable
class WorkExperienceStore(Protocol):
    """
    Protocol for WorkExperience storage backends.

    Implement this to provide alternative storage (e.g. PostgreSQL, Redis).
    """

    def save(self, experience: WorkExperience) -> None:
        """Persist or update a work experience card."""
        ...

    def get(self, experience_id: UUID) -> Optional[WorkExperience]:
        """Retrieve a card by its ID, or None if not found."""
        ...

    def list_all(self, include_disabled: bool = False) -> list[WorkExperience]:
        """List cards. Pass include_disabled=True to include disabled cards."""
        ...

    def list_by_scope(self, scope: WorkExperienceScope, include_disabled: bool = False) -> list[WorkExperience]:
        """List cards for a given scope. Pass include_disabled=True to include disabled cards."""
        ...

    def disable(self, experience_id: UUID) -> bool:
        """Mark a card as disabled. Returns True if found and updated."""
        ...

    def increment_hit(self, experience_id: UUID) -> None:
        """Increment hit_count and update last_retrieved_at."""
        ...

    def update_status(self, experience_id: UUID, status: "WorkExperienceStatus") -> bool:
        """Update a card's governance status. Returns True if found and updated."""
        ...

    def record_effective_use(self, experience_id: UUID) -> None:
        """Record a successful prompt injection (increments effective_count and sets last_used_at)."""
        ...

    def update_experience_level(
        self, experience_id: UUID, level: "ExperienceLevel"
    ) -> bool:
        """Update a card's experience level. Returns True if found and updated."""
        ...

    def update_maturity_score(self, experience_id: UUID, score: float) -> bool:
        """Update a card's maturity score. Returns True if found and updated."""
        ...

    def find_similar(
        self,
        trigger_hint_prefix: str,
        keywords: Optional[list[str]] = None,
        exclude_id: Optional[UUID] = None,
    ) -> list["WorkExperience"]:
        """Find similar experiences by trigger hint and keywords for merging/updating."""
        ...
