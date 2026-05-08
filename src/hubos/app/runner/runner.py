# -*- coding: utf-8 -*-
# pylint: disable=unused-argument too-many-branches too-many-statements
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any


from agentscope.message import Msg, TextBlock
from agentscope.pipeline import stream_printing_messages
from agentscope_runtime.engine.runner import Runner
from agentscope_runtime.engine.schemas.agent_schemas import AgentRequest
from agentscope_runtime.engine.schemas.exception import AgentException
from dotenv import load_dotenv

from .command_dispatch import (
    _get_last_user_text,
    _is_command,
    run_command_path,
)
from .query_error_dump import write_query_error_dump
from .session import SafeJSONSession, prune_stale_session_messages
from .utils import build_env_context
from ..channels.schema import DEFAULT_CHANNEL
from ...agents.react_agent import HubOSAgent
from ...security.tool_guard.models import TOOL_GUARD_DENIED_MARK
from ...config.config import load_agent_config
from ...constant import (
    TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS,
    WORKING_DIR,
)
from ...security.tool_guard.approval import ApprovalDecision

if TYPE_CHECKING:
    from ...agents.memory import BaseMemoryManager

logger = logging.getLogger(__name__)

_APPROVE_EXACT = frozenset(
    {
        "approve",
        "/approve",
        "/daemon approve",
    },
)


def _is_approval(text: str) -> bool:
    """Return True only when *text* is exactly ``approve``,
    ``/approve``, or ``/daemon approve`` (case-insensitive).

    Leading/trailing whitespace and blank lines are stripped before
    comparison.  Everything else is treated as denial.
    """
    normalized = " ".join(text.split()).lower()
    return normalized in _APPROVE_EXACT


class AgentRunner(Runner):
    """Agent runner with optional instance pooling.

    When ``enable_pool=True``, HubOSAgent instances are cached per
    ``agent_id`` and reused across requests.  On each request only the
    lightweight per-session state (request_context, memory, sys_prompt)
    is refreshed — avoiding the cost of re-registering skills, MCP
    clients, and rebuilding the toolkit.
    """

    # Pool config (class-level defaults)
    _POOL_MAX_SIZE = 32
    _POOL_IDLE_TTL_SECS = 3600.0  # evict after 1 h idle

    def __init__(
        self,
        agent_id: str = "default",
        workspace_dir: Path | None = None,
        task_tracker: Any | None = None,
        enable_pool: bool = True,
    ) -> None:
        super().__init__()
        self.framework_type = "agentscope"
        self.agent_id = agent_id  # Store agent_id for config loading
        self.workspace_dir = (
            workspace_dir  # Store workspace_dir for prompt building
        )
        self._chat_manager = None  # Store chat_manager reference
        self._mcp_manager = None  # MCP client manager for hot-reload
        self._workspace: Any = None  # Workspace instance for control commands
        self.memory_manager: BaseMemoryManager | None = None
        self._task_tracker = task_tracker  # Task tracker for background tasks

        # Agent instance pool: agent_id → (HubOSAgent, timestamp)
        self._enable_pool = enable_pool
        self._agent_pool: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._agent_pool_lock = threading.Lock()

    def set_chat_manager(self, chat_manager):
        """Set chat manager for auto-registration.

        Args:
            chat_manager: ChatManager instance
        """
        self._chat_manager = chat_manager

    def set_mcp_manager(self, mcp_manager):
        """Set MCP client manager for hot-reload support.

        Args:
            mcp_manager: MCPClientManager instance
        """
        self._mcp_manager = mcp_manager

    def set_workspace(self, workspace):
        """Set workspace for control command handlers.

        Args:
            workspace: Workspace instance
        """
        self._workspace = workspace

    # ------------------------------------------------------------------
    # Agent instance pool
    # ------------------------------------------------------------------

    async def _get_or_create_agent(
        self,
        agent_config: Any,
        env_context: str,
        mcp_clients: list,
        request_context: dict,
    ) -> HubOSAgent:
        """Return a HubOSAgent from the pool or create a new one.

        Pooled agents retain their toolkit, skills, and MCP client
        registrations.  Per-request state (request_context, memory)
        is refreshed before the agent is returned.

        **Concurrency safety**: If the pooled instance is currently
        in-use (borrowed by another concurrent request), a fresh
        temporary agent is created instead.  This prevents memory
        cross-contamination between sessions.
        """
        if not self._enable_pool:
            return await self._create_agent(
                agent_config,
                env_context,
                mcp_clients,
                request_context,
            )

        pool_key = self.agent_id
        was_borrowed = False

        with self._agent_pool_lock:
            self._evict_expired_entries()

            if pool_key in self._agent_pool:
                agent, _ts = self._agent_pool[pool_key]
                # Check if already borrowed by a concurrent request
                if getattr(agent, "_pool_borrowed", False):
                    was_borrowed = True
                    logger.info(
                        "Agent pool HIT but BORROWED for '%s' "
                        "— creating temporary instance",
                        pool_key,
                    )
                    # Fall through to create a new one
                else:
                    # Mark as borrowed and remove from pool
                    agent._pool_borrowed = True  # noqa: SLF001
                    del self._agent_pool[pool_key]
                    logger.info(
                        "Agent pool HIT for '%s' — reusing instance",
                        pool_key,
                    )
                    self._refresh_agent(agent, request_context)
                    return agent

        # Cache miss or borrowed — create new
        is_temporary = (
            was_borrowed  # only temp if pool had one but it was in use
        )
        agent = await self._create_agent(
            agent_config,
            env_context,
            mcp_clients,
            request_context,
        )
        if is_temporary:
            agent._pool_is_temporary = True  # noqa: SLF001
            logger.info(
                "Agent pool MISS for '%s' — created temporary instance",
                pool_key,
            )
        else:
            logger.info(
                "Agent pool MISS for '%s' — created new instance",
                pool_key,
            )
        return agent

    async def _create_agent(
        self,
        agent_config: Any,
        env_context: str,
        mcp_clients: list,
        request_context: dict,
    ) -> HubOSAgent:
        """Create a fresh HubOSAgent and register MCP clients."""
        agent = HubOSAgent(
            agent_config=agent_config,
            env_context=env_context,
            mcp_clients=mcp_clients,
            memory_manager=self.memory_manager,
            request_context=request_context,
            workspace_dir=self.workspace_dir,
            task_tracker=self._task_tracker,
        )
        await agent.register_mcp_clients()
        agent.set_console_output_enabled(enabled=False)
        return agent

    def _refresh_agent(
        self,
        agent: HubOSAgent,
        request_context: dict,
    ) -> None:
        """Reset per-session state on a pooled agent for reuse."""
        # Update request context
        agent._request_context = dict(request_context)  # noqa: SLF001
        from ...agents.react_agent import set_runtime_request_context

        set_runtime_request_context(agent._request_context)  # noqa: SLF001

        # Reset in-memory conversation history so stale messages from
        # the previous session don't leak into the new one.
        from agentscope.memory import InMemoryMemory

        agent.memory = InMemoryMemory()

        # Console output stays disabled
        agent.set_console_output_enabled(enabled=False)

    def _return_agent(self, agent: HubOSAgent) -> None:
        """Return an agent to the pool after use.

        Temporary agents (created because the pooled instance was
        borrowed) are simply discarded.  The primary pooled instance
        is put back for reuse.
        """
        if not self._enable_pool:
            return

        # Temporary agents are not returned to pool
        if getattr(agent, "_pool_is_temporary", False):
            logger.debug("Discarding temporary agent instance")
            return

        pool_key = self.agent_id
        agent._pool_borrowed = False  # noqa: SLF001
        with self._agent_pool_lock:
            self._agent_pool[pool_key] = (agent, time.monotonic())
            self._agent_pool.move_to_end(pool_key)

            # Evict if over capacity
            while len(self._agent_pool) > self._POOL_MAX_SIZE:
                evicted_key, _ = self._agent_pool.popitem(last=False)
                logger.info(
                    "Agent pool evicted '%s' (over capacity)",
                    evicted_key,
                )

    def _evict_expired_entries(self) -> None:
        """Remove entries idle beyond TTL. Must be called under lock."""
        now = time.monotonic()
        expired = [
            k
            for k, (_, ts) in self._agent_pool.items()
            if now - ts > self._POOL_IDLE_TTL_SECS
        ]
        for k in expired:
            del self._agent_pool[k]
            logger.info("Agent pool evicted '%s' (idle TTL)", k)

    _APPROVAL_TIMEOUT_SECONDS = TOOL_GUARD_APPROVAL_TIMEOUT_SECONDS

    async def _resolve_pending_approval(
        self,
        session_id: str,
        query: str | None,
    ) -> tuple[Msg | None, bool, dict[str, Any] | None]:
        """Check for a pending tool-guard approval for *session_id*.

        Returns ``(response_msg, was_consumed, approved_tool_call)``:

        - ``(None, False, None)`` — no pending approval, continue normally.
        - ``(Msg, True, None)``   — denied; yield the Msg and stop.
        - ``(None, True, dict)``  — approved with stored tool call.

        Approvals are resolved FIFO per session (oldest pending first).
        """
        if not session_id:
            return None, False, None

        from ..approvals import get_approval_service

        svc = get_approval_service()
        pending = await svc.get_pending_by_session(session_id)
        if pending is None:
            return None, False, None

        elapsed = time.time() - pending.created_at
        if elapsed > self._APPROVAL_TIMEOUT_SECONDS:
            await svc.resolve_request(
                pending.request_id,
                ApprovalDecision.TIMEOUT,
            )
            return (
                Msg(
                    name="Friday",
                    role="assistant",
                    content=[
                        TextBlock(
                            type="text",
                            text=(
                                f"⏰ Tool `{pending.tool_name}` approval "
                                f"timed out ({int(elapsed)}s) — denied.\n"
                                f"工具 `{pending.tool_name}` 审批超时"
                                f"（{int(elapsed)}s），已拒绝执行。"
                            ),
                        ),
                    ],
                ),
                True,
                None,
            )

        normalized = (query or "").strip().lower()
        if _is_approval(normalized):
            resolved = await svc.resolve_request(
                pending.request_id,
                ApprovalDecision.APPROVED,
            )
            approved_tool_call: dict[str, Any] | None = None
            record = resolved or pending
            if isinstance(record.extra, dict):
                candidate = record.extra.get("tool_call")
                if isinstance(candidate, dict):
                    approved_tool_call = dict(candidate)
                    siblings = record.extra.get("sibling_tool_calls")
                    if isinstance(siblings, list):
                        approved_tool_call["_sibling_tool_calls"] = siblings
                    remaining = record.extra.get("remaining_queue")
                    if isinstance(remaining, list):
                        approved_tool_call["_remaining_queue"] = remaining
                    thinking_blocks = record.extra.get("thinking_blocks")
                    if isinstance(thinking_blocks, list):
                        approved_tool_call["_thinking_blocks"] = (
                            thinking_blocks
                        )
            return None, True, approved_tool_call

        await svc.resolve_request(
            pending.request_id,
            ApprovalDecision.DENIED,
        )
        return (
            Msg(
                name="Friday",
                role="assistant",
                content=[
                    TextBlock(
                        type="text",
                        text=(
                            f"❌ Tool `{pending.tool_name}` denied.\n"
                            f"工具 `{pending.tool_name}` 已拒绝执行。"
                        ),
                    ),
                ],
            ),
            True,
            None,
        )

    async def query_handler(
        self,
        msgs,
        request: AgentRequest = None,
        **kwargs,
    ):
        """
        Handle agent query.
        """
        logger.debug(
            f"AgentRunner.query_handler called: agent_id={self.agent_id}, "
            f"msgs={msgs}, request={request}",
        )
        query = _get_last_user_text(msgs)
        session_id = getattr(request, "session_id", "") or ""

        (
            approval_response,
            approval_consumed,
            approved_tool_call,
        ) = await self._resolve_pending_approval(session_id, query)
        if approval_response is not None:
            yield approval_response, True
            user_id = getattr(request, "user_id", "") or ""
            await self._cleanup_denied_session_memory(
                session_id,
                user_id,
                denial_response=approval_response,
            )
            return

        if not approval_consumed and query and _is_command(query):
            logger.info("Command path: %s", query.strip()[:50])
            async for msg, last in run_command_path(request, msgs, self):
                yield msg, last
            return

        logger.debug(
            f"AgentRunner.stream_query: request={request}, "
            f"agent_id={self.agent_id}",
        )

        # Set agent context for model creation
        from ..agent_context import set_current_agent_id

        set_current_agent_id(self.agent_id)

        agent = None
        chat = None
        session_state_loaded = False
        chat_turn_started_at = time.time()
        final_response_text = ""
        try:
            session_id = request.session_id
            user_id = request.user_id
            channel = getattr(request, "channel", DEFAULT_CHANNEL)

            logger.info(
                "Handle agent query:\n%s",
                json.dumps(
                    {
                        "session_id": session_id,
                        "user_id": user_id,
                        "channel": channel,
                        "msgs_len": len(msgs) if msgs else 0,
                        "msgs_str": str(msgs)[:300] + "...",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )

            env_context = build_env_context(
                session_id=session_id,
                user_id=user_id,
                channel=channel,
                working_dir=(
                    str(self.workspace_dir)
                    if self.workspace_dir
                    else str(WORKING_DIR)
                ),
            )

            try:
                from ...core.infra.feature_flags import get_feature_flags

                flags = get_feature_flags()
                if flags.use_work_experience():
                    from ...core.work_experience.integration_v4 import (
                        get_work_experience_interceptor,
                    )

                    interceptor = get_work_experience_interceptor()
                    matched_card = interceptor.pre_execute(
                        user_message=query or "",
                        session_id=session_id,
                    )
                else:
                    matched_card = None
                if matched_card:
                    card_guidance = matched_card.formatted_for_injection()
                    env_context = (
                        f"{env_context}\n\n---\n"
                        f"📌 相关工作经验（参考以下流程执行，但不要逐字复述）：\n"
                        f"{card_guidance}\n---\n"
                    )
                    logger.info(
                        "WorkExperience v4 guidance injected",
                        extra={
                            "session_id": session_id,
                            "agent_id": self.agent_id,
                            "card_id": matched_card.card_id,
                            "task_type": matched_card.task_type,
                        },
                    )
            except Exception:
                logger.warning(
                    "WorkExperience v4 retrieval/injection failed; "
                    "continuing without guidance",
                    exc_info=True,
                )

            # Get MCP clients from manager (hot-reloadable)
            mcp_clients = []
            if self._mcp_manager is not None:
                mcp_clients = await self._mcp_manager.get_clients()

            # Load agent-specific configuration
            agent_config = load_agent_config(self.agent_id)

            request_context = {
                "session_id": session_id,
                "user_id": user_id,
                "channel": channel,
                "agent_id": self.agent_id,
                **(
                    {
                        "forced_tool_call_json": json.dumps(
                            approved_tool_call,
                            ensure_ascii=False,
                        ),
                    }
                    if approved_tool_call
                    else {}
                ),
            }

            agent = await self._get_or_create_agent(
                agent_config=agent_config,
                env_context=env_context,
                mcp_clients=mcp_clients,
                request_context=request_context,
            )

            logger.debug(
                f"Agent Query msgs {msgs}",
            )

            name = "New Chat"
            if len(msgs) > 0:
                content = msgs[0].get_text_content()
                if content:
                    name = msgs[0].get_text_content()[:10]
                else:
                    name = "Media Message"

            logger.debug(
                f"DEBUG chat_manager status: "
                f"_chat_manager={self._chat_manager}, "
                f"is_none={self._chat_manager is None}, "
                f"agent_id={self.agent_id}",
            )

            if self._chat_manager is not None:
                logger.debug(
                    f"Runner: Calling get_or_create_chat for "
                    f"session_id={session_id}, user_id={user_id}, "
                    f"channel={channel}, name={name}",
                )
                chat = await self._chat_manager.get_or_create_chat(
                    session_id,
                    user_id,
                    channel,
                    name=name,
                )
                logger.debug(f"Runner: Got chat: {chat.id}")
            else:
                logger.warning(
                    f"ChatManager is None! Cannot auto-register chat for "
                    f"session_id={session_id}",
                )

            try:
                await self.session.load_session_state(
                    session_id=session_id,
                    user_id=user_id,
                    agent=agent,
                )
            except KeyError as e:
                logger.warning(
                    "load_session_state skipped (state schema mismatch): %s; "
                    "will save fresh state on completion to recover file",
                    e,
                )
            session_state_loaded = True

            # Phase-2 token optimization: drop messages older than 2 hours.
            # Always keeps system messages + last 10 non-system messages.
            if hasattr(agent, "memory") and agent.memory is not None:
                prune_stale_session_messages(
                    agent.memory,
                    max_age_hours=2.0,
                    min_keep=10,
                )

            # Rebuild system prompt so it always reflects the latest
            # AGENTS.md / SOUL.md / PROFILE.md, not the stale one saved
            # in the session state.
            agent.rebuild_sys_prompt()

            async for msg, last in stream_printing_messages(
                agents=[agent],
                coroutine_task=agent(msgs),
            ):
                if last and getattr(msg, "role", None) == "assistant":
                    text = ""
                    try:
                        text = msg.get_text_content() or ""
                    except Exception:
                        text = ""
                    if text.strip():
                        final_response_text = text
                yield msg, last

            if not final_response_text.strip():
                logger.warning(
                    "Agent returned empty response for session=%s "
                    "channel=%s — session may be too large or LLM "
                    "returned empty content",
                    session_id[:20],
                    channel,
                )

        except asyncio.CancelledError as exc:
            logger.info(f"query_handler: {session_id} cancelled!")
            if agent is not None:
                await agent.interrupt()
            raise AgentException("Task has been cancelled!") from exc
        except Exception as e:
            debug_dump_path = write_query_error_dump(
                request=request,
                exc=e,
                locals_=locals(),
            )
            path_hint = (
                f"\n(Details:  {debug_dump_path})" if debug_dump_path else ""
            )
            logger.exception(f"Error in query handler: {e}{path_hint}")
            if debug_dump_path:
                setattr(e, "debug_dump_path", debug_dump_path)
                if hasattr(e, "add_note"):
                    e.add_note(
                        f"(Details:  {debug_dump_path})",
                    )
                suffix = f"\n(Details:  {debug_dump_path})"
                e.args = (
                    (f"{e.args[0]}{suffix}" if e.args else suffix.strip()),
                ) + e.args[1:]
            raise
        finally:
            if agent is not None and final_response_text.strip():
                try:
                    from ...core.infra.feature_flags import get_feature_flags

                    if get_feature_flags().use_work_experience():
                        from ...core.work_experience.integration_v4 import (
                            get_work_experience_interceptor,
                        )

                        interceptor = get_work_experience_interceptor()
                        interceptor.post_chat_turn(
                            session_id=session_id,
                            user_input=query or "",
                            assistant_response=final_response_text,
                            channel=channel,
                            agent_id=self.agent_id,
                            execution_time_ms=int(
                                (time.time() - chat_turn_started_at) * 1000,
                            ),
                        )
                except Exception:
                    logger.warning(
                        "Failed to persist WorkExperience v4 from chat turn",
                        exc_info=True,
                    )

            if agent is not None and session_state_loaded:
                await self.session.save_session_state(
                    session_id=session_id,
                    user_id=user_id,
                    agent=agent,
                )

            if self._chat_manager is not None and chat is not None:
                await self._chat_manager.update_chat(chat)

            # Return agent to the pool for reuse
            if agent is not None:
                self._return_agent(agent)

    async def _cleanup_denied_session_memory(
        self,
        session_id: str,
        user_id: str,
        denial_response: "Msg | None" = None,
    ) -> None:
        """Clean up session memory after a tool-guard denial.

        In the deny path (no agent is created), this method:

        1. Removes the LLM denial explanation (the assistant message
           immediately following the last marked entry).
        2. Strips ``TOOL_GUARD_DENIED_MARK`` from all marks lists so
           the kept tool-call info becomes normal memory entries.
        3. Appends *denial_response* (e.g. "❌ Tool denied") to the
           persisted session memory.
        """
        if not hasattr(self, "session") or self.session is None:
            return

        path = self.session._get_save_path(  # pylint: disable=protected-access
            session_id,
            user_id,
        )
        if not Path(path).exists():
            return

        try:
            with open(
                path,
                "r",
                encoding="utf-8",
                errors="surrogatepass",
            ) as f:
                states = json.load(f)

            agent_state = states.get("agent", {})
            memory_state = agent_state.get("memory", {})
            content = memory_state.get("content", [])

            if not content:
                return

            def _is_marked(entry):
                return (
                    isinstance(entry, list)
                    and len(entry) >= 2
                    and isinstance(entry[1], list)
                    and TOOL_GUARD_DENIED_MARK in entry[1]
                )

            last_marked_idx = -1
            for i, entry in enumerate(content):
                if _is_marked(entry):
                    last_marked_idx = i

            modified = False

            if last_marked_idx >= 0 and last_marked_idx + 1 < len(content):
                next_entry = content[last_marked_idx + 1]
                if (
                    isinstance(next_entry, list)
                    and len(next_entry) >= 1
                    and isinstance(next_entry[0], dict)
                    and next_entry[0].get("role") == "assistant"
                ):
                    del content[last_marked_idx + 1]
                    modified = True

            for entry in content:
                if _is_marked(entry):
                    entry[1].remove(TOOL_GUARD_DENIED_MARK)
                    modified = True

            if denial_response is not None:
                ts = getattr(denial_response, "timestamp", None)
                msg_dict = {
                    "id": getattr(denial_response, "id", ""),
                    "name": getattr(denial_response, "name", "Friday"),
                    "role": getattr(denial_response, "role", "assistant"),
                    "content": denial_response.content,
                    "metadata": getattr(
                        denial_response,
                        "metadata",
                        None,
                    ),
                    "timestamp": str(ts) if ts is not None else "",
                }
                content.append([msg_dict, []])
                modified = True

            if modified:
                with open(
                    path,
                    "w",
                    encoding="utf-8",
                    errors="surrogatepass",
                ) as f:
                    json.dump(states, f, ensure_ascii=False)
                logger.info(
                    "Tool guard: cleaned up denied session memory in %s",
                    path,
                )
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to clean up denied messages from session %s",
                session_id,
                exc_info=True,
            )

    async def init_handler(self, *args, **kwargs):
        """
        Init handler.
        """
        # Load environment variables from .env file
        # env_path = Path(__file__).resolve().parents[4] / ".env"
        env_path = Path("./") / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.debug(f"Loaded environment variables from {env_path}")
        else:
            logger.debug(
                f".env file not found at {env_path}, "
                "using existing environment variables",
            )

        session_dir = str(
            (self.workspace_dir if self.workspace_dir else WORKING_DIR)
            / "sessions",
        )
        self.session = SafeJSONSession(save_dir=session_dir)

    async def shutdown_handler(self, *args, **kwargs):
        """
        Shutdown handler.
        """
