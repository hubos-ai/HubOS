"""Adaptive Parallelism Controller for DAG-native Step 6.

Dynamically adjusts max_parallelism based on system load and failure rates.
"""

import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class ParallelismConfig:
    """Configuration for adaptive parallelism."""
    min_parallelism: int = 1
    max_parallelism: int = 50
    scale_up_threshold: float = 0.3  # Scale up when queue pressure < 30%
    scale_down_threshold: float = 0.8  # Scale down when utilization > 80%
    failure_rate_threshold: float = 0.15  # Scale down when failure rate > 15%
    timeout_rate_threshold: float = 0.1  # Scale down when timeout rate > 10%
    scale_factor: float = 1.5  # How much to scale by
    cooldown_seconds: float = 30.0  # Time between adjustments


@dataclass
class SystemMetrics:
    """Snapshot of system metrics for adaptation."""
    queue_depth: int = 0
    running_nodes: int = 0
    recent_failures: int = 0
    recent_timeouts: int = 0
    recent_total: int = 0
    last_adjustment_time: float = 0.0


class AdaptiveParallelism:
    """Dynamically adjusts parallelism based on system conditions."""

    def __init__(self, config: Optional[ParallelismConfig] = None) -> None:
        self._config = config or ParallelismConfig()
        self._current_parallelism: int = self._config.max_parallelism // 2
        self._metrics = SystemMetrics()
        self._last_adjustment = 0.0
        self._adjustment_history: list[dict] = []

    def get_current_parallelism(self) -> int:
        """Get current recommended parallelism."""
        return self._current_parallelism

    def record_node_started(self) -> None:
        """Record that a node started."""
        self._metrics.running_nodes += 1

    def record_node_completed(self, success: bool, timed_out: bool = False) -> None:
        """Record node completion."""
        self._metrics.running_nodes -= 1
        self._metrics.recent_total += 1

        if not success:
            self._metrics.recent_failures += 1

        if timed_out:
            self._metrics.recent_timeouts += 1

    def record_queue_depth(self, depth: int) -> None:
        """Update queue depth metric."""
        self._metrics.queue_depth = depth

    def should_adjust(self) -> bool:
        """Check if an adjustment should be made."""
        now = time.time()
        if now - self._metrics.last_adjustment_time < self._config.cooldown_seconds:
            return False

        # Need enough samples
        if self._metrics.recent_total < 10:
            return False

        return True

    def calculate_adjustment(self) -> tuple[int, str]:
        """Calculate parallelism adjustment.

        Returns:
            Tuple of (new_parallelism, reason)
        """
        if not self.should_adjust():
            return self._current_parallelism, "cooldown"

        failure_rate = (
            self._metrics.recent_failures / self._metrics.recent_total
            if self._metrics.recent_total > 0 else 0
        )

        timeout_rate = (
            self._metrics.recent_timeouts / self._metrics.recent_total
            if self._metrics.recent_total > 0 else 0
        )

        queue_pressure = (
            self._metrics.queue_depth / self._current_parallelism
            if self._current_parallelism > 0 else 0
        )

        utilization = (
            self._metrics.running_nodes / self._current_parallelism
            if self._current_parallelism > 0 else 0
        )

        new_parallelism = self._current_parallelism
        reason_parts = []

        # Check if should scale down
        if failure_rate > self._config.failure_rate_threshold:
            new_parallelism = int(self._current_parallelism / self._config.scale_factor)
            reason_parts.append(f"failure_rate={failure_rate:.2%}>threshold")
        elif timeout_rate > self._config.timeout_rate_threshold:
            new_parallelism = int(self._current_parallelism / self._config.scale_factor)
            reason_parts.append(f"timeout_rate={timeout_rate:.2%}>threshold")
        elif utilization > self._config.scale_down_threshold:
            # System is busy, but healthy - scale down slightly
            if self._metrics.queue_depth < self._metrics.running_nodes:
                new_parallelism = int(self._current_parallelism / self._config.scale_factor)
                reason_parts.append(f"high_utilization={utilization:.2%}")

        # Check if should scale up
        elif queue_pressure < self._config.scale_up_threshold:
            if self._metrics.queue_depth > self._current_parallelism:
                new_parallelism = min(
                    int(self._current_parallelism * self._config.scale_factor),
                    self._config.max_parallelism
                )
                reason_parts.append(f"queue_pressure={queue_pressure:.2%}<threshold")

        # Apply limits
        new_parallelism = max(
            self._config.min_parallelism,
            min(self._config.max_parallelism, new_parallelism)
        )

        reason = ", ".join(reason_parts) if reason_parts else "no_change"

        return new_parallelism, reason

    def adjust(self) -> tuple[int, str]:
        """Perform adjustment if needed.

        Returns:
            Tuple of (new_parallelism, reason)
        """
        new_val, reason = self.calculate_adjustment()

        if new_val != self._current_parallelism:
            old_val = self._current_parallelism
            self._current_parallelism = new_val
            self._metrics.last_adjustment_time = time.time()

            # Record history
            self._adjustment_history.append({
                "timestamp": time.time(),
                "old_value": old_val,
                "new_value": new_val,
                "reason": reason,
            })

            # Keep only last 100 adjustments
            if len(self._adjustment_history) > 100:
                self._adjustment_history = self._adjustment_history[-100:]

        # Reset counters
        self._metrics.recent_failures = 0
        self._metrics.recent_timeouts = 0
        self._metrics.recent_total = 0

        return self._current_parallelism, reason

    def get_adjustment_history(self, limit: int = 20) -> list[dict]:
        """Get recent adjustment history."""
        return self._adjustment_history[-limit:]

    def get_current_metrics(self) -> dict:
        """Get current system metrics snapshot."""
        return {
            "current_parallelism": self._current_parallelism,
            "queue_depth": self._metrics.queue_depth,
            "running_nodes": self._metrics.running_nodes,
            "recent_failures": self._metrics.recent_failures,
            "recent_timeouts": self._metrics.recent_timeouts,
            "recent_total": self._metrics.recent_total,
            "failure_rate": (
                self._metrics.recent_failures / self._metrics.recent_total
                if self._metrics.recent_total > 0 else 0
            ),
            "timeout_rate": (
                self._metrics.recent_timeouts / self._metrics.recent_total
                if self._metrics.recent_total > 0 else 0
            ),
            "queue_pressure": (
                self._metrics.queue_depth / self._current_parallelism
                if self._current_parallelism > 0 else 0
            ),
        }

    def set_config(self, config: ParallelismConfig) -> None:
        """Update configuration."""
        self._config = config

    def reset(self) -> None:
        """Reset to initial state."""
        self._current_parallelism = self._config.max_parallelism // 2
        self._metrics = SystemMetrics()
        self._adjustment_history = []
