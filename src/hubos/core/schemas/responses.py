"""Final response and merge result schemas."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4


def _utcnow() -> datetime:
    """Return timezone-aware UTC datetime."""
    return datetime.now(timezone.utc)


SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class MergeResult:
    """
    Result of merging a single TaskResult into the final response.

    Part of the Merge Contract as defined in ARCHITECTURE.md.
    """

    unit_id: UUID
    status: str = "merged"
    conflict_notes: Optional[str] = None
    merged_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FinalResponse:
    """
    Final response generated after merging all TaskResults.

    Part of the Merge Contract as defined in ARCHITECTURE.md.
    Coordinator merges TaskResult[] into FinalResponse with conflict notes.
    """

    schema_version: str = field(default=SCHEMA_VERSION)
    response_id: UUID = field(default_factory=uuid4)
    task_id: str = ""
    session_id: str = ""
    trace_id: str = ""
    content: str = ""
    merge_results: list[MergeResult] = field(default_factory=list)
    conflict_summary: Optional[str] = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.task_id:
            raise ValueError("task_id is required")
