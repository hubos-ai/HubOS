"""Regression tests for pending tool-approval message handling."""
from __future__ import annotations

import pytest

from hubos.app.approvals.service import ApprovalService
from hubos.app.channels.command_registry import CommandRegistry
from hubos.app.runner.runner import AgentRunner, _is_denial
from hubos.security.tool_guard.models import ToolGuardResult


@pytest.fixture
def approval_service(monkeypatch: pytest.MonkeyPatch) -> ApprovalService:
    from hubos.app.approvals import service as service_module

    service = ApprovalService()
    monkeypatch.setattr(service_module, "_approval_service", service)
    return service


async def _create_pending(
    service: ApprovalService,
    *,
    session_id: str = "session-1",
):
    return await service.create_pending(
        session_id=session_id,
        user_id="user-1",
        channel="feishu",
        tool_name="execute_shell_command",
        result=ToolGuardResult(
            tool_name="execute_shell_command",
            params={"command": "rm important.txt"},
        ),
        extra={
            "tool_call": {
                "id": "tool-1",
                "name": "execute_shell_command",
                "input": {"command": "rm important.txt"},
            },
        },
    )


@pytest.mark.asyncio
async def test_normal_message_cancels_all_pending_and_continues(
    approval_service: ApprovalService,
) -> None:
    first = await _create_pending(approval_service)
    second = await _create_pending(approval_service)
    runner = AgentRunner.__new__(AgentRunner)

    response, consumed, tool_call, silently_cancelled = (
        await runner._resolve_pending_approval("session-1", "请继续新任务")
    )

    assert response is None
    assert consumed is False
    assert tool_call is None
    assert silently_cancelled is True
    assert await approval_service.get_all_pending_by_session("session-1") == []
    assert (await approval_service.get_request(first.request_id)).status == "denied"
    assert (await approval_service.get_request(second.request_id)).status == "denied"


@pytest.mark.asyncio
async def test_explicit_deny_returns_denial_message(
    approval_service: ApprovalService,
) -> None:
    await _create_pending(approval_service)
    runner = AgentRunner.__new__(AgentRunner)

    response, consumed, tool_call, silently_cancelled = (
        await runner._resolve_pending_approval("session-1", "/deny")
    )

    assert response is not None
    response_text = "\n".join(
        block.get("text", "")
        if isinstance(block, dict)
        else str(getattr(block, "text", ""))
        for block in response.content
    )
    assert "已拒绝执行" in response_text
    assert consumed is True
    assert tool_call is None
    assert silently_cancelled is False


@pytest.mark.asyncio
async def test_explicit_approve_replays_exact_tool_call(
    approval_service: ApprovalService,
) -> None:
    await _create_pending(approval_service)
    runner = AgentRunner.__new__(AgentRunner)

    response, consumed, tool_call, silently_cancelled = (
        await runner._resolve_pending_approval("session-1", "/approve")
    )

    assert response is None
    assert consumed is True
    assert tool_call == {
        "id": "tool-1",
        "name": "execute_shell_command",
        "input": {"command": "rm important.txt"},
    }
    assert silently_cancelled is False


@pytest.mark.asyncio
async def test_expired_approval_does_not_swallow_normal_message(
    approval_service: ApprovalService,
) -> None:
    pending = await _create_pending(approval_service)
    pending.created_at -= AgentRunner._APPROVAL_TIMEOUT_SECONDS + 1
    runner = AgentRunner.__new__(AgentRunner)

    response, consumed, tool_call, silently_cancelled = (
        await runner._resolve_pending_approval("session-1", "查看现在状态")
    )

    assert response is None
    assert consumed is False
    assert tool_call is None
    assert silently_cancelled is True
    assert (await approval_service.get_request(pending.request_id)).status == "timeout"


def test_denial_requires_an_explicit_command() -> None:
    assert _is_denial("/deny") is True
    assert _is_denial(" deny ") is True
    assert _is_denial("继续处理") is False


def test_denial_commands_have_approval_priority() -> None:
    registry = CommandRegistry()
    assert registry.get_priority_level("/deny") == 10
    assert registry.get_priority_level("/daemon deny") == 10
