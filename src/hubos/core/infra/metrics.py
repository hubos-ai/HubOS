"""Prometheus metrics service for production observability."""

import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Default metrics port
DEFAULT_METRICS_PORT = int(os.environ.get("SOLO_HUB_METRICS_PORT", "9090"))


@dataclass
class Counter:
    """Simple counter metric."""

    name: str
    description: str
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class Gauge:
    """Simple gauge metric."""

    name: str
    description: str
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)


@dataclass
class Histogram:
    """Simple histogram metric."""

    name: str
    description: str
    buckets: list[float]
    values: list[float] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)

    def observe(self, value: float) -> None:
        """Observe a value."""
        self.values.append(value)


class MetricsService:
    """
    Prometheus-compatible metrics service.

    Per Week 5 requirements:
    - Exports metrics in Prometheus format
    - Required metrics:
      - planning_latency_ms
      - worker_success_rate
      - merge_conflict_rate
      - memory_local_hit_rate
      - memory_hermes_hit_rate
      - memory_hermes_sync_success_rate
      - task_completion_time_ms
    """

    # Singleton instance
    _instance: Optional["MetricsService"] = None
    _lock = Lock()
    _initialized: bool = False

    def __new__(cls, *args: Any, **kwargs: Any) -> "MetricsService":
        """Singleton pattern."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize metrics service."""
        if MetricsService._initialized:
            return

        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}
        self._start_time = time.time()

        # Register standard metrics
        self._register_standard_metrics()

        MetricsService._initialized = True
        logger.info("Metrics service initialized")

    def _register_standard_metrics(self) -> None:
        """Register standard Prometheus metrics."""

        # Counters
        self.register_counter(
            "hubos_core_worker_executions_total",
            "Total number of worker executions",
            ["provider", "status"],
        )
        self.register_counter(
            "hubos_core_human_gate_tasks_total",
            "Total number of human gate tasks",
            ["status"],
        )
        self.register_counter(
            "hubos_core_dlq_entries_total",
            "Total number of DLQ entries",
            ["action"],
        )
        self.register_counter(
            "hubos_core_collaboration_messages_total",
            "Total number of collaboration messages",
            ["message_type", "direction"],
        )

        # Gauges
        self.register_gauge(
            "hubos_core_worker_success_rate",
            "Worker execution success rate",
            ["provider"],
        )
        self.register_gauge(
            "hubos_core_memory_local_hit_rate",
            "Local memory hit rate",
            [],
        )
        self.register_gauge(
            "hubos_core_memory_hermes_hit_rate",
            "Hermes memory hit rate",
            [],
        )
        self.register_gauge(
            "hubos_core_memory_hermes_sync_success_rate",
            "Hermes sync success rate",
            [],
        )
        self.register_gauge(
            "hubos_core_pending_human_tasks",
            "Number of pending human tasks",
            [],
        )
        self.register_gauge(
            "hubos_core_ready_state",
            "Readiness state (0=not ready, 1=ready)",
            [],
        )
        self.register_gauge(
            "hubos_core_drain_state",
            "Drain state (0=running, 1=draining, 2=drained)",
            [],
        )

        # Week 6.5: Memory Loop metrics
        self.register_gauge(
            "hubos_core_memory_write_accept_rate",
            "Memory write acceptance rate",
            [],
        )
        self.register_gauge(
            "hubos_core_memory_conflict_rate",
            "Memory conflict resolution rate",
            [],
        )
        self.register_gauge(
            "hubos_core_reflection_success_rate",
            "Reflection engine success rate",
            [],
        )
        self.register_gauge(
            "hubos_core_policy_hit_rate",
            "Policy routing hit rate",
            [],
        )
        self.register_gauge(
            "hubos_core_policy_effective_rate",
            "Policy effectiveness rate (hits that improved outcomes)",
            [],
        )
        self.register_counter(
            "hubos_core_memory_eviction_total",
            "Total memory evictions due to TTL/compaction",
            ["memory_type"],
        )
        self.register_counter(
            "hubos_core_reflection_reports_total",
            "Total reflection reports generated",
            [],
        )

        # Week 7: Production rollout guard metrics
        self.register_gauge(
            "hubos_core_policy_rollout_mode_count",
            "Count of policies by rollout mode",
            ["mode"],
        )
        self.register_counter(
            "hubos_core_policy_auto_rollback_total",
            "Total policy auto-rollbacks triggered",
            ["from_mode", "to_mode"],
        )
        self.register_counter(
            "hubos_core_policy_drift_detected_total",
            "Total policy drift detections",
            [],
        )
        self.register_gauge(
            "hubos_core_memory_budget_utilization",
            "Memory budget utilization ratio",
            ["namespace"],
        )
        self.register_counter(
            "hubos_core_memory_compaction_evicted_total",
            "Total memory entries evicted by compaction",
            ["memory_type"],
        )
        self.register_gauge(
            "hubos_core_hermes_retry_queue_size",
            "Hermes retry queue current size",
            [],
        )
        self.register_counter(
            "hubos_core_hermes_retry_success_total",
            "Total Hermes retry successes",
            [],
        )
        self.register_counter(
            "hubos_core_hermes_retry_deadletter_total",
            "Total Hermes retry dead letters",
            [],
        )

        # Week 10: Multi-instance consistency metrics
        self.register_histogram(
            "hubos_core_distributed_lock_acquire_latency_ms",
            "Distributed lock acquisition latency in milliseconds",
            [1, 5, 10, 25, 50, 100, 250, 500, 1000],
            ["lock_type"],
        )

        # Week 11: Approval gate metrics
        self.register_counter(
            "hubos_core_approval_requests_total",
            "Total approval requests",
            ["action", "level", "status"],
        )
        self.register_gauge(
            "hubos_core_approval_pending",
            "Number of pending approval requests",
            ["level"],
        )
        self.register_counter(
            "hubos_core_break_glass_used_total",
            "Total break-glass bypass usages",
            ["action"],
        )
        self.register_histogram(
            "hubos_core_approval_latency_ms",
            "Approval request lifecycle latency",
            [100, 500, 1000, 5000, 10000, 30000, 60000, 120000],
            [],
        )

        # Week 11: Internal FinOps metrics
        self.register_counter(
            "hubos_core_cost_tracked_total",
            "Total cost tracked",
            ["team_id", "resource_type", "provider"],
        )
        self.register_gauge(
            "hubos_core_budget_utilization_percent",
            "Budget utilization percentage",
            ["team_id", "period"],
        )
        self.register_counter(
            "hubos_core_budget_alerts_total",
            "Total budget alerts generated",
            ["team_id", "alert_type"],
        )

        # Week 11: SOP execution metrics
        self.register_counter(
            "hubos_core_sop_executions_total",
            "Total SOP executions",
            ["sop_id", "category", "status"],
        )
        self.register_histogram(
            "hubos_core_sop_execution_duration_ms",
            "SOP execution duration",
            [1000, 5000, 10000, 30000, 60000, 300000, 600000],
            ["sop_id"],
        )
        self.register_counter(
            "hubos_core_sop_step_failures_total",
            "Total SOP step failures",
            ["sop_id", "step"],
        )
        self.register_counter(
            "hubos_core_distributed_lock_contention_total",
            "Total distributed lock contention events",
            ["lock_type"],
        )
        self.register_counter(
            "hubos_core_idempotency_dedup_total",
            "Total idempotent operations deduplicated",
            ["operation_type"],
        )
        self.register_counter(
            "hubos_core_db_txn_retry_total",
            "Total database transaction retries",
            ["error_type"],
        )
        self.register_counter(
            "hubos_core_db_deadlock_total",
            "Total database deadlocks detected",
            [],
        )
        self.register_histogram(
            "hubos_core_pg_query_latency_ms",
            "PostgreSQL query latency in milliseconds",
            [1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000],
            ["operation"],
        )
        self.register_counter(
            "hubos_core_state_transition_conflict_total",
            "Total state transition conflicts detected",
            [],
        )
        self.register_gauge(
            "hubos_core_release_preflight_status",
            "Release preflight check status (1=pass, 0=fail)",
            ["check_name"],
        )

        # V1.5: Agent Factory routing metrics
        self.register_counter(
            "hubos_core_agent_routing_decision_total",
            "Total agent routing decisions",
            ["agent_id", "strategy", "outcome"],
        )
        self.register_histogram(
            "hubos_core_agent_routing_score",
            "Agent routing score distribution",
            [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
            ["agent_id", "score_type"],
        )
        self.register_gauge(
            "hubos_core_agent_runtime_success_rate",
            "Agent runtime success rate",
            ["agent_id", "role"],
        )
        self.register_histogram(
            "hubos_core_agent_execution_latency_ms",
            "Agent execution latency in milliseconds",
            [10, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
            ["agent_id", "role"],
        )
        self.register_counter(
            "hubos_core_agent_execution_total",
            "Total agent executions",
            ["agent_id", "role", "outcome"],
        )
        self.register_counter(
            "hubos_core_agent_rollout_canary_total",
            "Total canary routing decisions",
            ["agent_id", "rollout_mode"],
        )
        self.register_counter(
            "hubos_core_agent_rollout_shadow_total",
            "Total shadow mode executions",
            ["agent_id", "rollout_mode"],
        )

        # Week 12: Execution Loop MVP metrics
        self.register_counter(
            "hubos_core_task_submit_total",
            "Total task submissions",
            [],
        )
        self.register_counter(
            "hubos_core_task_success_total",
            "Total successful task completions",
            [],
        )
        self.register_counter(
            "hubos_core_task_failed_total",
            "Total failed tasks",
            [],
        )
        self.register_counter(
            "hubos_core_task_human_gate_total",
            "Total tasks entering human gate",
            [],
        )
        self.register_gauge(
            "hubos_core_task_queue_depth",
            "Current task queue depth",
            [],
        )
        self.register_histogram(
            "hubos_core_task_execution_duration_ms",
            "Task execution duration in milliseconds",
            [100, 500, 1000, 2000, 5000, 10000, 30000, 60000, 120000],
            [],
        )
        self.register_histogram(
            "hubos_core_task_stage_duration_ms",
            "Task stage execution duration in milliseconds",
            [50, 100, 200, 500, 1000, 2000, 5000, 10000],
            ["stage"],
        )

        # Week 14: WeChat Embedded Plugin metrics
        self.register_counter(
            "hubos_core_wechat_login_start_total",
            "Total WeChat QR login starts",
            [],
        )
        self.register_counter(
            "hubos_core_wechat_login_success_total",
            "Total successful WeChat logins",
            [],
        )
        self.register_counter(
            "hubos_core_wechat_login_failed_total",
            "Total failed WeChat logins",
            [],
        )
        self.register_counter(
            "hubos_core_wechat_inbound_message_total",
            "Total inbound WeChat messages",
            ["account_id"],
        )
        self.register_counter(
            "hubos_core_wechat_outbound_message_total",
            "Total outbound WeChat messages",
            ["account_id"],
        )
        self.register_counter(
            "hubos_core_wechat_inbound_dedup_total",
            "Total deduplicated inbound WeChat messages",
            ["account_id"],
        )
        self.register_counter(
            "hubos_core_wechat_poller_error_total",
            "Total WeChat poller errors",
            ["account_id"],
        )
        self.register_gauge(
            "hubos_core_wechat_poller_running_accounts",
            "Number of running WeChat pollers",
            [],
        )
        self.register_histogram(
            "hubos_core_wechat_poller_latency_ms",
            "WeChat poller latency in milliseconds",
            [100, 500, 1000, 2000, 5000, 10000, 30000, 60000],
            ["account_id"],
        )

        # Week 13.5: Parallel Core V1.5 Step 4 metrics
        self.register_counter(
            "hubos_core_parallel_backend_real_total",
            "Total parallel executions using real CAMEL backend",
            ["backend"],
        )
        self.register_counter(
            "hubos_core_parallel_fallback_total",
            "Total backend fallbacks in parallel execution",
            ["from_backend", "to_backend"],
        )
        self.register_counter(
            "hubos_core_parallel_branch_duplicate_prevented_total",
            "Total duplicate branch executions prevented",
            ["branch_id"],
        )
        self.register_counter(
            "hubos_core_parallel_recovery_resume_total",
            "Total parallel executions resumed from recovery",
            ["task_id", "source"],
        )
        self.register_counter(
            "hubos_core_parallel_human_gate_open_total",
            "Total human gates opened in parallel execution",
            ["task_id", "branch_id"],
        )
        self.register_counter(
            "hubos_core_parallel_human_gate_resolved_total",
            "Total human gates resolved in parallel execution",
            ["task_id", "branch_id", "action"],
        )
        self.register_counter(
            "hubos_core_parallel_merge_timeout_total",
            "Total merge timeouts in parallel execution",
            ["task_id", "merge_id"],
        )
        self.register_counter(
            "hubos_core_parallel_branch_dispatch_total",
            "Total parallel branch dispatches",
            ["branch_id", "role", "backend"],
        )
        self.register_counter(
            "hubos_core_parallel_branch_complete_total",
            "Total parallel branch completions",
            ["branch_id", "role", "backend", "status"],
        )
        self.register_counter(
            "hubos_core_parallel_merge_start_total",
            "Total parallel merge starts",
            ["merge_id", "required_branches"],
        )
        self.register_counter(
            "hubos_core_parallel_merge_complete_total",
            "Total parallel merge completions",
            ["merge_id", "status"],
        )

        # Week 13.5: Parallel Core V1.5 Step 5 - DAG-native metrics
        self.register_gauge(
            "hubos_core_dag_nodes_total",
            "Total DAG nodes across active plans",
            [],
        )
        self.register_gauge(
            "hubos_core_dag_nodes_running",
            "Currently running DAG nodes",
            [],
        )
        self.register_gauge(
            "hubos_core_dag_active_plans",
            "Active DAG execution plans",
            [],
        )
        self.register_histogram(
            "hubos_core_dag_node_latency_ms",
            "DAG node execution latency in milliseconds",
            [10, 50, 100, 200, 500, 1000, 2000, 5000, 10000],
            ["node_role", "executor"],
        )
        self.register_counter(
            "hubos_core_dag_retry_total",
            "Total DAG node retries",
            ["node_id", "reason"],
        )
        self.register_counter(
            "hubos_core_dag_human_gate_total",
            "Total DAG human gate interventions",
            ["node_id", "action"],
        )
        self.register_histogram(
            "hubos_core_dag_merge_wait_ms",
            "DAG merge wait time in milliseconds",
            [10, 50, 100, 200, 500, 1000, 2000, 5000],
            [],
        )
        self.register_counter(
            "hubos_core_dag_executor_selection_total",
            "DAG executor selection decisions",
            ["executor", "reason"],
        )
        self.register_counter(
            "hubos_core_dag_duplicate_prevented_total",
            "DAG duplicate dispatches prevented",
            ["node_id", "instance_id"],
        )
        self.register_counter(
            "hubos_core_dag_fallback_executor_total",
            "DAG executor fallbacks (camel -> native)",
            ["node_id", "reason"],
        )
        self.register_counter(
            "hubos_core_dag_node_dispatch_total",
            "Total DAG node dispatches",
            ["node_role", "executor"],
        )
        self.register_counter(
            "hubos_core_dag_node_complete_total",
            "Total DAG node completions",
            ["node_role", "executor", "status"],
        )
        self.register_counter(
            "hubos_core_dag_completed_total",
            "Total DAG executions completed",
            ["status"],
        )

        # Week 13.5: Step 7 - Cross-Task Learning Metrics
        self.register_counter(
            "hubos_core_learning_pattern_hit_total",
            "Total knowledge graph pattern hits",
            ["pattern_id", "pattern_type", "scope"],
        )
        self.register_gauge(
            "hubos_core_learning_pattern_success_rate",
            "Success rate per pattern",
            ["pattern_id", "scope"],
        )
        self.register_counter(
            "hubos_core_policy_transfer_attempt_total",
            "Total policy transfer attempts",
            ["source_scope", "target_scope", "status"],
        )
        self.register_counter(
            "hubos_core_policy_transfer_applied_total",
            "Total policy transfers applied",
            ["transfer_id", "rollout_mode"],
        )
        self.register_counter(
            "hubos_core_policy_transfer_rejected_total",
            "Total policy transfers rejected",
            ["transfer_id", "reason"],
        )
        self.register_counter(
            "hubos_core_memory_pollution_block_total",
            "Total memory pollution blocks",
            ["reason", "source_model"],
        )
        self.register_counter(
            "hubos_core_memory_demotion_total",
            "Total memory demotions",
            ["reason", "actor"],
        )
        self.register_counter(
            "hubos_core_optimizer_run_total",
            "Total optimizer runs",
            ["trigger", "dry_run", "status"],
        )
        self.register_counter(
            "hubos_core_optimizer_rollback_total",
            "Total optimizer rollbacks",
            ["reason"],
        )
        self.register_gauge(
            "hubos_core_optimizer_gain_score",
            "Optimizer gain score (before/after)",
            ["scope"],
        )

        # Step 8: Org-level Autonomy Metrics
        self.register_gauge(
            "hubos_core_org_objective_ontrack_ratio",
            "Ratio of objectives that are on track",
            ["scope"],
        )
        self.register_counter(
            "hubos_core_org_objective_atrisk_total",
            "Total objectives that are at risk",
            ["scope", "severity"],
        )
        self.register_counter(
            "hubos_core_org_resource_arbitration_total",
            "Total resource arbitrations",
            ["resource_type", "decision"],
        )
        self.register_counter(
            "hubos_core_org_resource_deferred_total",
            "Total resource deferrals",
            ["resource_type", "reason"],
        )
        self.register_counter(
            "hubos_core_org_negotiation_success_total",
            "Total successful negotiations",
            ["channel", "topic_type"],
        )
        self.register_counter(
            "hubos_core_org_negotiation_timeout_total",
            "Total negotiation timeouts",
            ["channel"],
        )
        self.register_counter(
            "hubos_core_org_policy_activation_total",
            "Total policy activations",
            ["policy_type", "scope"],
        )
        self.register_counter(
            "hubos_core_org_policy_rollback_total",
            "Total policy rollbacks",
            ["policy_id", "reason"],
        )
        self.register_counter(
            "hubos_core_org_cogovernance_auto_action_total",
            "Total auto governance actions",
            ["risk_level"],
        )
        self.register_counter(
            "hubos_core_org_cogovernance_human_required_total",
            "Total human-required governance actions",
            ["risk_level"],
        )

        # Histograms
        self.register_histogram(
            "hubos_core_planning_latency_ms",
            "Planning phase latency in milliseconds",
            [10, 50, 100, 200, 500, 1000, 2000, 5000],
            [],
        )
        self.register_histogram(
            "hubos_core_task_completion_time_ms",
            "Task completion time in milliseconds",
            [100, 500, 1000, 2000, 5000, 10000, 30000, 60000],
            [],
        )
        self.register_histogram(
            "hubos_core_merge_latency_ms",
            "Merge phase latency in milliseconds",
            [10, 50, 100, 200, 500, 1000],
            [],
        )
        self.register_histogram(
            "hubos_core_worker_execution_latency_ms",
            "Worker execution latency in milliseconds",
            [50, 100, 200, 500, 1000, 2000, 5000, 10000],
            ["provider"],
        )
        self.register_histogram(
            "hubos_core_api_request_latency_ms",
            "API request latency in milliseconds",
            [5, 10, 25, 50, 100, 250, 500, 1000],
            ["endpoint", "method"],
        )
        self.register_histogram(
            "hubos_core_reflection_latency_ms",
            "Reflection engine latency in milliseconds",
            [10, 25, 50, 100, 250, 500, 1000],
            [],
        )

        # Counters
        self.register_counter(
            "hubos_core_api_requests_total",
            "Total API requests",
            ["endpoint", "method", "status"],
        )
        self.register_counter(
            "hubos_core_rate_limited_total",
            "Total requests rate limited",
            ["client_id", "scope"],
        )
        self.register_counter(
            "hubos_core_shutdown_drain_total",
            "Total shutdown drain operations",
            ["result"],
        )
        self.register_counter(
            "hubos_core_worker_executions_total",
            "Total number of worker executions",
            ["provider", "status"],
        )
        self.register_counter(
            "hubos_core_human_gate_tasks_total",
            "Total number of human gate tasks",
            ["status"],
        )
        self.register_counter(
            "hubos_core_dlq_entries_total",
            "Total number of DLQ entries",
            ["action"],
        )
        self.register_counter(
            "hubos_core_collaboration_messages_total",
            "Total number of collaboration messages",
            ["message_type", "direction"],
        )

    def register_counter(
        self,
        name: str,
        description: str,
        label_names: list[str],
    ) -> None:
        """Register a counter metric."""
        key = self._metric_key(name, label_names)
        if key not in self._counters:
            self._counters[key] = Counter(
                name=name,
                description=description,
                labels={ln: "" for ln in label_names},
            )

    def register_gauge(
        self,
        name: str,
        description: str,
        label_names: list[str],
    ) -> None:
        """Register a gauge metric."""
        key = self._metric_key(name, label_names)
        if key not in self._gauges:
            self._gauges[key] = Gauge(
                name=name,
                description=description,
                labels={ln: "" for ln in label_names},
            )

    def register_histogram(
        self,
        name: str,
        description: str,
        buckets: list[float],
        label_names: list[str],
    ) -> None:
        """Register a histogram metric."""
        key = self._metric_key(name, label_names)
        if key not in self._histograms:
            self._histograms[key] = Histogram(
                name=name,
                description=description,
                buckets=buckets,
                labels={ln: "" for ln in label_names},
            )

    def _metric_key(self, name: str, label_names: list[str]) -> str:
        """Generate metric key from name and label names (for registration)."""
        return f"{name}:{','.join(sorted(label_names))}"

    def _metric_key_with_values(self, name: str, labels: dict[str, str]) -> str:
        """Generate metric key from name and label values (for operations)."""
        return f"{name}:{','.join(f'{k}={v}' for k, v in sorted(labels.items()))}"

    # ==================== Metric Operations ====================

    def increment_counter(
        self,
        name: str,
        labels: Optional[dict[str, str]] = None,
        value: float = 1.0,
    ) -> None:
        """Increment a counter."""
        key = self._metric_key_with_values(name, labels or {})
        if key in self._counters:
            self._counters[key].value += value

    def set_gauge(
        self,
        name: str,
        value: float,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Set a gauge value."""
        key = self._metric_key_with_values(name, labels or {})
        if key in self._gauges:
            self._gauges[key].value = value

    def observe_histogram(
        self,
        name: str,
        value: float,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Observe a value in a histogram."""
        key = self._metric_key_with_values(name, labels or {})
        if key in self._histograms:
            self._histograms[key].observe(value)

    # ==================== Convenience Methods ====================

    def record_worker_execution(
        self,
        provider: str,
        success: bool,
        latency_ms: float,
    ) -> None:
        """Record a worker execution."""
        status = "success" if success else "failure"
        self.increment_counter(
            "hubos_core_worker_executions_total",
            {"provider": provider, "status": status},
        )
        self.observe_histogram(
            "hubos_core_worker_execution_latency_ms",
            latency_ms,
            {"provider": provider},
        )

        # Update success rate gauge (simplified)
        key = self._metric_key_with_values("hubos_core_worker_success_rate", {"provider": provider})
        if key in self._gauges:
            counter_key = self._metric_key_with_values("hubos_core_worker_executions_total", {"provider": provider, "status": "success"})
            total_key = self._metric_key_with_values("hubos_core_worker_executions_total", {"provider": provider, "status": "failure"})
            successes = self._counters.get(counter_key, Counter(name="", description="")).value
            failures = self._counters.get(total_key, Counter(name="", description="")).value
            total = successes + failures
            if total > 0:
                self._gauges[key].value = successes / total

    def record_planning_latency(self, latency_ms: float) -> None:
        """Record planning phase latency."""
        self.observe_histogram("hubos_core_planning_latency_ms", latency_ms)

    def record_merge_latency(self, latency_ms: float, has_conflict: bool) -> None:
        """Record merge phase latency and conflict."""
        self.observe_histogram("hubos_core_merge_latency_ms", latency_ms)
        if has_conflict:
            self.increment_counter("hubos_core_merge_conflict_total")

    def record_task_completion(self, latency_ms: float) -> None:
        """Record task completion time."""
        self.observe_histogram("hubos_core_task_completion_time_ms", latency_ms)

    def record_human_gate_task(self, status: str) -> None:
        """Record human gate task event."""
        self.increment_counter(
            "hubos_core_human_gate_tasks_total",
            {"status": status},
        )

    def update_memory_metrics(
        self,
        local_hit_rate: float,
        hermes_hit_rate: float,
        hermes_sync_rate: float,
    ) -> None:
        """Update memory-related metrics."""
        self.set_gauge("hubos_core_memory_local_hit_rate", local_hit_rate)
        self.set_gauge("hubos_core_memory_hermes_hit_rate", hermes_hit_rate)
        self.set_gauge("hubos_core_memory_hermes_sync_success_rate", hermes_sync_rate)

    # ==================== Week 11: Approval Gate Metrics ====================

    def record_approval_request(
        self,
        action: str,
        level: str,
        status: str,
    ) -> None:
        """Record an approval request event."""
        self.increment_counter(
            "hubos_core_approval_requests_total",
            {"action": action, "level": level, "status": status},
        )

    def update_approval_pending(self, level: str, count: int) -> None:
        """Update pending approval count."""
        self.set_gauge(
            "hubos_core_approval_pending",
            float(count),
            {"level": level},
        )

    def record_break_glass(self, action: str) -> None:
        """Record break-glass bypass usage."""
        self.increment_counter(
            "hubos_core_break_glass_used_total",
            {"action": action},
        )

    def record_approval_latency(self, latency_ms: float) -> None:
        """Record approval request lifecycle latency."""
        self.observe_histogram("hubos_core_approval_latency_ms", latency_ms)

    # ==================== Week 11: Internal FinOps Metrics ====================

    def record_cost(
        self,
        team_id: str,
        resource_type: str,
        provider: str,
        cost: float,
    ) -> None:
        """Record a cost tracking event."""
        self.increment_counter(
            "hubos_core_cost_tracked_total",
            {"team_id": team_id, "resource_type": resource_type, "provider": provider},
            cost,
        )

    def update_budget_utilization(
        self,
        team_id: str,
        period: str,
        percent: float,
    ) -> None:
        """Update budget utilization gauge."""
        self.set_gauge(
            "hubos_core_budget_utilization_percent",
            percent,
            {"team_id": team_id, "period": period},
        )

    def record_budget_alert(
        self,
        team_id: str,
        alert_type: str,
    ) -> None:
        """Record a budget alert."""
        self.increment_counter(
            "hubos_core_budget_alerts_total",
            {"team_id": team_id, "alert_type": alert_type},
        )

    # ==================== Week 11: SOP Metrics ====================

    def record_sop_execution(
        self,
        sop_id: str,
        category: str,
        status: str,
    ) -> None:
        """Record an SOP execution."""
        self.increment_counter(
            "hubos_core_sop_executions_total",
            {"sop_id": sop_id, "category": category, "status": status},
        )

    def record_sop_duration(
        self,
        sop_id: str,
        duration_ms: float,
    ) -> None:
        """Record SOP execution duration."""
        self.observe_histogram(
            "hubos_core_sop_execution_duration_ms",
            duration_ms,
            {"sop_id": sop_id},
        )

    def record_sop_step_failure(
        self,
        sop_id: str,
        step: int,
    ) -> None:
        """Record an SOP step failure."""
        self.increment_counter(
            "hubos_core_sop_step_failures_total",
            {"sop_id": sop_id, "step": str(step)},
        )

    # ==================== V1.5: Agent Factory Metrics ====================

    def record_agent_routing_decision(
        self,
        agent_id: str,
        strategy: str,
        outcome: str,
    ) -> None:
        """Record an agent routing decision."""
        self.increment_counter(
            "hubos_core_agent_routing_decision_total",
            {"agent_id": agent_id, "strategy": strategy, "outcome": outcome},
        )

    def record_agent_routing_score(
        self,
        agent_id: str,
        score_type: str,
        score: float,
    ) -> None:
        """Record an agent routing score component."""
        self.observe_histogram(
            "hubos_core_agent_routing_score",
            score,
            {"agent_id": agent_id, "score_type": score_type},
        )

    def update_agent_runtime_success_rate(
        self,
        agent_id: str,
        role: str,
        success_rate: float,
    ) -> None:
        """Update agent runtime success rate gauge."""
        self.set_gauge(
            "hubos_core_agent_runtime_success_rate",
            success_rate,
            {"agent_id": agent_id, "role": role},
        )

    def record_agent_execution_latency(
        self,
        agent_id: str,
        role: str,
        latency_ms: float,
    ) -> None:
        """Record agent execution latency."""
        self.observe_histogram(
            "hubos_core_agent_execution_latency_ms",
            latency_ms,
            {"agent_id": agent_id, "role": role},
        )

    def record_agent_execution(
        self,
        agent_id: str,
        role: str,
        outcome: str,
    ) -> None:
        """Record an agent execution outcome."""
        self.increment_counter(
            "hubos_core_agent_execution_total",
            {"agent_id": agent_id, "role": role, "outcome": outcome},
        )

    def record_agent_canary_routing(
        self,
        agent_id: str,
        rollout_mode: str,
    ) -> None:
        """Record a canary routing decision."""
        self.increment_counter(
            "hubos_core_agent_rollout_canary_total",
            {"agent_id": agent_id, "rollout_mode": rollout_mode},
        )

    def record_agent_shadow_execution(
        self,
        agent_id: str,
        rollout_mode: str,
    ) -> None:
        """Record a shadow mode execution."""
        self.increment_counter(
            "hubos_core_agent_rollout_shadow_total",
            {"agent_id": agent_id, "rollout_mode": rollout_mode},
        )

    # ==================== Week 12: Execution Loop MVP Metrics ====================

    def record_task_submit(self) -> None:
        """Record a task submission."""
        self.increment_counter("hubos_core_task_submit_total")

    def record_task_execution_duration(self, duration_ms: float) -> None:
        """Record task execution duration."""
        self.observe_histogram("hubos_core_task_execution_duration_ms", duration_ms)

    def record_task_success(self) -> None:
        """Record a successful task completion."""
        self.increment_counter("hubos_core_task_success_total")

    def record_task_failure(self) -> None:
        """Record a failed task."""
        self.increment_counter("hubos_core_task_failed_total")

    def record_task_human_gate(self) -> None:
        """Record a task entering human gate."""
        self.increment_counter("hubos_core_task_human_gate_total")

    def record_task_stage_duration(self, stage: str, duration_ms: float) -> None:
        """Record task stage execution duration."""
        self.observe_histogram("hubos_core_task_stage_duration_ms", duration_ms, {"stage": stage})

    def update_task_queue_depth(self, depth: int) -> None:
        """Update task queue depth gauge."""
        self.set_gauge("hubos_core_task_queue_depth", float(depth))

    # ==================== Week 14: WeChat Embedded Plugin Metrics ====================

    def record_wechat_login_start(self) -> None:
        """Record a WeChat QR login start."""
        self.increment_counter("hubos_core_wechat_login_start_total")

    def record_wechat_login_success(self) -> None:
        """Record a successful WeChat login."""
        self.increment_counter("hubos_core_wechat_login_success_total")

    def record_wechat_login_failed(self) -> None:
        """Record a failed WeChat login."""
        self.increment_counter("hubos_core_wechat_login_failed_total")

    def record_wechat_inbound_message(self, account_id: str) -> None:
        """Record an inbound WeChat message."""
        self.increment_counter(
            "hubos_core_wechat_inbound_message_total",
            {"account_id": account_id},
        )

    def record_wechat_outbound_message(self, account_id: str) -> None:
        """Record an outbound WeChat message."""
        self.increment_counter(
            "hubos_core_wechat_outbound_message_total",
            {"account_id": account_id},
        )

    def record_wechat_inbound_dedup(self, account_id: str) -> None:
        """Record a deduplicated inbound WeChat message."""
        self.increment_counter(
            "hubos_core_wechat_inbound_dedup_total",
            {"account_id": account_id},
        )

    def record_wechat_poller_error(self, account_id: str) -> None:
        """Record a WeChat poller error."""
        self.increment_counter(
            "hubos_core_wechat_poller_error_total",
            {"account_id": account_id},
        )

    def record_wechat_poller_latency(self, account_id: str, latency_ms: float) -> None:
        """Record WeChat poller latency."""
        self.observe_histogram(
            "hubos_core_wechat_poller_latency_ms",
            latency_ms,
            {"account_id": account_id},
        )

    def update_wechat_poller_running(self, count: int) -> None:
        """Update number of running WeChat pollers."""
        self.set_gauge("hubos_core_wechat_poller_running_accounts", float(count))

    # ==================== Week 13.5: Parallel Core V1.5 Step 4 Metrics ====================

    def record_parallel_backend_real(self, backend: str = "camel") -> None:
        """Record execution using real CAMEL backend."""
        self.increment_counter(
            "hubos_core_parallel_backend_real_total",
            {"backend": backend},
        )

    def record_parallel_fallback(self, from_backend: str, to_backend: str) -> None:
        """Record backend fallback from one backend to another."""
        self.increment_counter(
            "hubos_core_parallel_fallback_total",
            {"from_backend": from_backend, "to_backend": to_backend},
        )

    def record_parallel_branch_duplicate_prevented(self, branch_id: str) -> None:
        """Record that a duplicate branch execution was prevented."""
        self.increment_counter(
            "hubos_core_parallel_branch_duplicate_prevented_total",
            {"branch_id": branch_id},
        )

    def record_parallel_recovery_resume(self, task_id: str, source: str) -> None:
        """Record that execution was resumed from recovery."""
        self.increment_counter(
            "hubos_core_parallel_recovery_resume_total",
            {"task_id": task_id, "source": source},
        )

    def record_parallel_human_gate_open(self, task_id: str, branch_id: Optional[str] = None) -> None:
        """Record a human gate opening for a parallel branch or merge."""
        self.increment_counter(
            "hubos_core_parallel_human_gate_open_total",
            {"task_id": task_id, "branch_id": branch_id or "merge"},
        )

    def record_parallel_human_gate_resolved(
        self, task_id: str, branch_id: Optional[str] = None, action: str = "approved"
    ) -> None:
        """Record a human gate resolution."""
        self.increment_counter(
            "hubos_core_parallel_human_gate_resolved_total",
            {"task_id": task_id, "branch_id": branch_id or "merge", "action": action},
        )

    def record_parallel_merge_timeout(self, task_id: str, merge_id: str) -> None:
        """Record a merge timeout event."""
        self.increment_counter(
            "hubos_core_parallel_merge_timeout_total",
            {"task_id": task_id, "merge_id": merge_id},
        )

    def record_parallel_branch_dispatch(self, branch_id: str, role: str, backend: str) -> None:
        """Record a parallel branch dispatch event."""
        self.increment_counter(
            "hubos_core_parallel_branch_dispatch_total",
            {"branch_id": branch_id, "role": role, "backend": backend},
        )

    def record_parallel_branch_complete(
        self, branch_id: str, role: str, backend: str, status: str
    ) -> None:
        """Record a parallel branch completion event."""
        self.increment_counter(
            "hubos_core_parallel_branch_complete_total",
            {"branch_id": branch_id, "role": role, "backend": backend, "status": status},
        )

    def record_parallel_merge_start(self, merge_id: str, required_branches: int) -> None:
        """Record a parallel merge start event."""
        self.increment_counter(
            "hubos_core_parallel_merge_start_total",
            {"merge_id": merge_id, "required_branches": str(required_branches)},
        )

    def record_parallel_merge_complete(self, merge_id: str, status: str) -> None:
        """Record a parallel merge completion event."""
        self.increment_counter(
            "hubos_core_parallel_merge_complete_total",
            {"merge_id": merge_id, "status": status},
        )

    # ==================== DAG-native Metrics (Step 5) ====================

    def record_dag_node_dispatch(self, node_id: str, role: str, executor: str) -> None:
        """Record a DAG node dispatch."""
        self.increment_counter(
            "hubos_core_dag_node_dispatch_total",
            {"node_role": role, "executor": executor},
        )
        self.increment_gauge("hubos_core_dag_nodes_running", {}, 1)

    def record_dag_node_complete(self, node_id: str, role: str, executor: str, status: str, duration_ms: float) -> None:
        """Record a DAG node completion."""
        self.increment_counter(
            "hubos_core_dag_node_complete_total",
            {"node_role": role, "executor": executor, "status": status},
        )
        self.increment_gauge("hubos_core_dag_nodes_running", {}, -1)
        self.record_histogram("hubos_core_dag_node_latency_ms", duration_ms, {"node_role": role, "executor": executor})

    def record_dag_retry(self, node_id: str, reason: str) -> None:
        """Record a DAG node retry."""
        self.increment_counter(
            "hubos_core_dag_retry_total",
            {"node_id": node_id, "reason": reason},
        )

    def record_dag_human_gate(self, node_id: str, action: str) -> None:
        """Record a DAG human gate intervention."""
        self.increment_counter(
            "hubos_core_dag_human_gate_total",
            {"node_id": node_id, "action": action},
        )

    def record_dag_merge_wait(self, wait_ms: float) -> None:
        """Record DAG merge wait time."""
        self.record_histogram("hubos_core_dag_merge_wait_ms", wait_ms, {})

    def record_dag_executor_selection(self, executor: str, reason: str) -> None:
        """Record DAG executor selection decision."""
        self.increment_counter(
            "hubos_core_dag_executor_selection_total",
            {"executor": executor, "reason": reason},
        )

    def record_dag_duplicate_prevented(self, node_id: str, instance_id: str) -> None:
        """Record a duplicate dispatch was prevented."""
        self.increment_counter(
            "hubos_core_dag_duplicate_prevented_total",
            {"node_id": node_id, "instance_id": instance_id},
        )

    def record_dag_fallback_executor(self, node_id: str, reason: str) -> None:
        """Record executor fallback (e.g., camel -> native)."""
        self.increment_counter(
            "hubos_core_dag_fallback_executor_total",
            {"node_id": node_id, "reason": reason},
        )

    def record_dag_completed(self, status: str) -> None:
        """Record a DAG execution completed."""
        self.increment_counter("hubos_core_dag_completed_total", {"status": status})

    def set_dag_active_plans(self, count: int) -> None:
        """Set the number of active DAG plans."""
        self.set_gauge("hubos_core_dag_active_plans", {}, count)

    # ==================== Prometheus Export ====================

    def export_prometheus(self) -> str:
        """
        Export all metrics in Prometheus text format.

        Returns:
            Prometheus-formatted metrics string.
        """
        lines: list[str] = []

        # Add timestamp
        timestamp = int(time.time() * 1000)

        # Export counters
        for counter in self._counters.values():
            lines.append(f"# HELP {counter.name} {counter.description}")
            lines.append(f"# TYPE {counter.name} counter")

            if counter.labels:
                # Multi-label counter
                labels_str = ",".join(f'{k}="{v}"' for k, v in counter.labels.items() if v)
                if labels_str:
                    lines.append(f"{counter.name}{{{labels_str}}} {counter.value} {timestamp}")
                else:
                    lines.append(f"{counter.name} {counter.value} {timestamp}")
            else:
                lines.append(f"{counter.name} {counter.value} {timestamp}")

        # Export gauges
        for gauge in self._gauges.values():
            lines.append(f"# HELP {gauge.name} {gauge.description}")
            lines.append(f"# TYPE {gauge.name} gauge")

            if any(gauge.labels.values()):
                labels_str = ",".join(f'{k}="{v}"' for k, v in gauge.labels.items() if v)
                if labels_str:
                    lines.append(f"{gauge.name}{{{labels_str}}} {gauge.value} {timestamp}")
                else:
                    lines.append(f"{gauge.name} {gauge.value} {timestamp}")
            else:
                lines.append(f"{gauge.name} {gauge.value} {timestamp}")

        # Export histograms
        for histogram in self._histograms.values():
            lines.append(f"# HELP {histogram.name} {histogram.description}")
            lines.append(f"# TYPE {histogram.name} histogram")

            if not histogram.values:
                continue

            # Calculate bucket counts
            values_sorted = sorted(histogram.values)
            total = len(values_sorted)

            for bucket in histogram.buckets:
                count = sum(1 for v in values_sorted if v <= bucket)
                bucket_label = f'le="{bucket}"'
                lines.append(f"{histogram.name}_bucket{{{bucket_label}}} {count} {timestamp}")

            # +Inf bucket
            lines.append(f"{histogram.name}_bucket{{le=\"+Inf\"}} {total} {timestamp}")

            # Sum and count
            lines.append(f"{histogram.name}_sum {sum(values_sorted)} {timestamp}")
            lines.append(f"{histogram.name}_count {total} {timestamp}")

        # Add uptime
        uptime_seconds = time.time() - self._start_time
        lines.append(f"# HELP hubos_core_uptime_seconds_seconds Service uptime")
        lines.append(f"# TYPE hubos_core_uptime_seconds_seconds gauge")
        lines.append(f"hubos_core_uptime_seconds {uptime_seconds} {timestamp}")

        return "\n".join(lines) + "\n"


# Global metrics service instance
_metrics_service: Optional[MetricsService] = None


def get_metrics_service() -> MetricsService:
    """Get the global metrics service instance."""
    global _metrics_service
    if _metrics_service is None:
        _metrics_service = MetricsService()
    return _metrics_service
