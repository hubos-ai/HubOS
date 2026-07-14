# -*- coding: utf-8 -*-
"""Handler for /stop command.

The /stop command immediately terminates an ongoing agent task.
"""

from __future__ import annotations

import logging

from .base import BaseControlCommandHandler, ControlContext

logger = logging.getLogger(__name__)


class StopCommandHandler(BaseControlCommandHandler):
    """Handler for /stop command.

    Features:
    - Immediate response (priority level 0)
    - Stops task via TaskTracker.request_stop (native cancellation)
    - Default: stops current session
    - Optional: specify target session_id

    Usage:
        /stop                  # Stop current session
        /stop session=console:user1  # Stop specific session
    """

    command_name = "/stop"

    async def handle(self, context: ControlContext) -> str:
        """Handle /stop command.

        Args:
            context: Control command context

        Returns:
            Response text (success or error message)
        """
        target_session_id = context.args.get(
            "session",
            context.session_id,
        )

        logger.info(
            f"/stop command: current_session={context.session_id[:30]} "
            f"target_session={target_session_id[:30]}",
        )

        workspace = context.workspace
        channel_id = context.channel.channel

        chat_id = await workspace.chat_manager.get_chat_id_by_session(
            target_session_id,
            channel_id,
        )

        if chat_id is None:
            logger.warning(
                f"/stop: No active chat found for "
                f"session={target_session_id[:30]} channel={channel_id}",
            )
            return (
                f"**No Active Task**\n\n"
                f"No running task found for session "
                f"`{target_session_id[:40]}`."
            )

        stopped = await workspace.task_tracker.request_stop(chat_id)

        channel_manager = workspace.channel_manager or getattr(
            context.channel,
            "_channel_manager",
            None,
        )
        if channel_manager is not None:
            cleared = await channel_manager.clear_queue(
                channel_id,
                target_session_id,
                20,
            )
        else:
            cleared = 0

        # RunControl: cancel all background runs (sub-agents, workflows, plans)
        bg_cancelled = 0
        try:
            from ...run_control import get_run_control_store

            workspace_id = getattr(workspace, "agent_id", None) or getattr(
                workspace,
                "workspace_id",
                None,
            )
            _cancelled_ids = await get_run_control_store().cancel_all(
                target_session_id,
                workspace_id=workspace_id,
            )
            bg_cancelled = len(_cancelled_ids)
        except Exception:  # noqa: BLE001
            pass

        if stopped or cleared > 0 or bg_cancelled > 0:
            logger.info(
                f"/stop: stopped={stopped} cleared={cleared} "
                f"bg_cancelled={bg_cancelled} "
                f"chat_id={chat_id} session={target_session_id[:30]}",
            )
            status_parts = []
            if stopped:
                status_parts.append("running task stopped")
            if cleared > 0:
                status_parts.append(f"{cleared} queued message(s) cleared")
            if bg_cancelled > 0:
                status_parts.append(
                    f"{bg_cancelled} background task(s) cancelled",
                )
            status_text = " and ".join(status_parts)
            return (
                f"**Task Stopped**\n\n"
                f"Session `{target_session_id[:40]}`: {status_text}."
            )
        else:
            logger.warning(
                f"/stop: Nothing to stop: "
                f"chat_id={chat_id} session={target_session_id[:30]}",
            )
            return (
                f"**Task Not Running**\n\n"
                f"No active task or queued messages for session "
                f"`{target_session_id[:40]}`."
            )
