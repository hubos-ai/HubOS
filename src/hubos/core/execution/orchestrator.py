# -*- coding: utf-8 -*-
"""Execution Loop MVP - Orchestrator.

Wires together workflow execution with existing agent registry and workflow presets.

Parallel Core V1.5 Step 1: Supports backend routing (native vs CAMEL).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from hubos.core.execution.task_store import (
    TaskStore,
    Task,
    TaskStatus,
    TaskStage,
    StageStatus,
)
from hubos.core.execution.event_store import (
    EventStore,
    ExecutionEvent,
    EventType,
)
from hubos.core.infra.feature_flags import get_feature_flags

logger = logging.getLogger(__name__)


def _get_task_store() -> "TaskStore":
    """Lazy import to avoid circular import."""
    from hubos.core.execution import get_task_store as _get

    return _get()


def _get_event_store() -> "EventStore":
    """Lazy import to avoid circular import."""
    from hubos.core.execution import get_event_store as _get

    return _get()


def _get_feature_flags():
    """Lazy import to avoid circular import."""
    from hubos.core.infra.feature_flags import get_feature_flags as _get

    return _get()


class ExecutionOrchestrator:
    """
    Orchestrates task execution across workflow stages.

    Connects:
    - Ingress/session normalization (input processing)
    - Planner/coordinator (workflow selection)
    - Worker dispatch (agent routing)
    - Merge (result aggregation)
    - Outbound (final response formation)
    """

    def __init__(
        self,
        task_store: Optional[TaskStore] = None,
        event_store: Optional[EventStore] = None,
    ) -> None:
        """Initialize orchestrator."""
        self._task_store = task_store or _get_task_store()
        self._event_store = event_store or _get_event_store()
        self._agent_registry = None  # Lazy load
        self._camel_backend = None  # Lazy load

    @property
    def camel_backend(self):
        """Lazy load CAMEL backend."""
        if self._camel_backend is None:
            from hubos.core.execution.backends import (
                get_camel_backend,
                CAMELCallbacks,
            )

            callbacks = CAMELCallbacks(
                task_id="",  # Will be set per-task
                trace_id="",
                event_store=self._event_store,
            )
            self._camel_backend = get_camel_backend(
                use_mock=False,
                callbacks=callbacks,
            )
        return self._camel_backend

    def _select_backend(self, task: Task) -> tuple[str, Optional[Any]]:
        """
        Select execution backend for a task.

        Returns:
            Tuple of (backend_name, backend_instance)
            backend_name is "native" or "camel"
        """
        flags = _get_feature_flags()

        # Check if parallel workflow is requested
        if task.requested_workflow == "parallel_dynamic_v1":
            if (
                flags.enable_camel_backend
                and flags.enable_parallel_workflow_v1
            ):
                # Try to use CAMEL backend
                try:
                    return ("camel", self.camel_backend)
                except Exception as e:
                    logger.warning(f"CAMEL backend initialization failed: {e}")
                    if flags.enable_backend_auto_fallback:
                        self._emit_fallback_event(task, str(e))
                        return ("native", None)
                    raise

        # Default to native backend
        return ("native", None)

    def _retrieve_work_experiences(self, task: Task) -> None:
        """
        Phase 4: Retrieve work experience cards before task execution.

        v4: Uses LLM-based matching to find a WorkflowCard.
        """
        from hubos.core.work_experience.integration_v4 import (
            get_work_experience_interceptor,
        )

        from hubos.core.infra.feature_flags import (
            get_feature_flags,
        )  # noqa: F811

        try:
            interceptor = get_work_experience_interceptor()
            card = interceptor.pre_execute(
                user_message=task.input_text or "",
                session_id=task.session_id or "",
            )

            # Emit event only when the layer is enabled
            if get_feature_flags().enable_work_experience_layer:
                self._event_store.add_event(
                    task_id=task.task_id,
                    trace_id=task.trace_id,
                    event_type=EventType.WORK_EXPERIENCE_RETRIEVED,
                    data={
                        "card_count": 1 if card else 0,
                        "card_ids": [card.card_id] if card else [],
                        "top_titles": [card.task_type] if card else [],
                    },
                )
        except Exception as exc:
            # Never let work experience retrieval block task execution
            logger.warning(
                "Work experience retrieval failed, continuing without cards",
                extra={"task_id": task.task_id, "error": str(exc)},
            )

    def _emit_fallback_event(self, task: Task, reason: str) -> None:
        """Emit backend fallback event."""
        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.BACKEND_FALLBACK,
            data={
                "reason": reason,
                "original_backend": "camel",
                "fallback_backend": "native",
            },
            error_code="BACKEND_FALLBACK",
        )

    def _persist_work_experience_from_task(self, task: Task) -> None:
        """
        Run post-execution reflection and card update (v4).
        Best-effort: never blocks user-facing result.
        """
        try:
            from hubos.core.work_experience.integration_v4 import (
                get_work_experience_interceptor,
            )

            get_work_experience_interceptor().post_execute(task)
        except Exception as exc:
            logger.warning(
                "Work experience post-execution persistence failed",
                extra={"task_id": task.task_id, "error": str(exc)},
            )

    @property
    def agent_registry(self):
        """Lazy load agent registry."""
        if self._agent_registry is None:
            from hubos.core.infra.agent_registry import get_agent_registry

            self._agent_registry = get_agent_registry()
        return self._agent_registry

    def submit_task(
        self,
        input_text: str,
        session_id: Optional[str] = None,
        channel: Optional[str] = None,
        priority: str = "normal",
        requested_workflow: str = "one_person_default",
    ) -> Task:
        """
        Submit a new task for execution.

        Args:
            input_text: Task description/input
            session_id: Optional session identifier
            channel: Optional channel (e.g., 'api', 'slack')
            priority: Task priority (low, normal, high)
            requested_workflow: Workflow preset name (default: one_person_default)

        Returns:
            Created Task object
        """
        # Create task in store
        task = self._task_store.create_task(
            input_text=input_text,
            session_id=session_id,
            channel=channel,
            priority=priority,
            requested_workflow=requested_workflow,
        )

        # Log submission event
        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.TASK_SUBMITTED,
            to_status=TaskStatus.RECEIVED.value,
            data={
                "input_text": input_text[:200],  # Truncate for logging
                "session_id": session_id,
                "channel": channel,
                "priority": priority,
                "workflow": requested_workflow,
            },
        )

        # Record metrics
        from hubos.core.infra.metrics import get_metrics_service

        metrics = get_metrics_service()
        metrics.record_task_submit()
        metrics.update_task_queue_depth(
            self._task_store.list_tasks().__len__(),
        )

        logger.info(f"Task submitted: {task.task_id} trace_id={task.trace_id}")
        return task

    def execute_task(self, task_id: str) -> Task:
        """
        Execute a task through its workflow stages.

        Args:
            task_id: Task to execute

        Returns:
            Updated Task object
        """
        from hubos.core.infra.metrics import get_metrics_service

        metrics = get_metrics_service()

        task = self._task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        if task.current_status not in (
            TaskStatus.RECEIVED,
            TaskStatus.PLANNED,
        ):
            raise ValueError(
                f"Task {task_id} is not in executable state: {task.current_status}",
            )

        # Select backend
        backend_name, backend = self._select_backend(task)

        # Phase 4: Retrieve work experiences before execution (bypass-read, flag-gated)
        self._retrieve_work_experiences(task)

        # Update to running
        self._transition_status(task, TaskStatus.RUNNING)

        # Execute stages based on workflow
        start_time = time.time()
        try:
            if backend_name == "camel" and backend:
                # Use CAMEL backend for parallel execution
                self._execute_with_camel(task, backend)
            else:
                # Use native backend (default)
                self._execute_workflow(task)
            duration_ms = (time.time() - start_time) * 1000
            metrics.record_task_execution_duration(duration_ms)
            metrics.record_task_success()
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            metrics.record_task_execution_duration(duration_ms)
            metrics.record_task_failure()
            logger.exception(f"Task {task_id} execution failed: {e}")
            self._fail_task(task, str(e))
            raise

        # Update queue depth
        metrics.update_task_queue_depth(len(self._task_store.list_tasks()))

        return task

    def _execute_with_camel(self, task: Task, backend: Any) -> None:
        """
        Execute task using CAMEL backend for parallel branches.

        Args:
            task: Task to execute
            backend: CAMEL backend instance
        """
        from hubos.core.workflow import get_preset
        from hubos.core.execution.backends import (
            BranchDefinition,
            MergeDefinition,
        )

        # Get workflow preset
        preset = get_preset(task.requested_workflow)
        if not preset:
            raise ValueError(f"Workflow not found: {task.requested_workflow}")

        # Build branch definitions from stages
        # Skip CEO stage (runs first, not parallel)
        # Parallel branches: INFO, DEV, REVIEW
        parallel_roles = ["info", "dev", "review"]
        branches = []
        for stage_def in preset.stages:
            if stage_def.role in parallel_roles:
                branch = BranchDefinition(
                    branch_id=f"branch-{stage_def.role}",
                    role=stage_def.role,
                    input_template=stage_def.input_template,
                    timeout_seconds=stage_def.timeout_seconds,
                    required=stage_def.required,
                )
                branches.append(branch)

        # Execute with CAMEL backend
        callbacks = backend._callbacks
        if callbacks:
            callbacks.task_id = task.task_id
            callbacks.trace_id = task.trace_id

        dag_result, success = backend.execute(
            task_id=task.task_id,
            trace_id=task.trace_id,
            input_text=task.input_text,
            branches=branches,
        )

        if not success:
            raise Exception(
                f"CAMEL execution failed: {dag_result.final_output}",
            )

        # Update task with results
        # Map branch outputs to stage outputs
        stage_outputs = {}
        for branch_id, branch_run in dag_result.branches.items():
            if branch_run.output:
                role = branch_run.role
                stage_outputs[role] = branch_run.output

        # Generate final response
        final_response = self._generate_response(task, stage_outputs)
        final_response["backend"] = "camel"
        final_response["parallel"] = True

        # Mark task as done
        self._task_store.update_status(
            task_id=task.task_id,
            status=TaskStatus.DONE,
            final_response=final_response,
        )

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.TASK_COMPLETED,
            from_status="running",
            to_status="done",
            data={
                "confidence": final_response.get("confidence", 0),
                "backend": "camel",
            },
        )

        self._persist_work_experience_from_task(task)

        logger.info(f"Task {task.task_id} completed via CAMEL backend")

    def _execute_workflow(self, task: Task) -> None:
        """Execute workflow stages for a task."""
        from hubos.core.infra.metrics import get_metrics_service

        metrics = get_metrics_service()

        workflow_name = task.requested_workflow

        # Get workflow definition (reuse existing workflow preset logic)
        from hubos.core.workflow import get_preset

        preset = get_preset(workflow_name)
        if not preset:
            raise ValueError(f"Workflow not found: {workflow_name}")

        # Check available agents
        available_roles = self._get_available_roles(task)

        # Execute each stage
        stage_outputs = {}
        for stage_def in preset.stages:
            stage = TaskStage(stage_def.stage.value)

            # Log stage dispatch
            self._event_store.add_event(
                task_id=task.task_id,
                trace_id=task.trace_id,
                event_type=EventType.STAGE_DISPATCH,
                stage=stage.value,
                to_status="running",
                data={"role": stage_def.role, "required": stage_def.required},
            )

            # Update stage status
            self._task_store.update_stage_status(
                task_id=task.task_id,
                stage=stage,
                status="running",
            )

            # Check if role is available
            if not available_roles.get(stage_def.role, False):
                # Find fallback
                fallback = self._find_fallback(stage_def.role, preset)
                if fallback:
                    self._apply_fallback(task, stage, fallback)
                else:
                    if stage_def.required:
                        self._fail_stage(
                            task,
                            stage,
                            f"No agent available for role: {stage_def.role}",
                        )
                        raise ValueError(
                            f"Required stage {stage.value} failed: no agent",
                        )
                    else:
                        self._skip_stage(
                            task,
                            stage,
                            "Role not available, optional stage",
                        )
                continue

            # Execute stage
            stage_start = time.time()
            try:
                output = self._execute_stage(
                    task,
                    stage,
                    stage_def,
                    available_roles,
                )
                stage_outputs[stage.value] = output
                stage_duration_ms = (time.time() - stage_start) * 1000

                # Record stage duration metric
                metrics.record_task_stage_duration(
                    stage.value,
                    stage_duration_ms,
                )

                # Update stage as completed
                self._task_store.update_stage_status(
                    task_id=task.task_id,
                    stage=stage,
                    status="completed",
                    output=output,
                )

                self._event_store.add_event(
                    task_id=task.task_id,
                    trace_id=task.trace_id,
                    event_type=EventType.STAGE_COMPLETED,
                    stage=stage.value,
                    from_status="running",
                    to_status="completed",
                )

            except Exception as e:
                stage_duration_ms = (time.time() - stage_start) * 1000
                metrics.record_task_stage_duration(
                    stage.value,
                    stage_duration_ms,
                )
                logger.exception(f"Stage {stage.value} failed: {e}")

                # Check if we should retry
                if self._should_retry_stage(task, stage):
                    retry_count = self._get_stage_retry_count(task, stage)
                    logger.info(
                        f"Retrying stage {stage.value} (attempt {retry_count + 1})",
                    )
                    self._increment_stage_retry(task, stage)
                    # Retry the stage
                    continue
                elif stage_def.required:
                    self._fail_stage(task, stage, str(e))
                    raise
                else:
                    self._skip_stage(
                        task,
                        stage,
                        f"Stage failed (optional): {str(e)}",
                    )

        # Generate final response
        final_response = self._generate_response(task, stage_outputs)

        # Mark task as done
        self._task_store.update_status(
            task_id=task.task_id,
            status=TaskStatus.DONE,
            final_response=final_response,
        )

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.TASK_COMPLETED,
            from_status="running",
            to_status="done",
            data={"confidence": final_response.get("confidence", 0)},
        )

        self._persist_work_experience_from_task(task)

        logger.info(f"Task {task.task_id} completed successfully")

    def _get_available_roles(self, task: Task) -> dict[str, bool]:
        """Check which roles have available agents."""
        available = {}
        for role in ["ceo", "info", "dev", "review"]:
            agents = self.agent_registry.list_agents(
                role=role,
                status="enabled",
            )
            available[role] = len(agents) > 0
        return available

    def _find_fallback(self, missing_role: str, preset) -> Optional[Any]:
        """Find fallback rule for missing role."""
        for rule in preset.fallback_rules:
            if rule.missing_role == missing_role:
                return rule
        return None

    def _apply_fallback(self, task: Task, stage: TaskStage, fallback) -> None:
        """Apply fallback action for missing role."""
        from hubos.core.workflow.preset import FallbackAction

        action = fallback.action

        if action == FallbackAction.SKIP:
            self._skip_stage(task, stage, fallback.reason)
        elif action == FallbackAction.DEGRADE:
            # Use alternative role if available
            alt_roles = getattr(fallback, "alternative_roles", [])
            if alt_roles:
                # For now, just skip - degradation would need more complex logic
                self._skip_stage(task, stage, f"Degraded: {fallback.reason}")
            else:
                self._skip_stage(
                    task,
                    stage,
                    f"No alternative available: {fallback.reason}",
                )
        elif action == FallbackAction.FAIL:
            self._fail_stage(
                task,
                stage,
                f"Failed by fallback rule: {fallback.reason}",
            )
        else:
            self._skip_stage(
                task,
                stage,
                f"Fallback action {action}: {fallback.reason}",
            )

    def _execute_stage(
        self,
        task: Task,
        stage: TaskStage,
        stage_def,
        available_roles: dict[str, bool],
    ) -> dict[str, Any]:
        """Execute a single workflow stage.

        When ENABLE_REAL_MODEL_EXECUTION=true, uses real MiniMax model.
        Otherwise falls back to mock implementation.
        """
        flags = _get_feature_flags()

        if flags.enable_real_model_execution:
            return self._execute_stage_real(task, stage, stage_def)
        else:
            return self._execute_stage_mock(task, stage)

    def _execute_stage_real(
        self,
        task: Task,
        stage: TaskStage,
        stage_def,
    ) -> dict[str, Any]:
        """Execute a single workflow stage using real LLM."""
        from hubos.core.llm.runtime import get_llm_runtime

        llm = get_llm_runtime()
        stage_name = stage.value.lower()

        try:
            result = llm.generate_for_stage(
                stage=stage_name,
                input_text=task.input_text,
                context={
                    "task_id": task.task_id,
                    "work_experience_cards": getattr(
                        task,
                        "work_experience_cards",
                        [],
                    )
                    or [],
                },
            )

            if result.success:
                logger.info(
                    f"[{stage_name.upper()}] LLM generated {len(result.text)} chars",
                )
                # Phase 6: Record effective use for successfully injected cards
                cards = getattr(task, "work_experience_cards", []) or []
                if cards:
                    logger.debug(
                        "WE_EXECUTION_SUCCESS",
                        extra={
                            "task_id": task.task_id,
                            "stage": stage_name,
                            "injected_card_count": len(cards),
                            "injected_card_ids": [
                                c.get("experience_id") for c in cards
                            ],
                            "injected_card_titles": [
                                c.get("title", "")[:60] for c in cards
                            ],
                            "response_chars": len(result.text),
                        },
                    )
                    try:
                        from hubos.core.work_experience.integration_v4 import (
                            get_work_experience_interceptor,
                        )

                        get_work_experience_interceptor().record_effective_uses(
                            cards,
                        )
                    except Exception:
                        pass  # Never let effective-use tracking block execution
                return {
                    "content": result.text,
                    "artifacts": [],
                    "confidence": 0.9 if result.text else 0.5,
                }
            else:
                logger.warning(
                    f"[{stage_name.upper()}] LLM failed: {result.error}, using fallback",
                )
                return self._execute_stage_mock(task, stage)

        except Exception as e:
            logger.error(f"[{stage_name.upper()}] LLM exception: {e}")
            return self._execute_stage_mock(task, stage)

    def _execute_stage_mock(
        self,
        task: Task,
        stage: TaskStage,
    ) -> dict[str, Any]:
        """Execute a single workflow stage (mock implementation).

        DEPRECATED: Only used when ENABLE_REAL_MODEL_EXECUTION=false.
        """
        import time

        time.sleep(0.01)  # Simulate minimal work

        return {
            "content": f"[{stage.value.upper()}] Processed: {task.input_text[:50]}...",
            "artifacts": [],
            "confidence": 0.9,
        }

    def _should_retry_stage(self, task: Task, stage: TaskStage) -> bool:
        """Check if a failed stage should be retried."""
        stage_key = stage.value
        if stage_key not in task.stage_statuses:
            return False
        stage_status = task.stage_statuses[stage_key]
        max_retries = getattr(task, "max_retries", 3)
        return stage_status.retry_count < max_retries

    def _get_stage_retry_count(self, task: Task, stage: TaskStage) -> int:
        """Get current retry count for a stage."""
        stage_key = stage.value
        if stage_key in task.stage_statuses:
            return task.stage_statuses[stage_key].retry_count
        return 0

    def _increment_stage_retry(self, task: Task, stage: TaskStage) -> None:
        """Increment retry count for a stage."""
        stage_key = stage.value
        if stage_key not in task.stage_statuses:
            task.stage_statuses[stage_key] = StageStatus(
                stage=stage,
                status="pending",
                retry_count=0,
            )
        task.stage_statuses[stage_key].retry_count += 1

    def _generate_response(
        self,
        task: Task,
        stage_outputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate final response from stage outputs.

        Priority for response_text:
        1. review stage output (cleaned) - for user-facing
        2. last successful stage output (cleaned)
        3. fallback message

        Internal stages (ceo/info/dev) are preserved for debugging but NOT sent to user.
        """
        from hubos.core.llm.runtime import get_llm_runtime

        llm = get_llm_runtime()

        # Priority: review > dev > info > ceo
        stage_priority = ["review", "dev", "info", "ceo"]

        # Try to get a clean user-facing response
        response_text = None
        source_stage = None

        for stage_name in stage_priority:
            if stage_name in stage_outputs and stage_outputs[stage_name]:
                content = stage_outputs[stage_name].get("content", "")
                if content:
                    # Clean the output using the sanitizer
                    cleaned = llm._clean_review_output(content)
                    if cleaned and len(cleaned) > 5:
                        response_text = cleaned
                        source_stage = stage_name
                        break

        # If still no good response, try raw content
        if not response_text:
            for stage_name in stage_priority:
                if stage_name in stage_outputs and stage_outputs[stage_name]:
                    content = stage_outputs[stage_name].get("content", "")
                    if content and len(content) > 5:
                        response_text = content
                        source_stage = stage_name
                        break

        # Last resort fallback
        if not response_text:
            response_text = "抱歉，我现在无法处理你的请求，请稍后重试。"
            source_stage = None

        # Calculate average confidence from all stages
        total_confidence = 0.0
        for output in stage_outputs.values():
            if output:
                total_confidence += output.get("confidence", 0.5)
        avg_confidence = (
            total_confidence / len(stage_outputs) if stage_outputs else 0.0
        )

        return {
            # response_text is the user-facing response (cleaned, from review if available)
            "response_text": response_text,
            # internal_reasoning preserves all stage outputs for debugging
            "internal_reasoning": {
                stage: output.get("content", "") if output else ""
                for stage, output in stage_outputs.items()
            },
            "response_source_stage": source_stage,
            "confidence": avg_confidence,
            "stages_completed": list(stage_outputs.keys()),
            "output_summary": {
                stage: {"confidence": out.get("confidence", 0)}
                for stage, out in stage_outputs.items()
            },
        }

    def _transition_status(self, task: Task, new_status: TaskStatus) -> None:
        """Transition task to new status."""
        old_status = task.current_status
        self._task_store.update_status(task.task_id, new_status)

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.STATE_TRANSITION,
            from_status=old_status.value,
            to_status=new_status.value,
        )

    def _skip_stage(self, task: Task, stage: TaskStage, reason: str) -> None:
        """Mark a stage as skipped."""
        self._task_store.update_stage_status(
            task_id=task.task_id,
            stage=stage,
            status="skipped",
            error=reason,
        )

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.STAGE_SKIPPED,
            stage=stage.value,
            from_status="running",
            to_status="skipped",
            data={"reason": reason},
        )

    def _fail_stage(self, task: Task, stage: TaskStage, error: str) -> None:
        """Mark a stage as failed."""
        self._task_store.update_stage_status(
            task_id=task.task_id,
            stage=stage,
            status="failed",
            error=error,
        )

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.STAGE_FAILED,
            stage=stage.value,
            from_status="running",
            to_status="failed",
            error_code="STAGE_FAILED",
        )

    def _fail_task(self, task: Task, reason: str) -> None:
        """Mark task as failed."""
        self._task_store.update_status(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            failure_reason=reason,
        )

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.TASK_FAILED,
            from_status=task.current_status.value,
            to_status="failed",
            error_code="TASK_FAILED",
            data={"reason": reason},
        )

        self._persist_work_experience_from_task(task)

    def enter_human_gate(self, task_id: str, reason: str) -> Task:
        """Move task to human gate for manual intervention."""
        from hubos.core.infra.metrics import get_metrics_service

        metrics = get_metrics_service()

        task = self._task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        self._task_store.update_status(task.task_id, TaskStatus.HUMAN_GATE)
        # Mark that this task requires human approval
        self._task_store.set_requires_human(task.task_id, True)

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.HUMAN_GATE_ENTERED,
            from_status=task.current_status.value,
            to_status="human_gate",
            data={"reason": reason},
        )

        # Record human gate metric
        metrics.record_task_human_gate()

        return task

    def resolve_human_gate(
        self,
        task_id: str,
        resolution: str,
        approved: bool,
        final_response: Optional[dict[str, Any]] = None,
    ) -> Task:
        """
        Resolve a human gate and continue or fail task.

        Args:
            task_id: Task to resolve
            resolution: Human-readable resolution description
            approved: Whether the task was approved
            final_response: Response to use if approved

        Returns:
            Updated Task object
        """
        task = self._task_store.get_task(task_id)
        if not task:
            raise ValueError(f"Task not found: {task_id}")

        if task.current_status != TaskStatus.HUMAN_GATE:
            raise ValueError(f"Task {task_id} is not in human_gate state")

        self._event_store.add_event(
            task_id=task.task_id,
            trace_id=task.trace_id,
            event_type=EventType.HUMAN_GATE_RESOLVED,
            from_status="human_gate",
            to_status="running" if approved else "failed",
            data={"resolution": resolution, "approved": approved},
        )

        if approved:
            self._task_store.update_status(
                task_id=task.task_id,
                status=TaskStatus.RUNNING,
            )
            # Clear requires_human now that approval is done
            self._task_store.set_requires_human(task.task_id, False)
            # Continue execution
            return self.execute_task(task_id)
        else:
            self._task_store.update_status(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                failure_reason=f"Human gate rejected: {resolution}",
            )
            # Clear requires_human since gate is resolved
            self._task_store.set_requires_human(task.task_id, False)
            return task

    def get_task_with_events(
        self,
        task_id: str,
    ) -> tuple[Optional[Task], list[ExecutionEvent]]:
        """Get task with its event history."""
        task = self._task_store.get_task(task_id)
        events = self._event_store.get_events(task_id) if task else []
        return task, events
