# -*- coding: utf-8 -*-
"""Workflow services for usability sprint."""

from hubos.core.workflow.preset import (
    WorkflowPresetService,
    WorkflowExecution,
    WorkflowPreset,
    StageDefinition,
    FallbackRule,
    FallbackAction,
    WorkflowStage,
    get_workflow_service,
    get_preset,
    list_presets,
    register_preset,
    ONE_PERSON_DEFAULT_STAGES,
    STANDARD_OUTPUT_TEMPLATE,
)
from hubos.core.workflow.task_board import (
    TaskBoardService,
    TaskCard,
    RecoveryAction,
    BoardColumn,
    get_task_board,
)
from hubos.core.workflow.workflow_state import (
    WorkflowStateStore,
    WorkflowState,
    get_workflow_state_store,
)

__all__ = [
    # Preset
    "WorkflowPresetService",
    "WorkflowExecution",
    "WorkflowPreset",
    "StageDefinition",
    "FallbackRule",
    "FallbackAction",
    "WorkflowStage",
    "get_workflow_service",
    "get_preset",
    "list_presets",
    "register_preset",
    "ONE_PERSON_DEFAULT_STAGES",
    "STANDARD_OUTPUT_TEMPLATE",
    # Task Board
    "TaskBoardService",
    "TaskCard",
    "RecoveryAction",
    "BoardColumn",
    "get_task_board",
    # Workflow State
    "WorkflowStateStore",
    "WorkflowState",
    "get_workflow_state_store",
]
