# -*- coding: utf-8 -*-
"""Policy routing integration for execution planning.

Applies learned policy hints to influence worker selection,
parallelism, timeout, and retry strategies.
"""

import logging
from typing import Any, Optional
from uuid import UUID

from hubos.core.schemas.memory import RouteHint, LearnedPolicy

logger = logging.getLogger(__name__)


class PolicyRouter:
    """
    Policy router that applies learned policies to execution plans.

    Per Week 6.5 requirements:
    - Reads learned_policy before planning
    - Adjusts worker priority, parallelism, timeout, retry
    - Records policy hits and effectiveness
    - Can be disabled via config (safe fallback)
    """

    def __init__(
        self,
        reflection_engine: Any,  # ReflectionEngine - avoid circular import
        enabled: bool = True,
    ) -> None:
        """
        Initialize policy router.

        Args:
            reflection_engine: Reference to ReflectionEngine instance.
            enabled: Whether policy routing is enabled.
        """
        self._reflection_engine = reflection_engine
        self._enabled = enabled

        # Metrics
        self._policy_hit_count = 0
        self._policy_miss_count = 0
        self._effective_count = 0

    @property
    def is_enabled(self) -> bool:
        """Check if policy routing is enabled."""
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable policy routing."""
        self._enabled = enabled
        logger.info(
            "Policy routing toggled",
            extra={"enabled": enabled},
        )

    def apply_route_hint(
        self,
        task_input: dict[str, Any],
        plan_params: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Apply route hint to planning parameters.

        Args:
            task_input: Original task input.
            plan_params: Current planning parameters (will be modified).

        Returns:
            Modified plan_params with route hints applied.
        """
        if not self._enabled:
            logger.debug("Policy routing disabled, skipping")
            return plan_params

        route_hint = self._reflection_engine.generate_route_hint(task_input)

        if route_hint is None:
            self._policy_miss_count += 1
            logger.debug(
                "No route hint found",
                extra={"task_input_keys": list(task_input.keys())},
            )
            return plan_params

        # Apply route hint to plan parameters
        self._policy_hit_count += 1

        # Worker priority
        if route_hint.worker_priority:
            current_priority = plan_params.get("worker_priority", [])
            if not current_priority:
                plan_params["worker_priority"] = route_hint.worker_priority
                logger.info(
                    "Applied worker priority from policy",
                    extra={
                        "policy_id": str(route_hint.policy_id)
                        if route_hint.policy_id
                        else None,
                        "worker_priority": route_hint.worker_priority,
                    },
                )

        # Skip providers
        if route_hint.skip_providers:
            current_skip = plan_params.get("skip_providers", [])
            plan_params["skip_providers"] = list(
                set(current_skip + route_hint.skip_providers),
            )

        # Timeout
        if route_hint.timeout_seconds:
            current_timeout = plan_params.get("timeout_seconds", 300)
            # Use the route hint timeout if it's reasonable
            if route_hint.timeout_seconds != 300:  # Not default
                plan_params["timeout_seconds"] = route_hint.timeout_seconds
                logger.info(
                    "Applied timeout from policy",
                    extra={
                        "policy_id": str(route_hint.policy_id)
                        if route_hint.policy_id
                        else None,
                        "timeout_seconds": route_hint.timeout_seconds,
                    },
                )

        # Retry count
        if route_hint.retry_count:
            plan_params["retry_count"] = route_hint.retry_count

        # Parallel mode
        if route_hint.parallel:
            plan_params["parallel"] = True

        # Store the route hint for later effectiveness tracking
        plan_params["_route_hint"] = route_hint

        logger.info(
            "Route hint applied",
            extra={
                "trigger_task_id": route_hint.trigger_task_id,
                "policy_id": str(route_hint.policy_id)
                if route_hint.policy_id
                else None,
                "confidence": route_hint.confidence,
            },
        )

        return plan_params

    def record_execution_outcome(
        self,
        plan_params: dict[str, Any],
        was_successful: bool,
        confidence: float,
    ) -> None:
        """
        Record execution outcome for policy effectiveness tracking.

        Args:
            plan_params: Plan parameters that were used (contains _route_hint).
            was_successful: Whether the execution succeeded.
            confidence: Confidence score of the execution.
        """
        route_hint: Optional[RouteHint] = plan_params.get("_route_hint")
        if not route_hint or not route_hint.policy_id:
            return

        # Determine effectiveness
        # A policy is effective if it helped achieve success
        was_effective = was_successful and confidence >= 0.7

        self._reflection_engine.record_policy_effectiveness(
            route_hint.policy_id,
            was_effective,
        )

        if was_effective:
            self._effective_count += 1

        logger.info(
            "Policy effectiveness recorded",
            extra={
                "policy_id": str(route_hint.policy_id),
                "was_effective": was_effective,
                "effective_count": self._effective_count,
            },
        )

    def get_metrics(self) -> dict[str, Any]:
        """Get policy routing metrics."""
        total_hits = self._policy_hit_count + self._policy_miss_count
        hit_rate = (
            self._policy_hit_count / total_hits if total_hits > 0 else 0.0
        )
        effective_rate = (
            self._effective_count / self._policy_hit_count
            if self._policy_hit_count > 0
            else 0.0
        )

        return {
            "policy_hit_count": self._policy_hit_count,
            "policy_miss_count": self._policy_miss_count,
            "policy_hit_rate": hit_rate,
            "policy_effective_rate": effective_rate,
            "enabled": self._enabled,
        }
