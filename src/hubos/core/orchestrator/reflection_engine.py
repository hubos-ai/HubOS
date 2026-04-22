"""Self-iteration reflection engine for task反思 and policy generation."""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from hubos.core.schemas.memory import (
    LearnedPolicy,
    RouteHint,
    ReflectionReport,
    EpisodicMemory,
    MemorySource,
)
from hubos.core.schemas.tasks import TaskResult, TaskStatus

logger = logging.getLogger(__name__)


class ReflectionMode(str, Enum):
    """Mode for reflection execution."""

    SYNC = "sync"  # Blocking
    ASYNC = "async"  # Non-blocking (fire-and-forget)
    DISABLED = "disabled"  # No reflection


@dataclass
class TaskContext:
    """Context for a completed task to be reflected upon."""

    task_id: str
    session_id: str
    trace_id: str
    task_input: dict[str, Any]  # Original task input
    execution_trace: list[dict[str, Any]]  # Steps taken during execution
    task_result: TaskResult  # Final result from workers
    human_feedback: Optional[dict[str, Any]] = None  # Human feedback if any
    execution_time_ms: int = 0


@dataclass
class ReflectionEngineConfig:
    """Configuration for reflection engine."""

    mode: ReflectionMode = ReflectionMode.ASYNC
    min_reflection_interval_seconds: float = 1.0  # Rate limit reflection
    policy_success_threshold: float = 0.6  # Min success rate to create policy
    max_policy_suggestions: int = 3  # Max policies to suggest per reflection


class ReflectionEngine:
    """
    Self-iteration reflection engine.

    Per Week 6.5 requirements:
    - Runs after each task completion (non-blocking by default)
    - Generates ReflectionReport with what_worked, what_failed, root_cause, strategy
    - Writes reusable strategies to learned_policy
    - Generates RouteHint for future similar tasks
    - Records reflection metrics
    """

    def __init__(
        self,
        config: Optional[ReflectionEngineConfig] = None,
        policy_store: Optional[dict[str, LearnedPolicy]] = None,
    ) -> None:
        """
        Initialize reflection engine.

        Args:
            config: Optional configuration.
            policy_store: Optional external policy storage (dict).
        """
        self._config = config or ReflectionEngineConfig()
        self._policy_store = policy_store or {}
        self._episodic_store: dict[str, EpisodicMemory] = {}
        self._reflection_reports: dict[str, ReflectionReport] = {}
        self._last_reflection_time: float = 0.0

        # Metrics
        self._reflection_count = 0
        self._reflection_success_count = 0
        self._total_reflection_latency_ms = 0

    def reflect(self, context: TaskContext) -> ReflectionReport:
        """
        Perform reflection on completed task.

        This is the main entry point. When mode is ASYNC, this should be called
        fire-and-forget. When mode is SYNC, it blocks until complete.

        Args:
            context: Task context with execution details.

        Returns:
            ReflectionReport with analysis results.
        """
        start_time = time.time()

        # Rate limiting
        if self._config.mode != ReflectionMode.SYNC:
            elapsed = time.time() - self._last_reflection_time
            if elapsed < self._config.min_reflection_interval_seconds:
                logger.debug(
                    "Reflection rate limited",
                    extra={"elapsed_ms": elapsed * 1000},
                )

        logger.info(
            "Starting reflection",
            extra={
                "task_id": context.task_id,
                "trace_id": context.trace_id,
                "mode": self._config.mode.value,
            },
        )

        # Generate reflection report
        report = self._analyze(context)

        # Store report
        self._reflection_reports[context.task_id] = report

        # Generate episodic memory
        episodic = self._create_episodic_memory(context, report)
        self._episodic_store[context.task_id] = episodic

        # Generate and store policies if applicable
        if report.policy_suggestions:
            self._apply_policy_suggestions(report)

        # Update metrics
        latency_ms = int((time.time() - start_time) * 1000)
        self._reflection_count += 1
        self._total_reflection_latency_ms += latency_ms
        self._last_reflection_time = time.time()

        if report.confidence >= self._config.policy_success_threshold:
            self._reflection_success_count += 1

        logger.info(
            "Reflection completed",
            extra={
                "task_id": context.task_id,
                "report_id": str(report.report_id),
                "confidence": report.confidence,
                "policy_suggestions": len(report.policy_suggestions),
                "latency_ms": latency_ms,
            },
        )

        return report

    def _analyze(self, context: TaskContext) -> ReflectionReport:
        """Analyze task execution and generate reflection report."""
        what_worked: list[str] = []
        what_failed: list[str] = []
        root_cause = ""
        next_time_strategy = ""
        confidence = 0.5

        # Analyze based on task result
        if context.task_result.status == TaskStatus.SUCCESS:
            confidence = 0.8
            what_worked.append("Task completed successfully")

            # Analyze execution trace for good patterns
            for step in context.execution_trace:
                if step.get("success") and step.get("worker"):
                    what_worked.append(f"Worker {step['worker']} succeeded")

        elif context.task_result.status == TaskStatus.FAILURE:
            confidence = 0.6
            what_failed.append("Task failed")
            if context.task_result.error_message:
                what_failed.append(f"Error: {context.task_result.error_message}")

            # Root cause analysis
            root_cause = self._analyze_root_cause(context)

            # Strategy suggestion
            next_time_strategy = self._generate_recovery_strategy(context)

        # Incorporate human feedback if available
        if context.human_feedback:
            report_has_human_feedback = True
            feedback_worked = context.human_feedback.get("worked", [])
            feedback_failed = context.human_feedback.get("failed", [])

            if isinstance(feedback_worked, list):
                what_worked.extend(feedback_worked)
            if isinstance(feedback_failed, list):
                what_failed.extend(feedback_failed)

            confidence = max(confidence, 0.85)  # Human feedback boosts confidence

        # Generate policy suggestions
        policy_suggestions = self._generate_policy_suggestions(
            context, what_worked, what_failed, confidence
        )

        return ReflectionReport(
            report_id=uuid4(),
            task_id=context.task_id,
            session_id=context.session_id,
            trace_id=context.trace_id,
            what_worked=what_worked,
            what_failed=what_failed,
            root_cause=root_cause,
            next_time_strategy=next_time_strategy,
            confidence=confidence,
            has_human_feedback=context.human_feedback is not None,
            policy_suggestions=policy_suggestions,
            created_at=datetime.now(timezone.utc),
        )

    def _analyze_root_cause(self, context: TaskContext) -> str:
        """Analyze root cause of failure."""
        root_cause_parts: list[str] = []

        # Check for timeout
        if context.task_result.error_message and "timeout" in context.task_result.error_message.lower():
            root_cause_parts.append("Execution timeout - consider increasing timeout or optimizing")

        # Check for worker failures
        failed_workers = set()
        for step in context.execution_trace:
            if not step.get("success") and step.get("worker"):
                failed_workers.add(step["worker"])

        if failed_workers:
            root_cause_parts.append(f"Failed workers: {', '.join(failed_workers)}")

        # Check retry behavior
        if context.task_result.retry_count > 0:
            root_cause_parts.append(f"Retried {context.task_result.retry_count} times")

        # Check input complexity
        input_size = len(str(context.task_input))
        if input_size > 10000:
            root_cause_parts.append(f"Large input ({input_size} chars) may cause issues")

        if not root_cause_parts:
            return "Unknown root cause"

        return "; ".join(root_cause_parts)

    def _generate_recovery_strategy(self, context: TaskContext) -> str:
        """Generate recovery strategy for next time."""
        strategies: list[str] = []

        # Timeout strategy
        if context.task_result.error_message and "timeout" in context.task_result.error_message.lower():
            strategies.append("Consider increasing timeout by 50% for similar tasks")

        # Parallel strategy
        if len(context.execution_trace) > 3:
            strategies.append("Task is complex - consider parallel execution")

        # Worker selection strategy
        failed_workers = set()
        successful_workers = set()
        for step in context.execution_trace:
            if step.get("worker"):
                if step.get("success"):
                    successful_workers.add(step["worker"])
                else:
                    failed_workers.add(step["worker"])

        if failed_workers:
            strategies.append(f"Avoid workers: {', '.join(failed_workers)}")
        if successful_workers:
            strategies.append(f"Prefer workers: {', '.join(successful_workers)}")

        if not strategies:
            return "No specific recovery strategy needed"

        return " | ".join(strategies)

    def _generate_policy_suggestions(
        self,
        context: TaskContext,
        what_worked: list[str],
        what_failed: list[str],
        confidence: float,
    ) -> list[dict[str, Any]]:
        """Generate policy suggestions from reflection."""
        suggestions: list[dict[str, Any]] = []

        if confidence < self._config.policy_success_threshold:
            return suggestions

        # Generate trigger from task input (simplified - use first significant key)
        trigger = self._extract_trigger(context.task_input)

        # Generate worker priority policy if we have data
        if what_worked:
            worker_hints = self._extract_worker_hints(context.execution_trace, what_worked)
            if worker_hints:
                suggestions.append({
                    "trigger": trigger,
                    "action": {
                        "worker_priority": worker_hints["preferred"],
                        "skip_providers": worker_hints.get("avoid", []),
                    },
                    "confidence": confidence,
                    "applicability": 0.6,
                })

        # Generate timeout policy
        if context.execution_time_ms > 0:
            suggested_timeout = int(context.execution_time_ms * 1.5 / 1000)  # 1.5x buffer
            suggestions.append({
                "trigger": trigger,
                "action": {
                    "timeout_seconds": suggested_timeout,
                },
                "confidence": confidence * 0.9,
                "applicability": 0.5,
            })

        # Limit suggestions
        return suggestions[:self._config.max_policy_suggestions]

    def _extract_trigger(self, task_input: dict[str, Any]) -> str:
        """Extract a trigger key from task input for policy matching."""
        # Simple heuristic: use the first key with a string value
        for key, value in task_input.items():
            if isinstance(value, str) and len(value) > 5:
                return f"input:{key}:{value[:20]}"
        return f"task:{hash(str(task_input)) % 10000}"

    def _extract_worker_hints(
        self,
        execution_trace: list[dict[str, Any]],
        what_worked: list[str],
    ) -> dict[str, list[str]]:
        """Extract worker preference hints from execution trace."""
        worker_scores: dict[str, int] = {}

        for step in execution_trace:
            worker = step.get("worker", "unknown")
            if step.get("success"):
                worker_scores[worker] = worker_scores.get(worker, 0) + 1
            else:
                worker_scores[worker] = worker_scores.get(worker, 0) - 1

        preferred = [w for w, score in sorted(worker_scores.items(), key=lambda x: -x[1]) if score > 0]
        avoid = [w for w, score in sorted(worker_scores.items(), key=lambda x: -x[1]) if score < 0]

        return {"preferred": preferred[:3], "avoid": avoid[:2]}

    def _create_episodic_memory(
        self,
        context: TaskContext,
        report: ReflectionReport,
    ) -> EpisodicMemory:
        """Create episodic memory from task and reflection."""
        outcome = "success"
        if context.task_result.status == TaskStatus.FAILURE:
            outcome = "failure"
        elif context.task_result.confidence < 0.5:
            outcome = "partial"

        # Compute impact score
        impact_score = 0.5
        if report.what_failed:
            impact_score += 0.2
        if report.has_human_feedback:
            impact_score += 0.2
        impact_score = min(1.0, impact_score)

        return EpisodicMemory(
            episode_id=uuid4(),
            task_id=context.task_id,
            session_id=context.session_id,
            trace_id=context.trace_id,
            event_trace=context.execution_trace,
            decision_rationale=report.next_time_strategy,
            outcome=outcome,
            impact_score=impact_score,
            confidence=report.confidence,
            value_score=impact_score * report.confidence,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )

    def _apply_policy_suggestions(self, report: ReflectionReport) -> None:
        """Apply policy suggestions to policy store."""
        for suggestion in report.policy_suggestions:
            trigger = suggestion.get("trigger", "")
            action = suggestion.get("action", {})
            confidence = suggestion.get("confidence", 0.5)
            applicability = suggestion.get("applicability", 0.5)

            if not trigger:
                continue

            # Check if policy already exists
            existing = self._policy_store.get(trigger)

            if existing:
                # Update if new is better
                if confidence > existing.confidence:
                    existing.action = action
                    existing.confidence = confidence
                    existing.applicability = applicability
                    existing.updated_at = datetime.now(timezone.utc)
                    logger.info(
                        "Policy updated",
                        extra={"trigger": trigger, "confidence": confidence},
                    )
            else:
                # Create new policy
                policy = LearnedPolicy(
                    policy_id=uuid4(),
                    trigger=trigger,
                    action=action,
                    confidence=confidence,
                    applicability=applicability,
                    source=MemorySource.REFLECTION,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                self._policy_store[trigger] = policy
                logger.info(
                    "New policy created",
                    extra={"trigger": trigger, "policy_id": str(policy.policy_id)},
                )

    def generate_route_hint(self, task_input: dict[str, Any]) -> Optional[RouteHint]:
        """
        Generate route hint for a given task input.

        Args:
            task_input: Task input to match against policies.

        Returns:
            RouteHint if matching policy found, None otherwise.
        """
        trigger = self._extract_trigger(task_input)

        # Find best matching policy
        best_policy: Optional[LearnedPolicy] = None
        best_score = 0.0

        for policy_trigger, policy in self._policy_store.items():
            if policy.disabled:
                continue

            # Simple trigger matching (could be more sophisticated)
            if trigger.startswith(policy_trigger) or policy_trigger.startswith(trigger.split(":")[0]):
                score = policy.confidence * policy.applicability
                if score > best_score:
                    best_score = score
                    best_policy = policy

        if not best_policy:
            return None

        # Generate route hint from policy
        action = best_policy.action
        route_hint = RouteHint(
            trigger_task_id=best_policy.policy_id.hex,
            policy_id=best_policy.policy_id,
            worker_priority=action.get("worker_priority", []),
            parallel=action.get("parallel", False),
            timeout_seconds=action.get("timeout_seconds", 300),
            retry_count=action.get("retry_count", 3),
            skip_providers=action.get("skip_providers", []),
            confidence=best_policy.confidence,
            created_at=datetime.now(timezone.utc),
        )

        # Update policy hit count
        best_policy.hit_count += 1
        best_policy.last_used_at = datetime.now(timezone.utc)

        logger.info(
            "Route hint generated",
            extra={
                "trigger": trigger,
                "policy_id": str(best_policy.policy_id),
                "confidence": best_policy.confidence,
                "worker_priority": route_hint.worker_priority,
            },
        )

        return route_hint

    def record_policy_effectiveness(self, policy_id: UUID, was_effective: bool) -> None:
        """Record whether a policy hit was effective."""
        for policy in self._policy_store.values():
            if policy.policy_id == policy_id:
                if was_effective:
                    policy.effective_count += 1
                # Update success rate
                if policy.hit_count > 0:
                    policy.success_rate = policy.effective_count / policy.hit_count
                policy.updated_at = datetime.now(timezone.utc)
                break

    def get_policy(self, policy_id: UUID) -> Optional[LearnedPolicy]:
        """Get policy by ID."""
        for policy in self._policy_store.values():
            if policy.policy_id == policy_id:
                return policy
        return None

    def disable_policy(self, policy_id: UUID) -> bool:
        """Disable a policy."""
        policy = self.get_policy(policy_id)
        if policy:
            policy.disabled = True
            policy.updated_at = datetime.now(timezone.utc)
            logger.info("Policy disabled", extra={"policy_id": str(policy_id)})
            return True
        return False

    def get_policies(
        self,
        trigger_prefix: Optional[str] = None,
        disabled: Optional[bool] = None,
    ) -> list[LearnedPolicy]:
        """Get policies matching criteria."""
        results = list(self._policy_store.values())
        if trigger_prefix:
            results = [p for p in results if p.trigger.startswith(trigger_prefix)]
        if disabled is not None:
            results = [p for p in results if p.disabled == disabled]
        return results

    def get_reflection_report(self, task_id: str) -> Optional[ReflectionReport]:
        """Get reflection report for a task."""
        return self._reflection_reports.get(task_id)

    def get_episodic(self, task_id: str) -> Optional[EpisodicMemory]:
        """Get episodic memory for a task."""
        return self._episodic_store.get(task_id)

    def get_metrics(self) -> dict[str, Any]:
        """Get reflection engine metrics."""
        return {
            "reflection_count": self._reflection_count,
            "reflection_success_count": self._reflection_success_count,
            "reflection_success_rate": (
                self._reflection_success_count / self._reflection_count
                if self._reflection_count > 0 else 0.0
            ),
            "avg_reflection_latency_ms": (
                self._total_reflection_latency_ms / self._reflection_count
                if self._reflection_count > 0 else 0.0
            ),
            "policy_count": len(self._policy_store),
            "enabled_policy_count": sum(1 for p in self._policy_store.values() if not p.disabled),
        }
