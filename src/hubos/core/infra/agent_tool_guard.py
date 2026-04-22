"""Agent Tool Permission Guard.

Provides:
- Unified tool permission validation before execution
- Risk level enforcement
- Approval-required action checking
- Structured error codes and audit logging

Usage:
    guard = AgentToolGuard()

    result = guard.check_permission(
        agent_id="agent-123",
        tool_name="database_write",
        context={"operation": "delete"}
    )

    if not result.allowed:
        handle_error(result)
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class ToolErrorCode(str, Enum):
    """Structured error codes for tool permission failures."""

    # Success
    ALLOWED = "ALLOWED"

    # Agent errors
    AGENT_NOT_FOUND = "AGENT_NOT_FOUND"
    AGENT_DISABLED = "AGENT_DISABLED"

    # Tool errors
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    TOOL_DISABLED = "TOOL_DISABLED"
    TOOL_RISK_LEVEL_HIGH = "TOOL_RISK_LEVEL_HIGH"
    TOOL_APPROVAL_REQUIRED = "TOOL_APPROVAL_REQUIRED"

    # System errors
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass
class ToolPermissionResult:
    """Result of tool permission check."""

    allowed: bool
    error_code: ToolErrorCode
    message: str
    agent_id: Optional[str] = None
    tool_name: Optional[str] = None
    action: Optional[str] = None
    risk_level: Optional[str] = None
    requires_approval: bool = False
    approval_action: Optional[str] = None


class AgentToolGuard:
    """
    Unified tool permission guard.

    Validates tool calls against:
    - Agent's allowed_tools list
    - Agent's risk_level
    - Agent's approval_required_actions

    All denials are logged for audit purposes.
    """

    # Risk level to numeric value mapping
    RISK_LEVELS = {
        "low": 1,
        "medium": 2,
        "high": 3,
        "critical": 4,
    }

    # High-risk tools that require approval regardless of agent config
    HIGH_RISK_TOOLS = {
        "database_write": "high",
        "database_delete": "critical",
        "file_delete": "high",
        "config_global_write": "critical",
        "policy_full_rollback": "high",
        "tenant_delete": "critical",
        "dlq_bulk_discard": "medium",
        "plugin_install": "high",
        "plugin_uninstall": "high",
    }

    def __init__(
        self,
        agent_registry: Optional[Any] = None,
        tool_policies: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        """
        Initialize tool guard.

        Args:
            agent_registry: Optional AgentRegistry instance.
                           If not provided, will use get_agent_registry().
        """
        self._agent_registry = agent_registry
        self._tool_policies: dict[str, dict[str, Any]] = {}
        self.set_tool_policies(tool_policies)

    def set_tool_policies(self, tool_policies: Optional[list[dict[str, Any]]]) -> None:
        """Set dynamic tool policies from settings."""
        self._tool_policies = {}
        if not tool_policies:
            return
        for policy in tool_policies:
            tool_id = policy.get("id")
            if tool_id:
                self._tool_policies[tool_id] = policy

    @property
    def agent_registry(self) -> Any:
        """Get agent registry (lazy load)."""
        if self._agent_registry is None:
            from hubos.core.infra.agent_registry import get_agent_registry
            self._agent_registry = get_agent_registry()
        return self._agent_registry

    def check_permission(
        self,
        agent_id: str,
        tool_name: str,
        context: Optional[dict[str, Any]] = None,
    ) -> ToolPermissionResult:
        """
        Check if agent is allowed to use a tool.

        Args:
            agent_id: ID of the agent
            tool_name: Name of the tool
            context: Optional context including action type

        Returns:
            ToolPermissionResult with allowed status and error details
        """
        ctx = context or {}

        # Get agent
        agent = self.agent_registry.get_agent(agent_id)
        if not agent:
            result = ToolPermissionResult(
                allowed=False,
                error_code=ToolErrorCode.AGENT_NOT_FOUND,
                message=f"Agent {agent_id} not found",
                agent_id=agent_id,
                tool_name=tool_name,
            )
            self._audit_log(result, ctx)
            return result

        # Check agent status
        if agent.status.value != "enabled":
            result = ToolPermissionResult(
                allowed=False,
                error_code=ToolErrorCode.AGENT_DISABLED,
                message=f"Agent {agent_id} is disabled",
                agent_id=agent_id,
                tool_name=tool_name,
            )
            self._audit_log(result, ctx)
            return result

        # Check global tool policy (settings)
        policy = self._tool_policies.get(tool_name)
        if policy and policy.get("enabled") is False:
            result = ToolPermissionResult(
                allowed=False,
                error_code=ToolErrorCode.TOOL_DISABLED,
                message=f"Tool '{tool_name}' is globally disabled",
                agent_id=agent_id,
                tool_name=tool_name,
                risk_level=self._get_tool_risk_level(tool_name),
            )
            self._audit_log(result, ctx)
            return result

        # Check if tool is in allowed_tools
        if tool_name not in agent.allowed_tools:
            result = ToolPermissionResult(
                allowed=False,
                error_code=ToolErrorCode.TOOL_NOT_ALLOWED,
                message=f"Tool '{tool_name}' not in agent's allowed tools",
                agent_id=agent_id,
                tool_name=tool_name,
                risk_level=agent.risk_level.value,
            )
            self._audit_log(result, ctx)
            return result

        # Determine risk level of the tool
        tool_risk = self._get_tool_risk_level(tool_name)

        # Check risk level enforcement
        agent_risk_value = self.RISK_LEVELS.get(agent.risk_level.value, 2)
        tool_risk_value = self.RISK_LEVELS.get(tool_risk, 1)

        if tool_risk_value > agent_risk_value:
            result = ToolPermissionResult(
                allowed=False,
                error_code=ToolErrorCode.TOOL_RISK_LEVEL_HIGH,
                message=f"Tool risk level '{tool_risk}' exceeds agent risk level '{agent.risk_level.value}'",
                agent_id=agent_id,
                tool_name=tool_name,
                risk_level=tool_risk,
                requires_approval=True,
            )
            self._audit_log(result, ctx)
            return result

        # Check approval-required actions
        if policy and policy.get("approval_required"):
            result = ToolPermissionResult(
                allowed=False,
                error_code=ToolErrorCode.TOOL_APPROVAL_REQUIRED,
                message=f"Tool '{tool_name}' requires approval by policy",
                agent_id=agent_id,
                tool_name=tool_name,
                action=ctx.get("action"),
                requires_approval=True,
                approval_action=ctx.get("action") or tool_name,
            )
            self._audit_log(result, ctx)
            return result

        action = ctx.get("action")
        if action and action in agent.approval_required_actions:
            result = ToolPermissionResult(
                allowed=False,
                error_code=ToolErrorCode.TOOL_APPROVAL_REQUIRED,
                message=f"Action '{action}' requires approval",
                agent_id=agent_id,
                tool_name=tool_name,
                action=action,
                requires_approval=True,
                approval_action=action,
            )
            self._audit_log(result, ctx)
            return result

        # All checks passed
        result = ToolPermissionResult(
            allowed=True,
            error_code=ToolErrorCode.ALLOWED,
            message="Tool permission granted",
            agent_id=agent_id,
            tool_name=tool_name,
            risk_level=agent.risk_level.value,
        )
        self._audit_log(result, ctx)
        return result

    def _get_tool_risk_level(self, tool_name: str) -> str:
        """Get risk level for a tool."""
        policy = self._tool_policies.get(tool_name)
        if policy and policy.get("risk"):
            return str(policy["risk"])
        return self.HIGH_RISK_TOOLS.get(tool_name, "low")

    def _audit_log(
        self,
        result: ToolPermissionResult,
        context: dict[str, Any],
    ) -> None:
        """Log permission check result for audit."""
        log_data = {
            "agent_id": result.agent_id,
            "tool_name": result.tool_name,
            "allowed": result.allowed,
            "error_code": result.error_code.value,
            "log_message": result.message,
        }

        if result.risk_level:
            log_data["risk_level"] = result.risk_level
        if result.requires_approval:
            log_data["requires_approval"] = True
        if result.approval_action:
            log_data["approval_action"] = result.approval_action

        if result.allowed:
            logger.info(
                "Tool permission granted",
                extra=log_data,
            )
        else:
            logger.warning(
                "Tool permission denied",
                extra=log_data,
            )

    def get_execution_limits(
        self,
        agent_id: str,
    ) -> Optional[dict[str, Any]]:
        """
        Get execution limits for an agent.

        Args:
            agent_id: ID of the agent

        Returns:
            Dict with timeout_seconds, retry_count, max_subagents, or None if agent not found
        """
        return self.agent_registry.get_agent_execution_config(agent_id)  # type: ignore


class ToolPermissionError(Exception):
    """Exception raised when tool permission is denied."""

    def __init__(
        self,
        error_code: ToolErrorCode,
        message: str,
        agent_id: Optional[str] = None,
        tool_name: Optional[str] = None,
    ) -> None:
        self.error_code = error_code
        self.message = message
        self.agent_id = agent_id
        self.tool_name = tool_name
        super().__init__(f"{error_code.value}: {message}")


def check_agent_tool_permission(
    agent_id: str,
    tool_name: str,
    context: Optional[dict[str, Any]] = None,
    agent_registry: Optional[Any] = None,
) -> ToolPermissionResult:
    """
    Convenience function to check tool permission.

    Args:
        agent_id: ID of the agent
        tool_name: Name of the tool
        context: Optional context
        agent_registry: Optional registry to use

    Returns:
        ToolPermissionResult
    """
    guard = AgentToolGuard(agent_registry=agent_registry)
    return guard.check_permission(agent_id, tool_name, context)


def enforce_tool_permission(
    agent_id: str,
    tool_name: str,
    context: Optional[dict[str, Any]] = None,
    agent_registry: Optional[Any] = None,
) -> None:
    """
    Enforce tool permission - raises exception if denied.

    Args:
        agent_id: ID of the agent
        tool_name: Name of the tool
        context: Optional context
        agent_registry: Optional registry to use

    Raises:
        ToolPermissionError: If permission is denied
    """
    guard = AgentToolGuard(agent_registry=agent_registry)
    result = guard.check_permission(agent_id, tool_name, context)

    if not result.allowed:
        raise ToolPermissionError(
            error_code=result.error_code,
            message=result.message,
            agent_id=agent_id,
            tool_name=tool_name,
        )
