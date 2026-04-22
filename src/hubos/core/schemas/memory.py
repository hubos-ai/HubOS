"""Memory context and update schemas."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


SCHEMA_VERSION = "1.0.0"


class MemoryNamespace(str, Enum):
    """Namespace isolation for memory types."""

    USER_PROFILE = "user_profile"
    PROJECT = "project"
    EPISODIC_TASK = "episodic_task"


# Week 6.5: Extended namespaces for three-tier memory
class FactScope(str, Enum):
    """Scope for fact memory."""

    GLOBAL = "global"  # System-wide facts
    USER = "user"  # User preferences
    PROJECT = "project"  # Project-specific
    SESSION = "session"  # Session-specific


class MemorySource(str, Enum):
    """Source priority for memory."""

    USER_CONFIRMED = "user_confirmed"  # Highest priority
    WORKER_RESULT = "worker_result"
    REFLECTION = "reflection"  # Self-iterated
    INFERRED = "inferred"  # Lower priority
    EXTERNAL = "external"  # Lowest priority


# Week 7: Rollout modes for policy deployment
class RolloutMode(str, Enum):
    """Policy rollout deployment mode."""

    OFF = "off"  # Policy disabled
    SHADOW = "shadow"  # Policy runs but doesn't affect routing
    CANARY = "canary"  # Policy affects small percentage (rollout_ratio)
    FULL = "full"  # Policy affects all traffic


@dataclass
class RolloutStats:
    """Statistics for policy rollout evaluation."""

    total_hits: int = 0
    effective_hits: int = 0
    failed_hits: int = 0
    avg_latency_ms: float = 0.0
    latency_before_ms: float = 0.0  # Baseline latency before policy
    last_evaluated_at: Optional[datetime] = None


@dataclass
class RolloutConfig:
    """Configuration for policy rollout guard."""

    enabled: bool = True
    auto_rollback_enabled: bool = True
    evaluation_window_size: int = 20  # Number of hits to evaluate
    degrade_threshold: float = 0.3  # effective_rate below this triggers degradation
    failure_delta_threshold: float = 0.2  # failure increase above this triggers degradation
    latency_delta_threshold_ms: float = 500.0  # Latency increase above this triggers
    consecutive_degrade_limit: int = 3  # Consecutive degrades before auto-rollback


@dataclass
class HermesRetryRecord:
    """Persistent record for Hermes sync retry queue."""

    retry_id: UUID = field(default_factory=uuid4)
    payload: dict[str, Any] = field(default_factory=dict)
    namespace: str = ""
    task_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    content_hash: str = ""
    attempts: int = 0
    max_attempts: int = 5
    next_retry_at: datetime = field(default_factory=_utcnow)
    last_error: Optional[str] = None
    last_error_type: Optional[str] = None
    status: str = "pending"  # pending, retrying, success, deadletter
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class MemoryEntry:
    """A single memory entry."""

    entry_id: UUID = field(default_factory=uuid4)
    namespace: MemoryNamespace = MemoryNamespace.EPISODIC_TASK
    key: str = ""
    value: dict[str, Any] = field(default_factory=dict)
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


# Week 6.5: Three-tier memory model


@dataclass
class FactMemory:
    """
    Fact memory - factual knowledge layer.

    Stores user preferences, project constraints, long-term rules.
    High confidence, low TTL (or none), versioned.
    """

    fact_id: UUID = field(default_factory=uuid4)
    key: str = ""
    value: Any = None
    confidence: float = 1.0  # 0.0 - 1.0
    source: MemorySource = MemorySource.INFERRED
    scope: FactScope = FactScope.SESSION
    ttl_seconds: Optional[int] = None  # None = no expiry
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def is_expired(self) -> bool:
        """Check if fact has expired based on TTL."""
        if self.ttl_seconds is None:
            return False
        age = (datetime.now(timezone.utc) - self.updated_at).total_seconds()
        return age > self.ttl_seconds


@dataclass
class EpisodicMemory:
    """
    Episodic memory - task process layer.

    Stores task process, key decisions, failure points.
    Lower confidence threshold, has TTL for auto-eviction.
    """

    episode_id: UUID = field(default_factory=uuid4)
    task_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    event_trace: list[dict[str, Any]] = field(default_factory=list)
    decision_rationale: str = ""
    outcome: str = ""  # "success", "failure", "partial"
    impact_score: float = 0.0  # How impactful this episode was
    confidence: float = 0.5  # Lower default confidence
    value_score: float = 0.0  # Computed value for eviction priority
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    archived: bool = False


@dataclass
class LearnedPolicy:
    """
    Learned policy - experience layer.

   反思后形成的策略/反模式/模板.
    Trigger-based, has success_rate tracking.
    """

    policy_id: UUID = field(default_factory=uuid4)
    trigger: str = ""  # What triggers this policy (query pattern, task type, etc.)
    action: dict[str, Any] = field(default_factory=dict)  # What action to take
    # Action fields may include:
    # - worker_priority: list[str]
    # - parallel: bool
    # - timeout_seconds: int
    # - retry_count: int
    # - skip_providers: list[str]
    confidence: float = 0.5  # How confident we are in this policy
    source: MemorySource = MemorySource.REFLECTION  # Origin of this policy
    success_rate: float = 0.0  # 0.0 - 1.0
    applicability: float = 0.5  # How broadly applicable (0-1)
    hit_count: int = 0
    effective_count: int = 0  # Times it actually helped
    last_used_at: Optional[datetime] = None
    disabled: bool = False
    # Week 7: Rollout guard fields
    rollout_mode: RolloutMode = RolloutMode.FULL
    rollout_ratio: int = 100  # 0-100 percentage of traffic affected
    rollback_on_degrade: bool = True  # Auto-rollback when degraded
    rollout_stats: RolloutStats = field(default_factory=RolloutStats)
    last_rollout_change_at: Optional[datetime] = None
    last_rollback_reason: Optional[str] = None
    consecutive_degrade_count: int = 0  # Consecutive evaluations below threshold
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)

    def effectiveness_ratio(self) -> float:
        """Calculate effectiveness ratio."""
        if self.hit_count == 0:
            return 0.0
        return self.effective_count / self.hit_count

    def should_apply_to_request(self) -> bool:
        """Determine if policy should apply to current request based on rollout mode."""
        if self.disabled or self.rollout_mode == RolloutMode.OFF:
            return False
        if self.rollout_mode == RolloutMode.FULL:
            return True
        if self.rollout_mode == RolloutMode.CANARY:
            import random
            return random.randint(1, 100) <= self.rollout_ratio
        # SHADOW mode: policy evaluates but doesn't affect routing
        return False  # Override in policy router to allow shadow evaluation


@dataclass
class RouteHint:
    """
    Route hint generated by reflection for future similar tasks.

    Influences worker selection, parallelism, timeout, retry.
    """

    trigger_task_id: str = ""  # Task that generated this hint
    policy_id: Optional[UUID] = None  # Associated learned policy
    worker_priority: list[str] = field(default_factory=list)  # Preferred providers
    parallel: bool = False  # Run units in parallel
    timeout_seconds: int = 300  # Adjusted timeout
    retry_count: int = 3  # Adjusted retry
    skip_providers: list[str] = field(default_factory=list)  # Providers to avoid
    confidence: float = 0.5  # How confident we are in this hint
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class ReflectionReport:
    """
    Reflection report generated after task completion.

    Outputs: what_worked, what_failed, root_cause, next_time_strategy.
    """

    report_id: UUID = field(default_factory=uuid4)
    task_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    what_worked: list[str] = field(default_factory=list)
    what_failed: list[str] = field(default_factory=list)
    root_cause: str = ""
    next_time_strategy: str = ""
    confidence: float = 0.5
    has_human_feedback: bool = False
    policy_suggestions: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class MemoryWriteAudit:
    """Audit trail for memory write decisions."""

    audit_id: UUID = field(default_factory=uuid4)
    memory_type: str = ""  # "fact", "episodic", "policy"
    key: str = ""
    decision: str = ""  # "accepted", "rejected", "updated", "evicted"
    reason: str = ""  # Detailed reason for the decision
    confidence: float = 0.0
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None
    created_at: datetime = field(default_factory=_utcnow)


@dataclass
class ConflictResolution:
    """Record of conflict resolution decisions."""

    resolution_id: UUID = field(default_factory=uuid4)
    memory_type: str = ""
    key: str = ""
    winner_id: str = ""  # Which entry won
    loser_id: str = ""  # Which entry lost
    resolution_method: str = ""  # "recency", "confidence", "source_priority"
    old_confidence: float = 0.0
    new_confidence: float = 0.0
    created_at: datetime = field(default_factory=_utcnow)


@dataclass(frozen=True)
class MemoryContext:
    """
    Memory retrieved before planning.

    Part of the Memory Contract as defined in ARCHITECTURE.md.
    Before planning: mandatory retrieval.
    """

    schema_version: str = field(default=SCHEMA_VERSION)
    task_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    entries: list[MemoryEntry] = field(default_factory=list)
    retrieved_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.task_id:
            raise ValueError("task_id is required")


@dataclass(frozen=True)
class MemoryUpdate:
    """
    Memory write-back after final response.

    Part of the Memory Contract as defined in ARCHITECTURE.md.
    After final response: mandatory write-back.
    """

    schema_version: str = field(default=SCHEMA_VERSION)
    task_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    namespace: MemoryNamespace = MemoryNamespace.EPISODIC_TASK
    entries: list[MemoryEntry] = field(default_factory=list)
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.task_id:
            raise ValueError("task_id is required")
