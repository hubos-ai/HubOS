# -*- coding: utf-8 -*-
"""TaskPlanExecutor — execution control for task plans.

Drives step-by-step state transitions. Steps with ``agent_id`` are dispatched
to the real HostAgentRunner; steps without fall back to simulated execution.
Steps with ``tool_name`` but no ``agent_id`` pause as ``waiting_user``.

The executor reads the latest plan state each iteration so that dynamically
inserted steps are picked up, and pause/resume is supported.

Each plan execution also creates a TaskMonitor record and emits events
(stage_started, stage_completed, error, etc.) so the monitoring page can
track plan progress.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Dict, Optional

from .task_plan import (
    PlanStatus,
    PlanStepStatus,
    TaskPlan,
    get_plan_store,
)

logger = logging.getLogger(__name__)

_AGENT_STEP_TIMEOUT = 120  # seconds
_PAUSE_POLL_INTERVAL = 0.1  # seconds


def _safe_monitor(func):
    """Decorator that swallows all exceptions from monitor calls."""

    async def wrapper(*args, **kwargs):
        try:
            await func(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logger.debug(
                "task_plan_executor: monitor call failed",
                exc_info=True,
            )

    return wrapper


class TaskPlanExecutor:
    """In-process executor that drives a plan's steps through state machines.

    Each loop iteration re-reads the plan from the store so that newly
    inserted steps are discovered and pause/resume state changes are
    respected.
    """

    def __init__(self) -> None:
        self._running: Dict[str, asyncio.Task] = {}  # plan_id → asyncio.Task
        self._cancel_events: Dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()
        self._monitor_task_ids: Dict[
            str,
            str,
        ] = {}  # plan_id → monitor task_id

    # -- public API ----------------------------------------------------------

    async def start_plan(self, plan_id: str) -> bool:
        """Start executing a draft plan. Returns True if started."""
        store = get_plan_store()
        plan = await store.get_plan(plan_id)
        if plan is None:
            raise KeyError(f"Plan not found: {plan_id}")

        if plan.status not in (PlanStatus.DRAFT, PlanStatus.WAITING_USER):
            return False

        # Risk confirmation gate
        if self._needs_confirmation(plan):
            await store.update_plan(
                plan_id,
                status=PlanStatus.WAITING_USER,
                metadata={"waiting_reason": "confirmation_required"},
            )
            return False

        async with self._lock:
            if plan_id in self._running:
                return False

            await store.update_plan(plan_id, status=PlanStatus.RUNNING)

            # Create monitor task
            await self._monitor_create(plan)

            cancel_event = asyncio.Event()
            self._cancel_events[plan_id] = cancel_event

            task = asyncio.create_task(
                self._run_plan(plan_id, cancel_event),
                name=f"hubos.plan-executor-{plan_id}",
            )
            self._running[plan_id] = task

        return True

    async def cancel_plan(self, plan_id: str) -> bool:
        """Cancel a running plan execution. Returns True if cancelled."""
        async with self._lock:
            cancel_event = self._cancel_events.get(plan_id)
            task = self._running.get(plan_id)

        # Save monitor id before cancelling the asyncio.Task, because
        # _run_plan's finally block calls _cleanup which clears the mapping.
        mid = self._monitor_task_ids.get(plan_id)

        if cancel_event is not None:
            cancel_event.set()

        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        store = get_plan_store()
        try:
            await store.cancel_plan(plan_id)
        except KeyError:
            return False

        await self._monitor_cancel_with_id(mid)
        self._cleanup(plan_id)
        return True

    async def pause_plan(self, plan_id: str) -> bool:
        """Pause a running plan."""
        store = get_plan_store()
        try:
            await store.pause_plan(plan_id)
        except (KeyError, ValueError):
            return False
        await self._monitor_pause(plan_id)
        return True

    async def resume_plan(self, plan_id: str) -> bool:
        """Resume a paused plan. Confirms high-risk plans on resume."""
        store = get_plan_store()
        plan = await store.get_plan(plan_id)
        if plan is None:
            return False

        # On resume, treat as confirmation for risk-gated plans
        if self._needs_confirmation(plan):
            await store.update_plan(
                plan_id,
                metadata={"confirmed": True},
            )

        # Also confirm current waiting step if any
        if plan.current_step_id:
            for step in plan.steps:
                if step.step_id == plan.current_step_id:
                    if (
                        step.metadata
                        and step.metadata.get("requires_confirmation")
                        and not step.metadata.get("confirmed")
                    ):
                        await store.update_step(
                            plan_id,
                            step.step_id,
                            metadata={"confirmed": True},
                        )
                    break

        try:
            await store.resume_plan(plan_id)
        except (KeyError, ValueError):
            return False

        await self._monitor_resume(plan_id)

        async with self._lock:
            if plan_id not in self._running or self._running[plan_id].done():
                cancel_event = asyncio.Event()
                self._cancel_events[plan_id] = cancel_event
                task = asyncio.create_task(
                    self._run_plan(plan_id, cancel_event),
                    name=f"hubos.plan-executor-{plan_id}",
                )
                self._running[plan_id] = task

        return True

    def is_running(self, plan_id: str) -> bool:
        """Check if a plan is currently being executed."""
        task = self._running.get(plan_id)
        return task is not None and not task.done()

    # -- internal: plan execution --------------------------------------------

    async def _run_plan(
        self,
        plan_id: str,
        cancel_event: asyncio.Event,
    ) -> None:
        """Execute steps of a plan. Re-reads plan each iteration for dynamic support."""
        store = get_plan_store()

        try:
            while True:
                if cancel_event.is_set():
                    await store.cancel_plan(plan_id)
                    return

                plan = await store.get_plan(plan_id)
                if plan is None:
                    return

                # Respect plan state
                if plan.status == PlanStatus.CANCELLED:
                    return
                if plan.status == PlanStatus.FAILED:
                    return
                if plan.status == PlanStatus.DONE:
                    return

                # Paused — wait for resume
                if plan.status == PlanStatus.WAITING_USER:
                    try:
                        await asyncio.sleep(_PAUSE_POLL_INTERVAL)
                    except asyncio.CancelledError:
                        raise
                    continue

                # Find next pending step (lowest order).
                # Also pick up waiting_user steps that have been confirmed
                # (risk-gated steps that were paused for confirmation).
                next_step = None
                for step in sorted(plan.steps, key=lambda s: s.order):
                    if step.status == PlanStepStatus.PENDING:
                        next_step = step
                        break
                    if (
                        step.status == PlanStepStatus.WAITING_USER
                        and step.metadata
                        and step.metadata.get("confirmed")
                    ):
                        next_step = step
                        break

                if next_step is None:
                    # No more pending steps — all done
                    plan = await store.get_plan(plan_id)
                    if plan is not None and plan.status in (
                        PlanStatus.FAILED,
                        PlanStatus.WAITING_USER,
                    ):
                        return
                    await store.update_plan(
                        plan_id,
                        status=PlanStatus.DONE,
                        current_step_id=None,
                    )
                    await self._monitor_done(plan_id, plan)
                    return

                # Execute the step
                await store.update_plan(
                    plan_id,
                    current_step_id=next_step.step_id,
                )
                await store.update_step(
                    plan_id,
                    next_step.step_id,
                    status=PlanStepStatus.RUNNING,
                )

                # Re-read plan for latest state after marking running
                plan = await store.get_plan(plan_id)
                if plan is None:
                    return

                # Monitor: stage started
                await self._monitor_step_started(plan_id, next_step)

                # Step-level risk confirmation gate
                if self._step_needs_confirmation(next_step):
                    # Find the step in the latest plan snapshot
                    for s in plan.steps:
                        if s.step_id == next_step.step_id:
                            if s.metadata and s.metadata.get("confirmed"):
                                break  # already confirmed
                            # Pause for confirmation
                            await store.update_step(
                                plan_id,
                                next_step.step_id,
                                status=PlanStepStatus.WAITING_USER,
                                metadata={
                                    "reason": "step requires confirmation",
                                },
                            )
                            await store.update_plan(
                                plan_id,
                                status=PlanStatus.WAITING_USER,
                                current_step_id=next_step.step_id,
                            )
                            await self._monitor_step_waiting(
                                plan_id,
                                next_step,
                            )
                            break
                    else:
                        continue
                    plan = await store.get_plan(plan_id)
                    if plan and plan.status == PlanStatus.WAITING_USER:
                        continue
                    if plan is None:
                        return

                stop = await self._execute_step(
                    store,
                    plan,
                    next_step,
                    cancel_event,
                )
                if stop:
                    # Re-read to check final state
                    plan = await store.get_plan(plan_id)
                    if plan is not None and plan.status in (
                        PlanStatus.FAILED,
                        PlanStatus.CANCELLED,
                    ):
                        await self._monitor_step_failed(
                            plan_id,
                            next_step,
                            plan,
                        )
                        return
                    # waiting_user from tool_name step
                    await self._monitor_step_waiting(plan_id, next_step)
                    continue

                # Step completed successfully
                await self._monitor_step_completed(plan_id, next_step)

        except asyncio.CancelledError:
            try:
                await store.cancel_plan(plan_id)
            except Exception:  # noqa: BLE001
                pass

        except Exception:  # noqa: BLE001
            logger.warning(
                "task_plan_executor: plan %s failed",
                plan_id,
                exc_info=True,
            )
            try:
                await store.update_plan(
                    plan_id,
                    status=PlanStatus.FAILED,
                    metadata={"executor_error": "internal error"},
                )
            except Exception:  # noqa: BLE001
                pass
            await self._monitor_failed(plan_id, "internal error")

        finally:
            self._cleanup(plan_id)
            # RunControl: update status based on final plan state
            self._update_run_control_status(plan_id, store)

    async def _execute_step(
        self,
        store,
        plan: TaskPlan,
        step,
        cancel_event: asyncio.Event,
    ) -> bool:
        """Execute a single step. Returns True if execution should stop."""

        if step.agent_id:
            return await self._execute_agent_step(store, plan, step)

        if step.tool_name:
            # Tool execution not implemented yet — pause plan
            await store.update_step(
                plan.plan_id,
                step.step_id,
                status=PlanStepStatus.WAITING_USER,
                metadata={"reason": "tool execution not implemented"},
            )
            await store.update_plan(
                plan.plan_id,
                status=PlanStatus.WAITING_USER,
                current_step_id=step.step_id,
            )
            return True

        # No agent, no tool — simulated execution
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            raise

        await store.update_step(
            plan.plan_id,
            step.step_id,
            status=PlanStepStatus.DONE,
        )
        return False

    async def _execute_agent_step(
        self,
        store,
        plan: TaskPlan,
        step,
    ) -> bool:
        """Dispatch a step to the real HostAgentRunner. Returns True on failure."""

        from hubos.core.workers.providers.host_agent import HostAgentWorker
        from hubos.core.workers.registry import get_host_agent_runner

        runner = get_host_agent_runner()
        if runner is None:
            await store.update_step(
                plan.plan_id,
                step.step_id,
                status=PlanStepStatus.FAILED,
                error="HostAgentRunner is not registered",
            )
            await store.update_plan(
                plan.plan_id,
                status=PlanStatus.FAILED,
                metadata={"failed_step_id": step.step_id},
            )
            return True

        worker = HostAgentWorker(agent_id=step.agent_id, runner=runner)
        try:
            res = await worker.execute(
                unit_id=uuid.uuid4(),
                input_data={
                    "prompt": step.description or step.title,
                    "context": {
                        "plan_id": plan.plan_id,
                        "step_id": step.step_id,
                        "session_id": plan.session_id,
                        "source": "task_plan_executor",
                    },
                },
                timeout_seconds=_AGENT_STEP_TIMEOUT,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "task_plan_executor: agent step %s failed",
                step.step_id,
                exc_info=True,
            )
            await store.update_step(
                plan.plan_id,
                step.step_id,
                status=PlanStepStatus.FAILED,
                error=str(exc)[:500],
            )
            await store.update_plan(
                plan.plan_id,
                status=PlanStatus.FAILED,
                metadata={"failed_step_id": step.step_id},
            )
            return True

        if not res.success:
            await store.update_step(
                plan.plan_id,
                step.step_id,
                status=PlanStepStatus.FAILED,
                error=res.error or "agent execution failed",
            )
            await store.update_plan(
                plan.plan_id,
                status=PlanStatus.FAILED,
                metadata={"failed_step_id": step.step_id},
            )
            return True

        await store.update_step(
            plan.plan_id,
            step.step_id,
            status=PlanStepStatus.DONE,
            metadata={
                "result_summary": res.data.get("content", "")[:1000],
                "execution_time_ms": res.execution_time_ms,
                "executed_by": step.agent_id,
            },
        )
        return False

    # -- internal: confirmation helpers --------------------------------------

    @staticmethod
    def _needs_confirmation(plan) -> bool:
        """Check if a plan requires risk confirmation and hasn't been confirmed."""
        if not plan.metadata:
            return False
        if not plan.metadata.get("requires_confirmation"):
            return False
        if plan.metadata.get("confirmed"):
            return False
        return True

    @staticmethod
    def _step_needs_confirmation(step) -> bool:
        """Check if a step requires risk confirmation and hasn't been confirmed."""
        if not step.metadata:
            return False
        if not step.metadata.get("requires_confirmation"):
            return False
        if step.metadata.get("confirmed"):
            return False
        return True

    # -- internal: monitor integration ---------------------------------------

    @_safe_monitor
    async def _monitor_create(self, plan) -> None:
        from .task_monitor import TaskStatus
        from .task_monitor_helpers import (
            safe_create_task,
            safe_update_task,
            register_cancel_handler,
        )

        mid = await safe_create_task(
            session_id=plan.session_id,
            source="task_plan",
            title=f"plan: {plan.title}",
            tool_name="task_plan_executor",
            metadata={"plan_id": plan.plan_id},
        )
        if mid:
            self._monitor_task_ids[plan.plan_id] = mid
            await safe_update_task(mid, status=TaskStatus.RUNNING)
            # Register cancel handler so TaskMonitor cancel propagates to plan
            plan_id = plan.plan_id
            register_cancel_handler(
                mid,
                lambda pid=plan_id: asyncio.ensure_future(
                    self.cancel_plan(pid),
                ),
            )
            # RunControl: register for unified cancel
            try:
                from .run_control import (
                    get_run_control_store,
                    RunEntry,
                    RunType,
                )

                await get_run_control_store().register(
                    RunEntry(
                        run_id="",
                        run_type=RunType.PLAN,
                        session_id=plan.session_id,
                        monitor_task_id=mid,
                        plan_id=plan.plan_id,
                    ),
                )
            except Exception:  # noqa: BLE001
                pass

    @_safe_monitor
    async def _monitor_step_started(self, plan_id: str, step) -> None:
        from .task_monitor import TaskEventType
        from .task_monitor_helpers import safe_add_event, safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        await safe_add_event(
            mid,
            TaskEventType.STAGE_STARTED,
            f"Step {step.order + 1}: {step.title}",
            stage=step.step_id,
            agent_id=step.agent_id,
            metadata={"plan_id": plan_id},
        )
        await safe_update_task(mid, current_stage=step.title)

    @_safe_monitor
    async def _monitor_step_completed(self, plan_id: str, step) -> None:
        from .task_monitor import TaskEventType
        from .task_monitor_helpers import safe_add_event

        mid = self._monitor_task_ids.get(plan_id)
        await safe_add_event(
            mid,
            TaskEventType.STAGE_COMPLETED,
            f"Step {step.order + 1} done: {step.title}",
            stage=step.step_id,
            metadata={"plan_id": plan_id},
        )

    @_safe_monitor
    async def _monitor_step_failed(self, plan_id: str, step, plan) -> None:
        from .task_monitor import TaskEventType, TaskStatus
        from .task_monitor_helpers import safe_add_event, safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        err = ""
        for s in plan.steps:
            if s.step_id == step.step_id:
                err = s.error or ""
                break
        await safe_add_event(
            mid,
            TaskEventType.ERROR,
            f"Step {step.order + 1} failed: {step.title} — {err}",
            stage=step.step_id,
            metadata={"plan_id": plan_id},
        )
        await safe_update_task(mid, status=TaskStatus.FAILED, error=err[:500])

    @_safe_monitor
    async def _monitor_step_waiting(self, plan_id: str, step) -> None:
        from .task_monitor import TaskEventType, TaskStatus
        from .task_monitor_helpers import safe_add_event, safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        await safe_add_event(
            mid,
            TaskEventType.LOG,
            f"Step {step.order + 1} waiting: {step.title}",
            stage=step.step_id,
            metadata={"plan_id": plan_id},
        )
        await safe_update_task(
            mid,
            status=TaskStatus.WAITING,
            current_stage=step.title,
        )

    @_safe_monitor
    async def _monitor_done(self, plan_id: str, plan) -> None:
        from .task_monitor import TaskStatus
        from .task_monitor_helpers import safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        await safe_update_task(
            mid,
            status=TaskStatus.DONE,
            progress=100,
            result_summary="Plan completed",
        )

    @_safe_monitor
    async def _monitor_failed(self, plan_id: str, error: str) -> None:
        from .task_monitor import TaskStatus
        from .task_monitor_helpers import safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        await safe_update_task(
            mid,
            status=TaskStatus.FAILED,
            error=error[:500],
        )

    @_safe_monitor
    async def _monitor_cancel(self, plan_id: str) -> None:
        from .task_monitor import TaskStatus
        from .task_monitor_helpers import safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        await self._monitor_cancel_with_id(mid)

    @_safe_monitor
    async def _monitor_cancel_with_id(self, mid: Optional[str]) -> None:
        from .task_monitor import TaskStatus
        from .task_monitor_helpers import safe_update_task

        await safe_update_task(
            mid,
            status=TaskStatus.CANCELLED,
            result_summary="Plan cancelled",
        )

    @_safe_monitor
    async def _monitor_pause(self, plan_id: str) -> None:
        from .task_monitor import TaskStatus
        from .task_monitor_helpers import safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        await safe_update_task(mid, status=TaskStatus.WAITING)

    @_safe_monitor
    async def _monitor_resume(self, plan_id: str) -> None:
        from .task_monitor import TaskStatus
        from .task_monitor_helpers import safe_update_task

        mid = self._monitor_task_ids.get(plan_id)
        await safe_update_task(mid, status=TaskStatus.RUNNING)

    def _cleanup(self, plan_id: str) -> None:
        """Remove internal tracking for a finished plan."""
        self._running.pop(plan_id, None)
        self._cancel_events.pop(plan_id, None)
        # Cleanup monitor
        mid = self._monitor_task_ids.pop(plan_id, None)
        if mid:
            from .task_monitor_helpers import unregister_cancel_handler

            unregister_cancel_handler(mid)

    def _update_run_control_status(self, plan_id: str, store) -> None:
        """Best-effort update RunControl status from final plan state."""
        try:
            from .run_control import get_run_control_store

            rc_store = get_run_control_store()
            # Find the run_id by plan_id — iterate runs for this executor.
            # We store the run_id in monitor_task metadata, but simpler:
            # just do an async update via ensure_future.
            import asyncio

            async def _do():
                try:
                    plan = await store.get_plan(plan_id)
                    if plan is None:
                        return
                    # Find matching run by plan_id
                    for entry in list(rc_store._runs.values()):
                        if entry.plan_id == plan_id:
                            status_map = {
                                PlanStatus.DONE: "done",
                                PlanStatus.FAILED: "failed",
                                PlanStatus.CANCELLED: "cancelled",
                                PlanStatus.WAITING_USER: "running",
                            }
                            new_status = status_map.get(plan.status, "done")
                            await rc_store.update_status(
                                entry.run_id,
                                new_status,
                            )
                            break
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "plan executor: RunControl status update failed for plan %s",
                        plan_id,
                        exc_info=True,
                    )

            asyncio.ensure_future(_do())
        except Exception:  # noqa: BLE001
            logger.warning(
                "plan executor: failed to schedule RunControl status update for plan %s",
                plan_id,
            )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_executor: Optional[TaskPlanExecutor] = None


def get_plan_executor() -> TaskPlanExecutor:
    """Return (and lazily create) the global TaskPlanExecutor."""
    global _executor
    if _executor is None:
        _executor = TaskPlanExecutor()
    return _executor
