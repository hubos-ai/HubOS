# -*- coding: utf-8 -*-
"""Workflow Preset Service - Golden Workflow Definition and Execution.

Provides:
- Workflow preset definitions (one_person_default, etc.)
- Fallback rules for missing roles
- Standardized output format
- CLI/API entry point for triggering workflows
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class WorkflowStage(str, Enum):
    """Standard workflow stages."""

    CEO = "ceo"  # Strategic planning
    INFO = "info"  # Information gathering
    DEV = "dev"  # Development
    REVIEW = "review"  # Code review
    SUMMARY = "summary"  # Final summary


class FallbackAction(str, Enum):
    """Fallback action when a role is unavailable."""

    SKIP = "skip"  # Skip this stage
    USE_NEXT_AVAILABLE = "next"  # Use next available role
    FAIL = "fail"  # Fail the workflow
    DEGRADE = "degrade"  # Continue with reduced quality


@dataclass
class FallbackRule:
    """Fallback rule for a missing role."""

    missing_role: str
    action: FallbackAction
    alternative_roles: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class StageDefinition:
    """Definition of a workflow stage."""

    stage: WorkflowStage
    role: str
    input_template: str
    timeout_seconds: int = 300
    required: bool = True
    approval_required: bool = False


@dataclass
class WorkflowPreset:
    """Workflow preset definition."""

    name: str
    description: str
    stages: list[StageDefinition]
    fallback_rules: list[FallbackRule]
    output_template: dict[str, Any]
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


# Standard stages for one_person_default
ONE_PERSON_DEFAULT_STAGES = [
    StageDefinition(
        stage=WorkflowStage.CEO,
        role="ceo",
        input_template="Analyze the following request and create a strategic plan: {input}",
        timeout_seconds=600,
        required=True,
    ),
    StageDefinition(
        stage=WorkflowStage.INFO,
        role="info",
        input_template="Gather relevant information for: {input}",
        timeout_seconds=120,
        required=False,  # Can be skipped if not needed
    ),
    StageDefinition(
        stage=WorkflowStage.DEV,
        role="dev",
        input_template="Implement the solution based on the plan: {input}",
        timeout_seconds=1800,
        required=True,
    ),
    StageDefinition(
        stage=WorkflowStage.REVIEW,
        role="review",
        input_template="Review the implementation: {input}",
        timeout_seconds=300,
        required=True,
    ),
]

# Fallback rules for missing roles
ONE_PERSON_DEFAULT_FALLBACKS = [
    FallbackRule(
        missing_role="info",
        action=FallbackAction.SKIP,
        reason="Information gathering is optional for simple tasks",
    ),
    FallbackRule(
        missing_role="review",
        action=FallbackAction.DEGRADE,
        alternative_roles=["dev"],
        reason="Code review can be performed by developer if dedicated reviewer unavailable",
    ),
]

# Standard output template
STANDARD_OUTPUT_TEMPLATE = {
    "summary": "{ceo_summary}\n\n{dev_summary}",
    "execution_log": [],
    "artifacts": [],
    "confidence": 0.8,
    "review_notes": [],
}


# Registry of available presets
WORKFLOW_PRESETS: dict[str, WorkflowPreset] = {}


def register_preset(preset: WorkflowPreset) -> None:
    """Register a workflow preset."""
    WORKFLOW_PRESETS[preset.name] = preset
    logger.info(f"Registered workflow preset: {preset.name}")


def get_preset(name: str) -> Optional[WorkflowPreset]:
    """Get a workflow preset by name."""
    return WORKFLOW_PRESETS.get(name)


def list_presets() -> list[str]:
    """List all available preset names."""
    return list(WORKFLOW_PRESETS.keys())


# Register default preset
register_preset(
    WorkflowPreset(
        name="one_person_default",
        description="CEO -> INFO -> DEV -> REVIEW workflow for single-person development",
        stages=ONE_PERSON_DEFAULT_STAGES,
        fallback_rules=ONE_PERSON_DEFAULT_FALLBACKS,
        output_template=STANDARD_OUTPUT_TEMPLATE,
    ),
)


# Parallel Core V1.5 Step 1: Parallel dynamic workflow
# Stages represent parallel branches that can run concurrently
PARALLEL_DYNAMIC_V1_STAGES = [
    # CEO is the planning/planning phase (runs first, not parallel)
    StageDefinition(
        stage=WorkflowStage.CEO,
        role="ceo",
        input_template="Analyze and create a strategic plan for: {input}",
        timeout_seconds=600,
        required=True,
    ),
    # Parallel branches: INFO, DEV, REVIEW can run concurrently after CEO
    StageDefinition(
        stage=WorkflowStage.INFO,
        role="info",
        input_template="Gather relevant information for: {input}",
        timeout_seconds=120,
        required=False,
    ),
    StageDefinition(
        stage=WorkflowStage.DEV,
        role="dev",
        input_template="Implement the solution based on the plan: {input}",
        timeout_seconds=1800,
        required=True,
    ),
    StageDefinition(
        stage=WorkflowStage.REVIEW,
        role="review",
        input_template="Review the implementation: {input}",
        timeout_seconds=300,
        required=True,
    ),
    # Summary is the merge phase
    StageDefinition(
        stage=WorkflowStage.SUMMARY,
        role="ceo",
        input_template="Summarize all branch results: {input}",
        timeout_seconds=120,
        required=True,
    ),
]

PARALLEL_DYNAMIC_V1_FALLBACKS = [
    FallbackRule(
        missing_role="info",
        action=FallbackAction.SKIP,
        reason="Information gathering is optional for parallel tasks",
    ),
    FallbackRule(
        missing_role="review",
        action=FallbackAction.DEGRADE,
        alternative_roles=["dev"],
        reason="Code review can be performed by developer if dedicated reviewer unavailable",
    ),
]

# Register parallel workflow
register_preset(
    WorkflowPreset(
        name="parallel_dynamic_v1",
        description="Parallel CEO planning -> (INFO | DEV | REVIEW) concurrent branches -> CEO summary merge",
        stages=PARALLEL_DYNAMIC_V1_STAGES,
        fallback_rules=PARALLEL_DYNAMIC_V1_FALLBACKS,
        output_template=STANDARD_OUTPUT_TEMPLATE,
    ),
)


@dataclass
class WorkflowExecution:
    """Record of a workflow execution."""

    execution_id: str
    preset_name: str
    input_text: str
    status: str  # running, completed, failed, degraded
    started_at: datetime
    completed_at: Optional[datetime] = None
    stages_completed: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    output: Optional[dict[str, Any]] = None
    confidence: float = 0.0
    execution_log: list[dict[str, Any]] = field(default_factory=list)


class WorkflowPresetService:
    """
    Service for managing workflow presets and executing workflows.

    Usage:
        service = WorkflowPresetService(agent_registry)
        execution = service.execute_preset("one_person_default", "Build a REST API")
    """

    def __init__(self, agent_registry: Any = None) -> None:
        """Initialize the workflow preset service."""
        self._agent_registry = agent_registry
        self._executions: dict[str, WorkflowExecution] = {}

    @property
    def agent_registry(self) -> Any:
        """Get agent registry (lazy load)."""
        if self._agent_registry is None:
            from hubos.core.infra.agent_registry import get_agent_registry

            self._agent_registry = get_agent_registry()
        return self._agent_registry

    def execute_preset(
        self,
        preset_name: str,
        input_text: str,
        tenant_id: str = "default",
    ) -> WorkflowExecution:
        """
        Execute a workflow preset.

        Args:
            preset_name: Name of the preset to execute
            input_text: Input text for the workflow
            tenant_id: Tenant for agent lookup

        Returns:
            WorkflowExecution record

        Raises:
            ValueError: If preset not found
        """
        preset = get_preset(preset_name)
        if not preset:
            raise ValueError(f"Preset not found: {preset_name}")

        execution_id = str(uuid4())[:8]
        execution = WorkflowExecution(
            execution_id=execution_id,
            preset_name=preset_name,
            input_text=input_text,
            status="running",
            started_at=datetime.now(timezone.utc),
            execution_log=[
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "workflow_started",
                    "preset": preset_name,
                },
            ],
        )

        self._executions[execution_id] = execution
        logger.info(
            f"Started workflow execution {execution_id} with preset {preset_name}",
        )

        try:
            # Check available agents for each role
            available_roles = self._get_available_roles(tenant_id)
            logger.info(f"Available roles: {available_roles}")

            # Execute stages with fallback handling
            stage_outputs = {}
            for stage in preset.stages:
                stage_result = self._execute_stage(
                    stage,
                    input_text,
                    available_roles,
                    execution,
                )

                if stage_result["status"] == "completed":
                    stage_outputs[stage.stage.value] = stage_result["output"]
                    execution.stages_completed.append(stage.stage.value)
                    execution.execution_log.append(
                        {
                            "timestamp": datetime.now(
                                timezone.utc,
                            ).isoformat(),
                            "action": "stage_completed",
                            "stage": stage.stage.value,
                            "role": stage.role,
                        },
                    )
                elif stage_result["status"] == "skipped":
                    execution.stages_skipped.append(stage.stage.value)
                    execution.execution_log.append(
                        {
                            "timestamp": datetime.now(
                                timezone.utc,
                            ).isoformat(),
                            "action": "stage_skipped",
                            "stage": stage.stage.value,
                            "reason": stage_result.get("reason", "fallback"),
                        },
                    )
                elif stage_result["status"] == "failed":
                    if stage.required:
                        execution.errors.append(
                            f"Required stage {stage.stage.value} failed: {stage_result.get('error')}",
                        )
                        execution.status = "failed"
                        execution.completed_at = datetime.now(timezone.utc)
                        return execution
                    else:
                        execution.stages_skipped.append(stage.stage.value)

            # Generate output
            output = self._generate_output(preset, stage_outputs, execution)
            execution.output = output
            execution.confidence = output.get("confidence", 0.5)
            execution.status = "completed"
            execution.completed_at = datetime.now(timezone.utc)

            execution.execution_log.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "workflow_completed",
                    "stages_completed": len(execution.stages_completed),
                    "confidence": execution.confidence,
                },
            )

            logger.info(
                f"Workflow execution {execution_id} completed with confidence {execution.confidence}",
            )

        except Exception as e:
            execution.status = "failed"
            execution.errors.append(str(e))
            execution.completed_at = datetime.now(timezone.utc)
            execution.execution_log.append(
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "workflow_failed",
                    "error": str(e),
                },
            )
            logger.exception(f"Workflow execution {execution_id} failed")

        return execution

    def _get_available_roles(self, tenant_id: str) -> dict[str, bool]:
        """Check which roles have available agents."""
        available = {}
        for role in ["ceo", "info", "dev", "review"]:
            agents = self.agent_registry.list_agents(
                role=role,
                status="enabled",
            )
            available[role] = len(agents) > 0
        return available

    def _execute_stage(
        self,
        stage: StageDefinition,
        input_text: str,
        available_roles: dict[str, bool],
        execution: WorkflowExecution,
    ) -> dict[str, Any]:
        """Execute a single workflow stage."""
        # Check if role is available
        if not available_roles.get(stage.role, False):
            # Find fallback
            fallback = self._find_fallback(stage.role, execution.preset_name)
            if fallback:
                return self._apply_fallback(stage, fallback, input_text)
            else:
                return {
                    "status": "failed",
                    "error": f"No fallback available for {stage.role}",
                }

        # For now, just simulate execution
        return {
            "status": "completed",
            "output": {
                "content": f"[{stage.stage.value.upper()}] Processed: {input_text[:100]}...",
                "artifacts": [],
            },
        }

    def _find_fallback(
        self,
        missing_role: str,
        preset_name: str,
    ) -> Optional[FallbackRule]:
        """Find fallback rule for a missing role."""
        preset = get_preset(preset_name)
        if not preset:
            return None

        for rule in preset.fallback_rules:
            if rule.missing_role == missing_role:
                return rule
        return None

    def _apply_fallback(
        self,
        stage: StageDefinition,
        fallback: FallbackRule,
        input_text: str,
    ) -> dict[str, Any]:
        """Apply fallback rule for a missing role."""
        if fallback.action == FallbackAction.SKIP:
            return {"status": "skipped", "reason": fallback.reason}
        elif fallback.action == FallbackAction.FAIL:
            return {
                "status": "failed",
                "error": f"Required role {stage.role} unavailable: {fallback.reason}",
            }
        elif fallback.action == FallbackAction.DEGRADE:
            if fallback.alternative_roles:
                return {
                    "status": "completed",
                    "output": {
                        "content": f"[{stage.stage.value.upper()}] (degraded) Processed: {input_text[:100]}...",
                        "artifacts": [],
                    },
                    "degraded": True,
                    "original_role": stage.role,
                    "used_role": fallback.alternative_roles[0],
                }
        elif fallback.action == FallbackAction.USE_NEXT_AVAILABLE:
            return {
                "status": "skipped",
                "reason": "Use next available role (not yet implemented)",
            }

        return {"status": "skipped", "reason": "No fallback available"}

    def _generate_output(
        self,
        preset: WorkflowPreset,
        stage_outputs: dict[str, Any],
        execution: WorkflowExecution,
    ) -> dict[str, Any]:
        """Generate standardized output from stage outputs."""
        # Build summary from stage outputs
        summaries = []
        for stage_name, output in stage_outputs.items():
            if isinstance(output, dict) and "content" in output:
                summaries.append(
                    f"[{stage_name.upper()}]\n{output['content']}",
                )

        # Calculate confidence based on stages completed/skipped
        total_stages = len(preset.stages)
        completed = len(execution.stages_completed)
        skipped = len(execution.stages_skipped)

        if total_stages > 0:
            base_confidence = completed / total_stages
            # Reduce confidence for skipped stages
            degradation = skipped * 0.05
            confidence = max(0.1, base_confidence - degradation)
        else:
            confidence = 0.5

        return {
            "summary": "\n\n".join(summaries)
            if summaries
            else "No output generated",
            "execution_log": execution.execution_log,
            "artifacts": [],  # Would collect from stage outputs
            "confidence": confidence,
            "review_notes": execution.errors if execution.errors else [],
            "stages_completed": execution.stages_completed,
            "stages_skipped": execution.stages_skipped,
            "degraded": len(execution.stages_skipped) > 0,
        }

    def get_execution(self, execution_id: str) -> Optional[WorkflowExecution]:
        """Get an execution by ID."""
        return self._executions.get(execution_id)

    def list_executions(
        self,
        preset_name: Optional[str] = None,
    ) -> list[WorkflowExecution]:
        """List all executions, optionally filtered by preset."""
        if preset_name:
            return [
                e
                for e in self._executions.values()
                if e.preset_name == preset_name
            ]
        return list(self._executions.values())


# Global service instance
_service: Optional[WorkflowPresetService] = None


def get_workflow_service() -> WorkflowPresetService:
    """Get the global workflow preset service."""
    global _service
    if _service is None:
        _service = WorkflowPresetService()
    return _service
