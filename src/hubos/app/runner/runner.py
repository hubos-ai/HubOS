# -*- coding: utf-8 -*-
# pylint: disable=unused-argument too-many-branches too-many-statements
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Any


from agentscope.message import Msg, TextBlock
from agentscope.pipeline import stream_printing_messages
from agentscope_runtime.engine.runner import Runner
from agentscope_runtime.engine.schemas.agent_schemas import (
    AgentRequest,
    DataContent,
    Message,
    MessageType,
)
from agentscope_runtime.engine.schemas.exception import AgentException
from dotenv import load_dotenv

from .command_dispatch import (
    _get_last_user_text,
    _is_command,
    run_command_path,
)
from .query_error_dump import write_query_error_dump
from .session import (
    SafeJSONSession,
    compact_stale_session_messages_locally,
    prune_empty_assistant_messages,
)
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

_INTERNAL_STATUS_MEMORY_MARK = "hubos_internal_status"
_INTERNAL_STATUS_TOOL_NAMES = frozenset(
    {
        "Context understanding",
        "Experience matching",
        "Knowledge injection",
    },
)
# Maximum internal-status messages retained in memory.
# Each turn produces 3 status cards; keep the last 5 turns = 15 messages.
_MAX_STATUS_MESSAGES = 15
_EMPTY_RESPONSE_FALLBACK_TEXT = (
    "我在。刚才模型返回了空内容，HubOS 已经自动做了恢复处理。" "请把刚才的任务再发一次，我会继续处理。"
)


def _make_internal_status_msg(
    *,
    status_id: str,
    label: str,
    state: str,
    output: str | None = None,
) -> Msg:
    """Create a HubOS-internal status card message.

    These cards are UI progress markers, not model-selected tool calls.
    Keeping them on a dedicated block type prevents future LLM turns from
    seeing fake ``tool_use`` / ``tool_result`` history.
    """
    block: dict[str, Any] = {
        "type": "hubos_status",
        "id": status_id,
        "name": label,
        "status": state,
    }
    if output is not None:
        block["output"] = output
        block["content"] = output
        block["result"] = output
    return Msg(name="assistant", role="assistant", content=[block])


def _make_empty_response_fallback_msg() -> Msg:
    """Create a user-visible fallback when the LLM returns no text."""
    return Msg(
        name="Friday",
        role="assistant",
        content=[
            TextBlock(
                type="text",
                text=_EMPTY_RESPONSE_FALLBACK_TEXT,
            ),
        ],
    )


def _hubos_status_stream_converter(
    element: dict,
    _message: Message,
    _last: bool,
    _tool_start: bool,
    metadata: dict | None,
    usage: Any,
):
    """Convert ``hubos_status`` AgentScope blocks to runtime DataContent.

    AgentScope Runtime's built-in adapter only understands text/thinking/tool
    blocks.  Without this converter it renders the raw dict as text during
    streaming.  The frontend then maps this structured DataContent to the
    dedicated status-card UI.
    """
    status = element.get("status") or "in_progress"
    status_message = Message(type=MessageType.MESSAGE, role="assistant")
    status_message.metadata = metadata or {}
    status_message.usage = usage

    status_data = {
        "kind": "hubos_status",
        "id": element.get("id") or "",
        "call_id": element.get("id") or "",
        "name": element.get("name") or "Status",
        "status": status,
    }
    raw_output = element.get(
        "output",
        element.get("result", element.get("content")),
    )
    if raw_output is not None:
        status_data["output"] = (
            json.dumps(raw_output, ensure_ascii=False)
            if isinstance(raw_output, (dict, list))
            else raw_output
        )
        status_data["result"] = status_data["output"]

    data_content = DataContent(
        index=0,
        delta=False,
        data=status_data,
    )

    status_message.add_content(new_content=data_content)
    data_content.msg_id = status_message.id
    if status == "completed":
        yield status_message.in_progress()
        yield data_content.completed()
        yield status_message.completed()
    else:
        yield status_message.in_progress()
        yield data_content.in_progress()


def _is_approval(text: str) -> bool:
    """Return True only when *text* is exactly ``approve``,
    ``/approve``, or ``/daemon approve`` (case-insensitive).

    Leading/trailing whitespace and blank lines are stripped before
    comparison.  Everything else is treated as denial.
    """
    normalized = " ".join(text.split()).lower()
    return normalized in _APPROVE_EXACT


def _is_internal_status_msg(msg: Msg) -> bool:
    """Return True for HubOS pre-agent status card messages.

    They are rendered as tool cards in the UI, but they are not real tools
    chosen by the model and must not be replayed into future LLM context.
    """
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return False

    for block in content:
        if _is_internal_status_block(block):
            return True
    return False


def _is_internal_status_block(block: Any) -> bool:
    """Return True for internal status blocks, including legacy tool-shaped ones."""
    if not isinstance(block, dict):
        return False
    block_type = block.get("type")
    if block_type == "hubos_status":
        return True
    if block_type not in {"tool_use", "tool_result"}:
        return False
    name = block.get("name")
    if name in _INTERNAL_STATUS_TOOL_NAMES:
        return True
    block_id = str(block.get("id") or block.get("tool_use_id") or "")
    return block_id.startswith("status-")


def _strip_hallucinated_internal_status_blocks(
    msg: Msg,
) -> tuple[Msg | None, bool]:
    """Remove model-emitted fake calls to internal status phases.

    Runner-created ``hubos_status`` blocks are legitimate UI status cards and
    are not stripped here.  Only old-style/model-emitted ``tool_use`` /
    ``tool_result`` blocks targeting internal phase names are removed.
    """
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return msg, False

    filtered: list[Any] = []
    removed = False
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") in {"tool_use", "tool_result"}
            and _is_internal_status_block(block)
        ):
            removed = True
            continue
        filtered.append(block)

    if not removed:
        return msg, False
    if not filtered:
        return None, True

    return (
        Msg(
            name=msg.name,
            role=msg.role,
            content=filtered,
            metadata=msg.metadata,
            timestamp=msg.timestamp,
            invocation_id=msg.invocation_id,
        ),
        True,
    )


def _strip_hallucinated_internal_status_from_memory(memory: Any) -> None:
    """Scrub hallucinated internal status tool calls from persisted memory."""
    content = getattr(memory, "content", None)
    if not isinstance(content, list):
        return

    next_content = []
    stripped = 0
    for item in content:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            next_content.append(item)
            continue
        msg, marks = item
        if not isinstance(msg, Msg):
            next_content.append(item)
            continue
        cleaned, removed = _strip_hallucinated_internal_status_blocks(msg)
        if not removed:
            next_content.append(item)
            continue
        stripped += 1
        if cleaned is not None:
            next_content.append((cleaned, marks))

    if stripped:
        logger.warning(
            "Stripped %s hallucinated internal status message(s) from memory",
            stripped,
        )
        content[:] = next_content


def _mark_internal_status_messages(memory: Any) -> None:
    """Mark existing status cards in memory so model reads can exclude them.

    Also prunes old internal-status messages to prevent unbounded growth.
    Only the most recent ``_MAX_STATUS_MESSAGES`` are kept.
    """
    content = getattr(memory, "content", None)
    if not isinstance(content, list):
        return

    # Collect indices of internal status messages
    status_indices: list[int] = []
    for idx, item in enumerate(content):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        msg, marks = item
        if not isinstance(msg, Msg) or not _is_internal_status_msg(msg):
            continue
        # Ensure the mark is present
        if marks is None:
            next_marks: list[str] = []
        elif isinstance(marks, str):
            next_marks = [marks]
        else:
            next_marks = list(marks)
        if _INTERNAL_STATUS_MEMORY_MARK not in next_marks:
            next_marks.append(_INTERNAL_STATUS_MEMORY_MARK)
            content[idx] = (msg, next_marks)
        status_indices.append(idx)

    # Prune oldest status messages if exceeding the cap
    if len(status_indices) > _MAX_STATUS_MESSAGES:
        to_remove = set(status_indices[:-_MAX_STATUS_MESSAGES])
        # Remove from end to preserve earlier indices
        for idx in sorted(to_remove, reverse=True):
            content.pop(idx)


def _install_internal_status_memory_filter(memory: Any) -> Any:
    """Make ordinary model memory reads skip internal status card messages.

    The messages remain persisted for history rendering.  Only unqualified
    ``get_memory()`` calls are filtered; explicit mark/exclude_mark callers keep
    their requested behavior.
    """
    original_get_memory = memory.get_memory

    async def filtered_get_memory(*args: Any, **kwargs: Any) -> Any:
        # If no mark/exclude_mark specified via keyword args, add our filter.
        # We intentionally only inspect kwargs to avoid fragile positional-arg
        # guessing that could conflict with positional None values.
        if (
            "mark" not in kwargs
            and "exclude_mark" not in kwargs
            and len(args) < 2
        ):
            kwargs["exclude_mark"] = _INTERNAL_STATUS_MEMORY_MARK
        return await original_get_memory(*args, **kwargs)

    memory.get_memory = filtered_get_memory
    return original_get_memory


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
        self.out_type_converters = {
            "hubos_status": _hubos_status_stream_converter,
        }
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
        # the previous session don't leak into the new one. Keep using
        # the ReMe-compatible memory when the memory manager is enabled;
        # the plain AgentScope InMemoryMemory lacks HubOS/ReMe compaction
        # methods such as mark_messages_compressed(), which causes repeated
        # compaction loops.
        if self.memory_manager is not None:
            memory = self.memory_manager.get_in_memory_memory()
        else:
            from agentscope.memory import InMemoryMemory

            memory = InMemoryMemory()

        agent.memory = memory
        if getattr(agent, "command_handler", None) is not None:
            agent.command_handler.memory = memory

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
                        approved_tool_call[
                            "_thinking_blocks"
                        ] = thinking_blocks
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
        pre_agent_status_msgs: list[Msg] = []
        experience_match_card_id = ""
        experience_match_task_type = ""
        experience_match_explicit = False
        try:
            session_id = request.session_id
            user_id = request.user_id
            channel = getattr(request, "channel", DEFAULT_CHANNEL)

            # Set session ID in context so downstream modules (tool output
            # archival, etc.) can resolve it without parameter threading.
            # ContextVars are async-task-local, so concurrent requests do
            # not interfere with each other.
            from ...config.context import set_current_session_id

            set_current_session_id(session_id)
            workspace_path = (
                self.workspace_dir if self.workspace_dir else WORKING_DIR
            )
            skip_session_state = bool(
                getattr(request, "skip_session_state", False),
            )
            skip_chat_registration = bool(
                getattr(request, "skip_chat_registration", False),
            )

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

            # --- Phase 1: Context Understanding ---
            ctx_call_id = f"status-{uuid.uuid4().hex[:8]}"
            _ctx_start = _make_internal_status_msg(
                status_id=ctx_call_id,
                label="Context understanding",
                state="in_progress",
            )
            yield _ctx_start, True
            pre_agent_status_msgs.append(_ctx_start)

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
            env_context = (
                f"{env_context}\n\n"
                "Internal runner note:\n"
                "- Context understanding, Experience matching, and "
                "Knowledge injection are internal pre-execution stages "
                "already handled by the Runner.\n"
                "- Do not call them as tools, do not emit function calls "
                "for them, and do not repeat them after your answer.\n"
            )

            # --- Phase 2: RunPolicy (depth + mode) ---
            from ..run_policy import (
                classify_run_depth,
                knowledge_budget_for,
            )

            run_depth = classify_run_depth(query or "")
            ki_budget = max(300, knowledge_budget_for(run_depth, query or ""))

            ctx_summary = f"depth={run_depth}"

            _ctx_done = _make_internal_status_msg(
                status_id=ctx_call_id,
                label="Context understanding",
                state="completed",
                output=ctx_summary,
            )
            yield _ctx_done, True
            pre_agent_status_msgs.append(_ctx_done)

            # --- Phase 3: Experience matching (if WE enabled + not light) ---
            matched_card = None
            try:
                from ...core.infra.feature_flags import get_feature_flags

                flags = get_feature_flags()
                if flags.use_work_experience():
                    exp_call_id = f"status-{uuid.uuid4().hex[:8]}"
                    _exp_start = _make_internal_status_msg(
                        status_id=exp_call_id,
                        label="Experience matching",
                        state="in_progress",
                    )
                    yield _exp_start, True
                    pre_agent_status_msgs.append(_exp_start)

                    from ...core.work_experience.integration_v4 import (
                        get_work_experience_interceptor,
                    )

                    interceptor = get_work_experience_interceptor()
                    exp_result = interceptor.pre_execute(
                        user_message=query or "",
                        session_id=session_id,
                    )
                    matched_card = exp_result.card
                    experience_match_card_id = (
                        exp_result.card.card_id if exp_result.card else ""
                    )
                    experience_match_task_type = exp_result.task_type or ""
                    experience_match_explicit = True

                    # Build status summary from real result
                    exp_status = exp_result.status
                    exp_task = exp_result.task_type
                    exp_ms = exp_result.elapsed_ms
                    if exp_status == "matched" and exp_task:
                        exp_summary = f"matched: {exp_task} · {exp_ms}ms"
                    elif exp_status == "no_match":
                        exp_summary = f"no matching card · {exp_ms}ms"
                    elif exp_status == "model_unavailable":
                        exp_summary = f"model unavailable · {exp_ms}ms"
                    elif exp_status == "invalid_output":
                        exp_summary = f"invalid model output · {exp_ms}ms"
                    else:
                        exp_summary = f"model call failed · {exp_ms}ms"

                    _exp_done = _make_internal_status_msg(
                        status_id=exp_call_id,
                        label="Experience matching",
                        state="completed",
                        output=exp_summary,
                    )
                    yield _exp_done, True
                    pre_agent_status_msgs.append(_exp_done)
            except Exception:
                logger.warning(
                    "WorkExperience v4 retrieval/injection failed; "
                    "continuing without guidance",
                    exc_info=True,
                )

            # --- Phase 4: Knowledge injection ---
            try:
                from ...core.knowledge_injection import (
                    KnowledgeInjectionConfig,
                    build_relevant_guidance,
                )

                ws_dir = (
                    self.workspace_dir if self.workspace_dir else WORKING_DIR
                )

                ki_call_id = f"status-{uuid.uuid4().hex[:8]}"
                _ki_start = _make_internal_status_msg(
                    status_id=ki_call_id,
                    label="Knowledge injection",
                    state="in_progress",
                )
                yield _ki_start, True
                pre_agent_status_msgs.append(_ki_start)

                ki_config = KnowledgeInjectionConfig(
                    default_max_tokens=ki_budget,
                    complex_max_tokens=ki_budget,
                    explicit_max_tokens=1000,
                )
                guidance_text, guidance_meta = build_relevant_guidance(
                    user_message=query or "",
                    experience_card=matched_card,
                    workspace_dir=ws_dir,
                    config=ki_config,
                )
                if guidance_text:
                    env_context = f"{env_context}\n\n---\n{guidance_text}\n---"

                # Build human-readable summary
                ic = guidance_meta.get("item_count", 0)
                if ic > 0:
                    et = guidance_meta.get("estimated_tokens", 0)
                    bt = guidance_meta.get("budget_tokens", 0)
                    sc = guidance_meta.get("sources", {})
                    src_parts = ", ".join(f"{k} {v}" for k, v in sc.items())
                    ki_summary = (
                        f"{ic} items · ~{et} tokens · budget {bt}"
                        f" · sources: {src_parts}"
                    )
                else:
                    ki_summary = "0 items · no relevant knowledge injected"

                _ki_done = _make_internal_status_msg(
                    status_id=ki_call_id,
                    label="Knowledge injection",
                    state="completed",
                    output=ki_summary,
                )
                yield _ki_done, True
                pre_agent_status_msgs.append(_ki_done)

                logger.info(
                    "Knowledge injection applied",
                    extra={
                        "session_id": session_id,
                        "agent_id": self.agent_id,
                        "depth": run_depth,
                        **guidance_meta,
                    },
                )
            except Exception:
                logger.warning(
                    "Knowledge injection failed, continuing without guidance",
                    exc_info=True,
                )

            # Get MCP clients from manager (hot-reloadable)
            mcp_clients = []
            if self._mcp_manager is not None:
                mcp_clients = await self._mcp_manager.get_clients()

            # Load agent-specific configuration
            agent_config = load_agent_config(self.agent_id)

            parent_session_id = getattr(
                request,
                "parent_session_id",
                "",
            )
            parent_workspace_dir = getattr(
                request,
                "parent_workspace_dir",
                "",
            )
            request_context = {
                "session_id": session_id,
                "user_id": user_id,
                "channel": channel,
                "agent_id": self.agent_id,
                "workspace_dir": str(workspace_path),
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
            if parent_session_id:
                request_context["parent_session_id"] = parent_session_id
                request_context["parent_workspace_dir"] = str(
                    parent_workspace_dir or workspace_path,
                )

            agent = await self._get_or_create_agent(
                agent_config=agent_config,
                env_context=env_context,
                mcp_clients=mcp_clients,
                request_context=request_context,
            )

            # Register recall_parent_context tool for sub-agents that
            # have a parent session to query.
            if parent_session_id:
                from ...core.parent_context import (
                    create_parent_context_tool,
                )

                ws_dir = parent_workspace_dir or workspace_path
                parent_ctx_tool = create_parent_context_tool(
                    parent_session_id=parent_session_id,
                    workspace_dir=str(ws_dir),
                )
                try:
                    agent.toolkit.register_tool_function(
                        parent_ctx_tool,
                        namesake_strategy="replace",
                    )
                    logger.info(
                        "Registered recall_parent_context for "
                        "parent_session=%s",
                        parent_session_id,
                    )
                except Exception:
                    logger.warning(
                        "Failed to register recall_parent_context tool",
                        exc_info=True,
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

            if skip_chat_registration:
                logger.debug(
                    "Skipping chat registration for session_id=%s channel=%s",
                    session_id,
                    channel,
                )
            elif self._chat_manager is not None:
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

            if skip_session_state:
                logger.debug(
                    "Skipping session state load for session_id=%s channel=%s",
                    session_id,
                    channel,
                )
            else:
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

            # Archive raw history before reducing the active model context.
            if hasattr(agent, "memory") and agent.memory is not None:
                if session_state_loaded:
                    try:
                        from ...core.memory.workspace_ledger import (
                            persist_memory_to_ledger,
                        )

                        await asyncio.to_thread(
                            persist_memory_to_ledger,
                            memory=agent.memory,
                            workspace_dir=workspace_path,
                            session_id=session_id,
                            user_id=user_id,
                            channel=channel,
                            agent_id=self.agent_id,
                            title=query or "",
                        )
                    except Exception:
                        logger.warning(
                            "Failed to persist pre-compaction memory ledger",
                            exc_info=True,
                        )
                    try:
                        from ...core.tool_output_archive import (
                            compact_completed_tool_inputs,
                        )

                        loaded_messages = await agent.memory.get_memory(
                            prepend_summary=False,
                        )
                        tool_compact = agent_config.running.tool_result_compact
                        if tool_compact.enabled:
                            try:
                                await self.memory_manager.compact_tool_result(
                                    messages=loaded_messages,
                                    recent_n=tool_compact.recent_n,
                                    old_max_bytes=tool_compact.old_max_bytes,
                                    recent_max_bytes=tool_compact.recent_max_bytes,
                                    retention_days=tool_compact.retention_days,
                                )
                            except Exception:
                                logger.warning(
                                    "Failed to archive loaded tool results",
                                    exc_info=True,
                                )
                        compact_completed_tool_inputs(
                            loaded_messages,
                            recent_n=max(6, tool_compact.recent_n * 3),
                            threshold=max(2_000, tool_compact.old_max_bytes),
                        )
                    except Exception:
                        logger.warning(
                            "Failed to archive loaded tool payloads",
                            exc_info=True,
                        )
                    await compact_stale_session_messages_locally(
                        agent.memory,
                        max_age_hours=2.0,
                        min_keep=10,
                    )
                prune_empty_assistant_messages(agent.memory)
                _mark_internal_status_messages(agent.memory)

            # Rebuild system prompt so it always reflects the latest
            # AGENTS.md / SOUL.md / PROFILE.md, not the stale one saved
            # in the session state.
            agent.rebuild_sys_prompt()

            # Inject pre-agent status messages into memory AFTER
            # load_session_state so they are not overwritten by the
            # restored session state.  They persist across page refresh
            # because the session state is saved again when the run ends.
            if pre_agent_status_msgs:
                await agent.memory.add(
                    pre_agent_status_msgs,
                    marks=_INTERNAL_STATUS_MEMORY_MARK,
                )

            original_get_memory = _install_internal_status_memory_filter(
                agent.memory,
            )
            try:
                async for msg, last in stream_printing_messages(
                    agents=[agent],
                    coroutine_task=agent(msgs),
                ):
                    (
                        clean_msg,
                        stripped,
                    ) = _strip_hallucinated_internal_status_blocks(msg)
                    if stripped:
                        logger.warning(
                            "Dropped hallucinated internal status block "
                            "from model stream: session_id=%s agent_id=%s",
                            session_id,
                            self.agent_id,
                        )
                    if clean_msg is None:
                        continue

                    if (
                        last
                        and getattr(clean_msg, "role", None) == "assistant"
                    ):
                        text = ""
                        try:
                            text = clean_msg.get_text_content() or ""
                        except Exception:
                            text = ""
                        if text.strip():
                            final_response_text = text
                    yield clean_msg, last
            finally:
                agent.memory.get_memory = original_get_memory

            if not final_response_text.strip():
                logger.warning(
                    "Agent returned empty response for session=%s "
                    "channel=%s — session may be too large or LLM "
                    "returned empty content",
                    session_id[:20],
                    channel,
                )
                fallback_msg = _make_empty_response_fallback_msg()
                final_response_text = _EMPTY_RESPONSE_FALLBACK_TEXT
                if hasattr(agent, "memory") and agent.memory is not None:
                    try:
                        await agent.memory.add(fallback_msg)
                    except Exception:
                        logger.warning(
                            "Failed to persist empty-response fallback",
                            exc_info=True,
                        )
                yield fallback_msg, True

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
            # -- WorkExperience v4 reflection (fire-and-forget) --
            # Previously this was a synchronous call that blocked the runner,
            # delaying RunControl "done" status by 3-10 seconds (LLM latency).
            # Now runs in background so the runner returns immediately.
            if agent is not None and final_response_text.strip():
                try:
                    from ...core.infra.feature_flags import get_feature_flags

                    if get_feature_flags().use_work_experience():
                        from ...core.work_experience.integration_v4 import (
                            get_work_experience_interceptor,
                        )

                        _wx_interceptor = get_work_experience_interceptor()
                        _wx_query = query or ""
                        _wx_response = final_response_text
                        _wx_session_id = session_id
                        _wx_channel = channel
                        _wx_agent_id = self.agent_id
                        _wx_workspace_dir = str(workspace_path)
                        _wx_match_card_id = experience_match_card_id
                        _wx_match_task_type = experience_match_task_type
                        _wx_match_explicit = experience_match_explicit
                        _wx_exec_ms = int(
                            (time.time() - chat_turn_started_at) * 1000,
                        )

                        def _wx_bg_task():
                            try:
                                _wx_interceptor.post_chat_turn(
                                    session_id=_wx_session_id,
                                    user_input=_wx_query,
                                    assistant_response=_wx_response,
                                    channel=_wx_channel,
                                    agent_id=_wx_agent_id,
                                    execution_time_ms=_wx_exec_ms,
                                    workspace_dir=_wx_workspace_dir,
                                    matched_card_id=_wx_match_card_id,
                                    matched_task_type=_wx_match_task_type,
                                    match_is_explicit=_wx_match_explicit,
                                )
                            except Exception:
                                logger.warning(
                                    "WorkExperience v4 background task failed",
                                    exc_info=True,
                                )

                        loop = asyncio.get_event_loop()
                        loop.run_in_executor(None, _wx_bg_task)
                except Exception:
                    logger.warning(
                        "Failed to dispatch WorkExperience v4 background task",
                        exc_info=True,
                    )

            if agent is not None and session_state_loaded:
                _strip_hallucinated_internal_status_from_memory(agent.memory)
                _mark_internal_status_messages(agent.memory)
                try:
                    from ...core.memory.workspace_ledger import (
                        persist_memory_to_ledger,
                    )

                    await asyncio.to_thread(
                        persist_memory_to_ledger,
                        memory=agent.memory,
                        workspace_dir=workspace_path,
                        session_id=session_id,
                        user_id=user_id,
                        channel=channel,
                        agent_id=self.agent_id,
                        title=query or "",
                    )
                except Exception:
                    logger.warning(
                        "Failed to persist final memory ledger",
                        exc_info=True,
                    )
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

            # Clear session_id ContextVar to prevent leakage across tasks
            try:
                from ...config.context import set_current_session_id

                set_current_session_id(None)
            except Exception:
                pass

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
