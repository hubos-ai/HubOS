"""Smart Executor Selector for DAG-native Step 6.

Selects optimal executor based on node hints, learned policies, and real-time metrics.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ExecutorMetrics:
    """Per-executor performance metrics."""
    executor: str
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0
    timed_out_runs: int = 0
    total_latency_ms: float = 0.0
    last_used: float = 0.0

    @property
    def success_rate(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.successful_runs / self.total_runs

    @property
    def avg_latency_ms(self) -> float:
        if self.total_runs == 0:
            return 0.0
        return self.total_latency_ms / self.total_runs


@dataclass
class SelectionResult:
    """Result of executor selection."""
    selected_executor: str
    selection_reason: str
    confidence: float  # 0.0 to 1.0
    fallback_executor: Optional[str] = None
    alternatives: list[str] = field(default_factory=list)


class ExecutorSelector:
    """Smart executor selector with learning and fallback capabilities."""

    def __init__(
        self,
        default_executor: str = "native",
        enable_auto_switch: bool = False,
    ) -> None:
        self._default = default_executor
        self._enable_auto_switch = enable_auto_switch
        self._executor_metrics: dict[str, ExecutorMetrics] = {}
        self._node_hint: dict[str, str] = {}  # node_id -> executor hint
        self._policy_recommendation: dict[str, str] = {}  # role -> executor recommendation
        self._selection_history: list[dict] = []

    def set_node_hint(self, node_id: str, executor: str) -> None:
        """Set executor hint for a specific node."""
        self._node_hint[node_id] = executor

    def set_policy_recommendation(self, role: str, executor: str) -> None:
        """Set policy recommendation for a role."""
        self._policy_recommendation[role] = executor

    def record_execution(
        self,
        executor: str,
        success: bool,
        latency_ms: float,
        timed_out: bool = False,
    ) -> None:
        """Record execution outcome for learning."""
        if executor not in self._executor_metrics:
            self._executor_metrics[executor] = ExecutorMetrics(executor=executor)

        metrics = self._executor_metrics[executor]
        metrics.total_runs += 1
        metrics.total_latency_ms += latency_ms
        metrics.last_used = time.time()

        if success:
            metrics.successful_runs += 1
        elif timed_out:
            metrics.timed_out_runs += 1
        else:
            metrics.failed_runs += 1

    def select(
        self,
        node_id: str,
        role: str,
        executor_hint: Optional[str] = None,
    ) -> SelectionResult:
        """Select best executor for a node.

        Selection priority:
        1. node.executor_hint (if provided)
        2. Policy recommendation (if available)
        3. Historical best performer
        4. Default executor

        Args:
            node_id: Node identifier
            role: Node role
            executor_hint: Optional executor hint from node definition

        Returns:
            SelectionResult with selected executor and reasoning
        """
        candidates = list(self._executor_metrics.keys())
        if not candidates:
            candidates = ["native", "camel"]

        # Build selection hierarchy
        selection_order = []
        reasons = []
        confidences = []

        # 1. Node hint (highest priority)
        if executor_hint:
            selection_order.append(executor_hint)
            reasons.append(f"node_hint={executor_hint}")
            confidences.append(0.9)
        elif node_id in self._node_hint:
            hint = self._node_hint[node_id]
            selection_order.append(hint)
            reasons.append(f"node_id_hint={hint}")
            confidences.append(0.85)

        # 2. Policy recommendation
        if role in self._policy_recommendation:
            policy_exec = self._policy_recommendation[role]
            if policy_exec not in selection_order:
                selection_order.append(policy_exec)
                reasons.append(f"policy_recommendation={policy_exec}")
                confidences.append(0.75)

        # 3. Historical best performer
        best_historical = self._get_best_historical_executor()
        if best_historical and best_historical not in selection_order:
            selection_order.append(best_historical)
            reasons.append("historical_best")
            confidences.append(0.6)

        # 4. Default
        if self._default not in selection_order:
            selection_order.append(self._default)
            reasons.append(f"default={self._default}")
            confidences.append(0.5)

        # Select primary
        selected = selection_order[0]
        reason = reasons[0]
        confidence = confidences[0]

        # Determine fallback (second choice)
        fallback = None
        if len(selection_order) > 1:
            fallback = selection_order[1]
            if not self._enable_auto_switch:
                fallback = None  # Auto-switch disabled

        # Record selection
        self._selection_history.append({
            "timestamp": time.time(),
            "node_id": node_id,
            "role": role,
            "selected": selected,
            "reason": reason,
            "confidence": confidence,
            "fallback": fallback,
        })

        return SelectionResult(
            selected_executor=selected,
            selection_reason=reason,
            confidence=confidence,
            fallback_executor=fallback,
            alternatives=selection_order[1:],
        )

    def _get_best_historical_executor(self) -> Optional[str]:
        """Get executor with best historical success rate."""
        best_executor = None
        best_score = -1.0

        for exec_name, metrics in self._executor_metrics.items():
            if metrics.total_runs < 3:  # Minimum sample size
                continue

            # Score = success_rate * recency_factor
            recency_factor = 1.0
            if metrics.last_used > 0:
                age_hours = (time.time() - metrics.last_used) / 3600
                recency_factor = max(0.5, 1.0 - (age_hours / 24))  # Decay over 24h

            score = metrics.success_rate * recency_factor

            if score > best_score:
                best_score = score
                best_executor = exec_name

        return best_executor

    def get_selection_history(self, limit: int = 50) -> list[dict]:
        """Get recent selection history."""
        return self._selection_history[-limit:]

    def get_executor_metrics(self) -> dict[str, dict]:
        """Get current metrics for all executors."""
        return {
            name: {
                "total_runs": m.total_runs,
                "successful_runs": m.successful_runs,
                "failed_runs": m.failed_runs,
                "timed_out_runs": m.timed_out_runs,
                "success_rate": m.success_rate,
                "avg_latency_ms": m.avg_latency_ms,
                "last_used": m.last_used,
            }
            for name, m in self._executor_metrics.items()
        }

    def set_auto_switch(self, enabled: bool) -> None:
        """Enable/disable auto-switch on failure."""
        self._enable_auto_switch = enabled

    def should_switch(self, current_executor: str) -> bool:
        """Check if should switch executor on failure."""
        if not self._enable_auto_switch:
            return False

        # Get current executor metrics
        metrics = self._executor_metrics.get(current_executor)
        if metrics is None:
            return True  # No history, try switching

        # If success rate is low, consider switching
        if metrics.success_rate < 0.7 and metrics.total_runs >= 5:
            return True

        return False

    def get_alternative_executor(self, current: str) -> Optional[str]:
        """Get alternative executor to switch to."""
        alternatives = [k for k in self._executor_metrics.keys() if k != current]

        if not alternatives:
            alternatives = ["native", "camel"]
            alternatives.remove(current)

        # Return the one with best success rate
        best = None
        best_rate = -1.0
        for alt in alternatives:
            m = self._executor_metrics.get(alt)
            if m and m.success_rate > best_rate:
                best_rate = m.success_rate
                best = alt

        return best if best else alternatives[0]
