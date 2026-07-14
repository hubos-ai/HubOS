# -*- coding: utf-8 -*-
"""Feishu Multi-User Gateway.

Routes incoming Feishu messages to per-user workspaces by hooking into
the single shared FeishuChannel's process handler.

Architecture::

    Feishu WS → FeishuChannel → Queue → consume_one → _process (OUR ROUTER)
                                                      |
                                          +-----------+-----------+
                                          |                       |
                                          v                       v
                                   FeishuUser1              FeishuUser2
                                       |                        |
                                       v                        v
                               Workspace(feishu_1)      Workspace(feishu_2)
                               [memory/chats/files]     [memory/chats/files]

Usage (in _app.py)::

    gateway = FeishuGateway(multi_agent_manager)
    default_agent = await multi_agent_manager.get_agent("default")
    feishu_channel = default_agent.channel_manager.channels.get("feishu")
    if feishu_channel:
        gateway.install_on(feishu_channel)
    await gateway.start()   # simply marks ready
"""

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any, Callable, Optional

from .multi_agent_manager import feishu_workspace_id_for_open_id

if TYPE_CHECKING:
    from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest

logger = logging.getLogger(__name__)

# Type for the channel's process handler
ProcessFunc = Callable[["AgentRequest"], AsyncGenerator[Any, None]]


class FeishuGateway:
    """Routes Feishu messages to per-user workspaces via process handler hook.

    Does NOT create a separate WS connection; instead it wraps the
    process handler on the *already-running* FeishuChannel managed by
    the default workspace's ChannelManager.

    Each Feishu user gets an independent workspace (``feishu_<open_id>``)
    with isolated memory, conversation history, and generated files.
    """

    def __init__(
        self,
        multi_agent_manager: Any,
    ):
        self.mam = multi_agent_manager
        self._original_process: Optional[ProcessFunc] = None
        self._channel_ref: Optional[Any] = None
        self._started = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def install_on(self, feishu_channel: Any) -> None:
        """Replace the channel's process handler with the multi-user router.

        Must be called before the channel processes its first message
        (i.e. during app startup).

        Args:
            feishu_channel: The existing ``FeishuChannel`` instance
                from the default workspace's ``ChannelManager``.
        """
        # Keep a reference so the router can access send-message methods
        # via the channel's on_event_message_completed etc.
        self._channel_ref = feishu_channel

        # Save the original process so we can fall through for non-Feishu
        # message types (e.g. system events, card actions).
        self._original_process = feishu_channel._process  # type: ignore[attr-defined]  # noqa: E501  # pylint: disable=protected-access

        # Replace with our router
        feishu_channel._process = self._route  # type: ignore[attr-defined]  # noqa: E501  # pylint: disable=protected-access

        # Install workspace resolver on the channel so
        # BaseChannel._resolve_owner_workspace can find the correct
        # per-user workspace for chat/task_tracker BEFORE _process runs.
        feishu_channel._owner_workspace_resolver = (  # type: ignore[attr-defined]  # noqa: E501  # pylint: disable=protected-access
            self._resolve_workspace_for_request
        )

        logger.info("FeishuGateway: routing handler installed")

    async def _resolve_workspace_for_request(
        self,
        request: "AgentRequest",
        payload: Any,
    ):
        """Resolve the per-user workspace for a Feishu request.

        Used as a callback by BaseChannel._resolve_owner_workspace.
        Returns the per-user workspace if found, else None (caller falls
        back to self._workspace).
        """
        # The real Feishu open_id lives in payload.meta.feishu_sender_id.
        # payload["user_id"] / payload["sender_id"] may be display names
        # (e.g. "nickname#suffix") and MUST NOT be used for workspace lookup.
        open_id = ""
        if isinstance(payload, dict):
            meta = payload.get("meta") or {}
            open_id = meta.get("feishu_sender_id", "") or ""
        if not open_id:
            # Fallback: AgentRequest.user_id is set correctly by
            # build_agent_request_from_native (which reads meta).
            open_id = getattr(request, "user_id", "") or ""

        if not open_id:
            return None

        # Fast path: already cached in manager
        ws_id = feishu_workspace_id_for_open_id(open_id)
        ws = self.mam.agents.get(ws_id)
        if ws is not None:
            return ws

        # Slow path: create workspace (first message from this user)
        return await self.mam.get_or_create_feishu_workspace(open_id)

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    async def _route(
        self,
        request: "AgentRequest",
    ) -> AsyncGenerator[Any, None]:
        """Route an AgentRequest to the per-user workspace runner.

        The Feishu channel calls this as ``self._process(request)``
        inside ``consume_one`` -> ``_run_process_loop``.

        - If the request has a ``user_id`` (Feishu open_id), it is
          routed to the corresponding per-user workspace.
        - Otherwise, the original process handler is used as fallback.
        """
        user_id = getattr(request, "user_id", None) or ""
        if not user_id:
            # No user context — use the original default workspace runner
            if self._original_process is not None:
                async for event in self._original_process(request):
                    yield event
            return

        # Get or create the per-user workspace
        workspace = await self.mam.get_or_create_feishu_workspace(user_id)
        if workspace is None:
            logger.warning(
                "FeishuGateway: no workspace for user %s, "
                "falling back to default",
                user_id,
            )
            if self._original_process is not None:
                async for event in self._original_process(request):
                    yield event
            return

        # Tag the request with the owner workspace_id so that
        # BaseChannel._resolve_owner_workspace can find the correct
        # workspace for chat/task_tracker before _process is called.
        setattr(request, "_hubos_workspace_id", workspace.agent_id)

        # Route to the per-user workspace runner
        async for event in workspace.runner.stream_query(request):
            yield event

    def is_installed(self) -> bool:
        """Check if the gateway is installed on a channel."""
        return self._original_process is not None

    async def start(self) -> None:
        """Mark the gateway as ready (no-op for this architecture).

        In this design there is no separate WS connection to start;
        the existing FeishuChannel's WS is already running. This method
        exists for interface consistency.
        """
        if self._started:
            return
        if not self.is_installed():
            logger.warning(
                "FeishuGateway.start() called but not installed on any "
                "channel — call install_on() first or no Feishu channel "
                "is configured.",
            )
        self._started = True
        logger.info("FeishuGateway active")

    async def stop(self) -> None:
        """Restore the original process handler (undo routing).

        Note: does NOT stop the underlying Feishu WS connection — that
        is managed by the default workspace's ChannelManager lifecycle.
        """
        if not self._started:
            return

        if (
            self._channel_ref is not None
            and self._original_process is not None
        ):
            self._channel_ref._process = self._original_process  # type: ignore[attr-defined]  # noqa: E501  # pylint: disable=protected-access

        self._original_process = None
        self._channel_ref = None
        self._started = False
        logger.info("FeishuGateway stopped — original routing restored")
