"""Policy Learning Engine for DAG-native Step 6.

Learns optimal execution policies from historical node run data.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional
from collections import defaultdict
import statistics


@dataclass
class PolicyBucket:
    """A bucket of similar node runs for learning."""
    bucket_key: str  # e.g., "role=dev,task_type=code"
    retry_count_samples: list[int] = field(default_factory=list)
    timeout_ms_samples: list[int] = field(default_factory=list)
    executor_success: dict[str, int] = field(default_factory=dict)  # executor -> success count
    executor_total: dict[str, int] = field(default_factory=dict)  # executor -> total count
    parallelism_samples: list[int] = field(default_factory=list)
    total_runs: int = 0
    total_failures: int = 0
    last_updated: float = 0.0


@dataclass
class LearnedPolicy:
    """A learned execution policy recommendation."""
    bucket_key: str
    recommended_retry_count: int = 3
    recommended_timeout_ms: int = 300000
    recommended_executor: str = "native"
    recommended_parallelism: int = 10
    confidence: float = 0.0  # 0.0 to 1.0
    applicability: dict[str, Any] = field(default_factory=dict)  # When this policy applies
    based_on_samples: int = 0
    success_rate: float = 0.0
    rollout_mode: str = "off"  # off, shadow, canary, full


class PolicyLearningEngine:
    """Learns optimal policies from historical node execution data."""

    def __init__(self) -> None:
        self._buckets: dict[str, PolicyBucket] = {}
        self._policies: dict[str, LearnedPolicy] = {}
        self._rollout_status: dict[str, str] = {}  # policy_id -> rollout mode

    def record_execution(
        self,
        role: str,
        task_type: str,
        executor: str,
        success: bool,
        retry_count: int,
        timeout_ms: int,
        parallelism_used: int,
        failure_reason: Optional[str] = None,
    ) -> None:
        """Record a node execution for learning."""
        bucket_key = self._make_bucket_key(role, task_type)
        bucket = self._get_or_create_bucket(bucket_key)

        bucket.retry_count_samples.append(retry_count)
        bucket.timeout_ms_samples.append(timeout_ms)
        bucket.parallelism_samples.append(parallelism_used)
        bucket.total_runs += 1
        if not success:
            bucket.total_failures += 1

        if executor not in bucket.executor_total:
            bucket.executor_total[executor] = 0
            bucket.executor_success[executor] = 0

        bucket.executor_total[executor] += 1
        if success:
            bucket.executor_success[executor] += 1

        bucket.last_updated = time.time()

        # Recalculate policy for this bucket
        self._recalculate_policy(bucket_key)

    def _make_bucket_key(self, role: str, task_type: str) -> str:
        """Create a bucket key from role and task type."""
        return f"role={role},task_type={task_type}"

    def _get_or_create_bucket(self, bucket_key: str) -> PolicyBucket:
        """Get or create a policy bucket."""
        if bucket_key not in self._buckets:
            self._buckets[bucket_key] = PolicyBucket(bucket_key=bucket_key)
        return self._buckets[bucket_key]

    def _recalculate_policy(self, bucket_key: str) -> None:
        """Recalculate policy for a bucket."""
        bucket = self._buckets.get(bucket_key)
        if bucket is None or bucket.total_runs < 5:
            return  # Need minimum samples

        # Calculate recommended retry count (median)
        if bucket.retry_count_samples:
            recommended_retry = int(statistics.median(bucket.retry_count_samples))
        else:
            recommended_retry = 3

        # Calculate recommended timeout (median + buffer)
        if bucket.timeout_ms_samples:
            recommended_timeout = int(statistics.median(bucket.timeout_ms_samples) * 1.2)
        else:
            recommended_timeout = 300000

        # Calculate best executor
        best_executor = "native"
        best_success_rate = 0.0
        for executor, total in bucket.executor_total.items():
            if total >= 3:  # Minimum sample size
                success_rate = bucket.executor_success.get(executor, 0) / total
                if success_rate > best_success_rate:
                    best_success_rate = success_rate
                    best_executor = executor

        # Calculate recommended parallelism
        if bucket.parallelism_samples:
            recommended_parallelism = int(statistics.median(bucket.parallelism_samples))
        else:
            recommended_parallelism = 10

        # Calculate confidence based on sample size
        confidence = min(1.0, bucket.total_runs / 50.0)

        # Calculate overall success rate
        success_rate = 1.0 - (bucket.total_failures / bucket.total_runs) if bucket.total_runs > 0 else 0.0

        self._policies[bucket_key] = LearnedPolicy(
            bucket_key=bucket_key,
            recommended_retry_count=recommended_retry,
            recommended_timeout_ms=recommended_timeout,
            recommended_executor=best_executor,
            recommended_parallelism=recommended_parallelism,
            confidence=confidence,
            applicability={"role": bucket_key.split(",")[0].split("=")[1],
                          "task_type": bucket_key.split(",")[1].split("=")[1]},
            based_on_samples=bucket.total_runs,
            success_rate=success_rate,
            rollout_mode=self._rollout_status.get(bucket_key, "off"),
        )

    def get_policy_suggestion(self, role: str, task_type: str) -> Optional[LearnedPolicy]:
        """Get a policy suggestion for a role/task_type combination."""
        bucket_key = self._make_bucket_key(role, task_type)
        return self._policies.get(bucket_key)

    def get_all_policies(self) -> dict[str, LearnedPolicy]:
        """Get all learned policies."""
        return dict(self._policies)

    def set_rollout_mode(self, bucket_key: str, mode: str) -> bool:
        """Set rollout mode for a policy.

        Modes: off, shadow, canary, full
        """
        if mode not in ("off", "shadow", "canary", "full"):
            return False

        self._rollout_status[bucket_key] = mode

        if bucket_key in self._policies:
            self._policies[bucket_key].rollout_mode = mode

        return True

    def get_rollout_status(self, bucket_key: str) -> str:
        """Get rollout status for a policy."""
        return self._rollout_status.get(bucket_key, "off")

    def rollback_policy(self, bucket_key: str) -> bool:
        """Rollback a policy to shadow mode."""
        return self.set_rollout_mode(bucket_key, "shadow")

    def disable_policy(self, bucket_key: str) -> bool:
        """Disable a policy."""
        return self.set_rollout_mode(bucket_key, "off")

    def export_policy(self, bucket_key: str) -> Optional[dict[str, Any]]:
        """Export a policy for persistence."""
        policy = self._policies.get(bucket_key)
        if policy is None:
            return None

        return {
            "bucket_key": policy.bucket_key,
            "recommended_retry_count": policy.recommended_retry_count,
            "recommended_timeout_ms": policy.recommended_timeout_ms,
            "recommended_executor": policy.recommended_executor,
            "recommended_parallelism": policy.recommended_parallelism,
            "confidence": policy.confidence,
            "applicability": policy.applicability,
            "based_on_samples": policy.based_on_samples,
            "success_rate": policy.success_rate,
            "rollout_mode": policy.rollout_mode,
        }

    def import_policy(self, policy_data: dict[str, Any]) -> None:
        """Import a policy from persistence."""
        bucket_key = policy_data["bucket_key"]
        self._policies[bucket_key] = LearnedPolicy(
            bucket_key=policy_data["bucket_key"],
            recommended_retry_count=policy_data.get("recommended_retry_count", 3),
            recommended_timeout_ms=policy_data.get("recommended_timeout_ms", 300000),
            recommended_executor=policy_data.get("recommended_executor", "native"),
            recommended_parallelism=policy_data.get("recommended_parallelism", 10),
            confidence=policy_data.get("confidence", 0.0),
            applicability=policy_data.get("applicability", {}),
            based_on_samples=policy_data.get("based_on_samples", 0),
            success_rate=policy_data.get("success_rate", 0.0),
            rollout_mode=policy_data.get("rollout_mode", "off"),
        )
        self._rollout_status[bucket_key] = policy_data.get("rollout_mode", "off")
