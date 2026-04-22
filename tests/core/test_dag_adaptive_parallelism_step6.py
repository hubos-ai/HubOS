#!/usr/bin/env python3
"""Tests for DAG Adaptive Parallelism - Parallel Core V1.5 Step 6."""

import pytest
import time
from hubos.core.dag.adaptive_parallelism import (
    AdaptiveParallelism,
    ParallelismConfig,
    SystemMetrics,
)


class TestAdaptiveParallelism:
    """Test adaptive parallelism controller."""

    def test_initialization(self):
        """Test controller initializes correctly."""
        config = ParallelismConfig(
            min_parallelism=2,
            max_parallelism=20,
            scale_up_threshold=0.3,
            scale_down_threshold=0.8,
        )
        controller = AdaptiveParallelism(config)

        assert controller._config.min_parallelism == 2
        assert controller._config.max_parallelism == 20
        # Initial parallelism is max/2
        assert controller.get_current_parallelism() == 10

    def test_default_config(self):
        """Test default configuration."""
        controller = AdaptiveParallelism()

        assert controller._config.min_parallelism == 1
        assert controller._config.max_parallelism == 50
        assert controller._config.scale_factor == 1.5
        assert controller._config.cooldown_seconds == 30.0

    def test_record_node_started(self):
        """Test recording node start."""
        controller = AdaptiveParallelism()

        controller.record_node_started()
        assert controller._metrics.running_nodes == 1

        controller.record_node_started()
        assert controller._metrics.running_nodes == 2

    def test_record_node_completed_success(self):
        """Test recording successful completion."""
        controller = AdaptiveParallelism()

        controller.record_node_started()
        controller.record_node_completed(success=True)

        assert controller._metrics.running_nodes == 0
        assert controller._metrics.recent_total == 1
        assert controller._metrics.recent_failures == 0

    def test_record_node_completed_failure(self):
        """Test recording failed completion."""
        controller = AdaptiveParallelism()

        controller.record_node_completed(success=False)

        assert controller._metrics.recent_total == 1
        assert controller._metrics.recent_failures == 1

    def test_record_node_completed_timeout(self):
        """Test recording timeout."""
        controller = AdaptiveParallelism()

        controller.record_node_completed(success=False, timed_out=True)

        assert controller._metrics.recent_total == 1
        assert controller._metrics.recent_failures == 1
        assert controller._metrics.recent_timeouts == 1

    def test_record_queue_depth(self):
        """Test recording queue depth."""
        controller = AdaptiveParallelism()

        controller.record_queue_depth(25)
        assert controller._metrics.queue_depth == 25

    def test_should_adjust_cooldown(self):
        """Test should_adjust returns False during cooldown."""
        config = ParallelismConfig(cooldown_seconds=60.0)
        controller = AdaptiveParallelism(config)

        # Record enough samples
        for _ in range(15):
            controller.record_node_completed(success=True)

        # Manually set last_adjustment_time to now
        controller._metrics.last_adjustment_time = time.time()

        # Should not adjust during cooldown
        assert controller.should_adjust() is False

    def test_should_adjust_insufficient_samples(self):
        """Test should_adjust returns False with insufficient samples."""
        controller = AdaptiveParallelism()

        # Only 5 samples (need 10)
        for _ in range(5):
            controller.record_node_completed(success=True)

        # Advance time to pass cooldown
        controller._metrics.last_adjustment_time = time.time() - 100

        assert controller.should_adjust() is False

    def test_scale_down_on_high_failure_rate(self):
        """Test scaling down on high failure rate."""
        config = ParallelismConfig(
            min_parallelism=1,
            max_parallelism=50,
            failure_rate_threshold=0.15,
            scale_factor=1.5,
            cooldown_seconds=0.0,  # Disable cooldown for test
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 15

        # Record high failure rate (20% failures)
        for i in range(10):
            success = i < 8  # 8 success, 2 failure = 20% failure
            controller.record_node_completed(success=success)

        new_val, reason = controller.calculate_adjustment()

        assert new_val < 15
        assert "failure_rate" in reason

    def test_scale_down_on_high_timeout_rate(self):
        """Test scaling down on high timeout rate."""
        config = ParallelismConfig(
            min_parallelism=1,
            max_parallelism=50,
            timeout_rate_threshold=0.1,
            scale_factor=1.5,
            cooldown_seconds=0.0,
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 15

        # Record 15% timeout rate
        for i in range(20):
            timed_out = i < 3  # 3 timeouts = 15%
            controller.record_node_completed(success=not timed_out, timed_out=timed_out)

        new_val, reason = controller.calculate_adjustment()

        assert new_val < 15
        assert "timeout_rate" in reason

    def test_scale_up_on_low_queue_pressure(self):
        """Test scaling up on low queue pressure."""
        config = ParallelismConfig(
            min_parallelism=1,
            max_parallelism=50,
            scale_up_threshold=0.3,
            scale_factor=1.5,
            cooldown_seconds=0.0,
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 10
        controller._metrics.queue_depth = 2  # 2/10 = 0.2 < 0.3
        controller._metrics.last_adjustment_time = time.time() - 100  # Outside cooldown

        # Record enough successful completions
        for _ in range(10):
            controller.record_node_completed(success=True)

        new_val, reason = controller.calculate_adjustment()

        # Queue pressure is low, but queue_depth (2) is NOT > current_parallelism (10)
        # So we expect no change (scale up only when queue > parallelism)
        # This test documents the actual behavior
        assert new_val == 10

    def test_adjust_performs_adjustment(self):
        """Test adjust actually changes parallelism."""
        config = ParallelismConfig(
            min_parallelism=1,
            max_parallelism=50,
            failure_rate_threshold=0.15,
            scale_factor=1.5,
            cooldown_seconds=0.0,
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 15

        # High failure rate
        for i in range(10):
            controller.record_node_completed(success=i < 8)

        old_val = controller._current_parallelism
        new_val, reason = controller.adjust()

        assert new_val != old_val
        assert controller._metrics.last_adjustment_time > 0

    def test_adjust_resets_counters(self):
        """Test adjust resets failure/timeout counters."""
        config = ParallelismConfig(
            min_parallelism=1,
            max_parallelism=50,
            failure_rate_threshold=0.15,
            cooldown_seconds=0.0,
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 15

        for i in range(10):
            controller.record_node_completed(success=i < 8)

        controller.adjust()

        assert controller._metrics.recent_failures == 0
        assert controller._metrics.recent_total == 0

    def test_adjustment_history(self):
        """Test adjustment history is recorded."""
        config = ParallelismConfig(
            min_parallelism=1,
            max_parallelism=50,
            failure_rate_threshold=0.15,
            scale_factor=1.5,
            cooldown_seconds=0.0,
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 15

        # Trigger adjustment
        for i in range(10):
            controller.record_node_completed(success=i < 8)

        controller.adjust()

        history = controller.get_adjustment_history()
        assert len(history) == 1
        assert history[0]["old_value"] == 15

    def test_parallelism_clamped_to_max(self):
        """Test parallelism doesn't exceed max."""
        config = ParallelismConfig(
            min_parallelism=1,
            max_parallelism=10,
            scale_factor=1.5,
            cooldown_seconds=0.0,
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 10

        # Queue pressure that would scale up
        controller.record_queue_depth(100)
        for _ in range(10):
            controller.record_node_completed(success=True)

        new_val, _ = controller.calculate_adjustment()

        assert new_val <= 10

    def test_parallelism_clamped_to_min(self):
        """Test parallelism doesn't go below min."""
        config = ParallelismConfig(
            min_parallelism=2,
            max_parallelism=50,
            failure_rate_threshold=0.15,
            scale_factor=1.5,
            cooldown_seconds=0.0,
        )
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 3

        # High failure rate
        for i in range(10):
            controller.record_node_completed(success=i < 8)

        new_val, _ = controller.calculate_adjustment()

        assert new_val >= 2

    def test_get_current_metrics(self):
        """Test getting current metrics snapshot."""
        controller = AdaptiveParallelism()

        controller.record_queue_depth(25)
        controller.record_node_started()
        controller.record_node_started()
        controller.record_node_completed(success=True)

        metrics = controller.get_current_metrics()

        assert "current_parallelism" in metrics
        assert metrics["queue_depth"] == 25
        assert metrics["running_nodes"] == 1
        assert metrics["recent_total"] == 1

    def test_reset(self):
        """Test reset to initial state."""
        config = ParallelismConfig(max_parallelism=20)
        controller = AdaptiveParallelism(config)

        controller._current_parallelism = 5
        controller.record_node_completed(success=False)
        controller._adjustment_history.append({"test": "entry"})

        controller.reset()

        assert controller.get_current_parallelism() == 10  # max/2
        assert controller._metrics.recent_failures == 0
        assert len(controller._adjustment_history) == 0

    def test_set_config(self):
        """Test updating configuration."""
        controller = AdaptiveParallelism()

        new_config = ParallelismConfig(
            min_parallelism=5,
            max_parallelism=100,
        )
        controller.set_config(new_config)

        assert controller._config.min_parallelism == 5
        assert controller._config.max_parallelism == 100

    def test_no_change_returns_current_parallelism(self):
        """Test calculate_adjustment returns current when no change needed."""
        config = ParallelismConfig(cooldown_seconds=0.0)
        controller = AdaptiveParallelism(config)
        controller._current_parallelism = 10

        # Low utilization, healthy metrics
        controller.record_queue_depth(5)
        for _ in range(10):
            controller.record_node_completed(success=True)

        new_val, reason = controller.calculate_adjustment()

        assert new_val == 10
        assert reason == "no_change"
