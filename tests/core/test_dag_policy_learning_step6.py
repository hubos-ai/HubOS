#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for DAG Policy Learning - Parallel Core V1.5 Step 6."""

import pytest
import time
from hubos.core.dag.policy_learning import (
    PolicyLearningEngine,
    PolicyBucket,
    LearnedPolicy,
)


class TestPolicyLearningEngine:
    """Test policy learning engine."""

    def test_initialization(self):
        """Test engine initializes correctly."""
        engine = PolicyLearningEngine()
        assert len(engine._buckets) == 0
        assert len(engine._policies) == 0

    def test_record_execution_creates_bucket(self):
        """Test recording execution creates a bucket."""
        engine = PolicyLearningEngine()

        engine.record_execution(
            role="dev",
            task_type="code",
            executor="native",
            success=True,
            retry_count=1,
            timeout_ms=60000,
            parallelism_used=5,
        )

        bucket_key = "role=dev,task_type=code"
        assert bucket_key in engine._buckets
        bucket = engine._buckets[bucket_key]
        assert bucket.total_runs == 1
        assert bucket.total_failures == 0

    def test_policy_not_calculated_until_min_samples(self):
        """Test policy is not calculated until minimum samples."""
        engine = PolicyLearningEngine()

        # Record fewer than 5 samples
        for i in range(4):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=1,
                timeout_ms=60000,
                parallelism_used=5,
            )

        bucket_key = "role=dev,task_type=code"
        assert bucket_key not in engine._policies

    def test_policy_calculated_after_min_samples(self):
        """Test policy is calculated after minimum samples."""
        engine = PolicyLearningEngine()

        # Record 5 samples
        for i in range(5):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=1,
                timeout_ms=60000,
                parallelism_used=5,
            )

        bucket_key = "role=dev,task_type=code"
        assert bucket_key in engine._policies
        policy = engine._policies[bucket_key]
        assert policy.bucket_key == bucket_key
        assert policy.based_on_samples == 5

    def test_learns_optimal_retry_count(self):
        """Test engine learns optimal retry count."""
        engine = PolicyLearningEngine()

        # Record varied retry counts
        retry_counts = [1, 2, 2, 3, 2, 1, 2, 2, 2, 3]
        for rc in retry_counts:
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=rc,
                timeout_ms=60000,
                parallelism_used=5,
            )

        bucket_key = "role=dev,task_type=code"
        policy = engine._policies[bucket_key]
        # Median of [1,2,2,3,2,1,2,2,2,3] is 2
        assert policy.recommended_retry_count == 2

    def test_learns_optimal_executor(self):
        """Test engine learns best executor."""
        engine = PolicyLearningEngine()

        # native succeeds more often
        for _ in range(8):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=1,
                timeout_ms=60000,
                parallelism_used=5,
            )

        # camel fails more often
        for _ in range(4):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="camel",
                success=False,
                retry_count=1,
                timeout_ms=60000,
                parallelism_used=5,
            )

        bucket_key = "role=dev,task_type=code"
        policy = engine._policies[bucket_key]
        assert policy.recommended_executor == "native"

    def test_rollout_modes(self):
        """Test rollout mode setting."""
        engine = PolicyLearningEngine()

        bucket_key = "role=dev,task_type=code"

        # Set rollout mode
        assert engine.set_rollout_mode(bucket_key, "shadow") is True
        assert engine.get_rollout_status(bucket_key) == "shadow"

        # Set to canary
        assert engine.set_rollout_mode(bucket_key, "canary") is True
        assert engine.get_rollout_status(bucket_key) == "canary"

        # Invalid mode rejected
        assert engine.set_rollout_mode(bucket_key, "invalid") is False

    def test_rollback_policy(self):
        """Test policy rollback."""
        engine = PolicyLearningEngine()

        bucket_key = "role=dev,task_type=code"
        engine.set_rollout_mode(bucket_key, "full")
        engine.rollback_policy(bucket_key)

        assert engine.get_rollout_status(bucket_key) == "shadow"

    def test_disable_policy(self):
        """Test disabling policy."""
        engine = PolicyLearningEngine()

        bucket_key = "role=dev,task_type=code"
        engine.set_rollout_mode(bucket_key, "full")
        engine.disable_policy(bucket_key)

        assert engine.get_rollout_status(bucket_key) == "off"

    def test_get_policy_suggestion(self):
        """Test getting policy suggestion."""
        engine = PolicyLearningEngine()

        # Record enough samples to get a policy
        for _ in range(10):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=2,
                timeout_ms=60000,
                parallelism_used=5,
            )

        suggestion = engine.get_policy_suggestion("dev", "code")
        assert suggestion is not None
        assert suggestion.bucket_key == "role=dev,task_type=code"

    def test_get_policy_suggestion_missing(self):
        """Test getting suggestion for unknown bucket."""
        engine = PolicyLearningEngine()

        suggestion = engine.get_policy_suggestion("unknown", "role")
        assert suggestion is None

    def test_export_import_policy(self):
        """Test policy export and import."""
        engine = PolicyLearningEngine()

        # Create a policy
        for _ in range(10):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=2,
                timeout_ms=60000,
                parallelism_used=5,
            )

        bucket_key = "role=dev,task_type=code"
        exported = engine.export_policy(bucket_key)
        assert exported is not None
        assert exported["bucket_key"] == bucket_key

        # Create new engine and import
        engine2 = PolicyLearningEngine()
        engine2.import_policy(exported)

        policy2 = engine2.get_policy_suggestion("dev", "code")
        assert policy2 is not None
        assert policy2.recommended_retry_count == 2

    def test_get_all_policies(self):
        """Test getting all policies."""
        engine = PolicyLearningEngine()

        # Create multiple buckets
        for _ in range(10):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=2,
                timeout_ms=60000,
                parallelism_used=5,
            )
        for _ in range(10):
            engine.record_execution(
                role="review",
                task_type="review",
                executor="camel",
                success=True,
                retry_count=1,
                timeout_ms=30000,
                parallelism_used=3,
            )

        all_policies = engine.get_all_policies()
        assert len(all_policies) == 2
        assert "role=dev,task_type=code" in all_policies
        assert "role=review,task_type=review" in all_policies

    def test_confidence_increases_with_samples(self):
        """Test confidence increases with more samples."""
        engine = PolicyLearningEngine()

        # Add 10 samples
        for i in range(10):
            engine.record_execution(
                role="dev",
                task_type="code",
                executor="native",
                success=True,
                retry_count=1,
                timeout_ms=60000,
                parallelism_used=5,
            )

        bucket_key = "role=dev,task_type=code"
        policy = engine._policies[bucket_key]
        # confidence = min(1.0, bucket.total_runs / 50.0)
        # For 10 samples: 10/50 = 0.2
        assert policy.confidence == 0.2
        assert policy.based_on_samples == 10
