# -*- coding: utf-8 -*-
"""Safe JSON session with filename sanitization for cross-platform
compatibility.

Windows filenames cannot contain: \\ / : * ? " < > |
This module wraps agentscope's SessionBase so that session_id and user_id
are sanitized before being used as filenames.
"""
import os
import re
import json
import logging
from datetime import datetime
from typing import Union, Sequence

import aiofiles
from agentscope.session import SessionBase

logger = logging.getLogger(__name__)

_TIME_COMPACT_CHUNK_CHARS = 30_000


# Characters forbidden in Windows filenames
_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|]')


def _message_has_block_type(msg, block_type: str) -> bool:
    """Return whether an AgentScope message contains a block of *block_type*."""
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return False
    return any(
        isinstance(block, dict) and block.get("type") == block_type
        for block in content
    )


def _is_protected_system_message(msg) -> bool:
    """Keep true system prompts, but allow old tool results to be pruned.

    AgentScope stores tool results as ``role=system`` messages. Treating every
    system-role message as permanent made large channel sessions accumulate
    stale tool outputs forever, which can poison provider formatting and lead
    to empty assistant replies.
    """
    if getattr(msg, "role", None) != "system":
        return False
    return not _message_has_block_type(msg, "tool_result")


def _is_empty_assistant_message(msg) -> bool:
    """Return True for assistant messages that carry no usable content."""
    if getattr(msg, "role", None) != "assistant":
        return False
    content = getattr(msg, "content", None)
    if not isinstance(content, list) or not content:
        return False

    for block in content:
        if not isinstance(block, dict):
            return False
        block_type = block.get("type")
        if block_type == "text" and str(block.get("text") or "").strip():
            return False
        if (
            block_type == "thinking"
            and str(block.get("thinking") or "").strip()
        ):
            return False
        if block_type not in {"text", "thinking"}:
            return False
    return True


def prune_empty_assistant_messages(memory) -> int:
    """Remove useless assistant messages that only contain empty text/thinking."""
    if not hasattr(memory, "content"):
        return 0

    next_content = []
    pruned = 0
    for item in list(memory.content):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            next_content.append(item)
            continue
        msg, _marks = item
        if _is_empty_assistant_message(msg):
            pruned += 1
            continue
        next_content.append(item)

    if pruned:
        memory.content = next_content
        logger.info("Pruned %d empty assistant message(s)", pruned)
    return pruned


def sanitize_filename(name: str) -> str:
    """Replace characters that are illegal in Windows filenames with ``--``.

    >>> sanitize_filename('discord:dm:12345')
    'discord--dm--12345'
    >>> sanitize_filename('normal-name')
    'normal-name'
    """
    return _UNSAFE_FILENAME_RE.sub("--", name)


def prune_stale_session_messages(
    memory,
    max_age_hours: float = 2.0,
    min_keep: int = 10,
) -> int:
    """Remove messages older than *max_age_hours* from an InMemoryMemory object.

    Rules:
    - True system prompt messages are **always** kept.
    - Tool-result messages are pruned by age even if AgentScope stores them
      with ``role=system``.
    - The most-recent *min_keep* non-system messages are **always** kept.
    - For older messages, those whose timestamp precedes the cutoff are dropped.

    Args:
        memory: An ``InMemoryMemory`` instance (must have a ``content`` attribute).
        max_age_hours: Age threshold in hours.  Messages older than this are pruned.
        min_keep: Minimum number of non-system messages to retain regardless of age.

    Returns:
        The number of messages that were pruned.
    """
    if not hasattr(memory, "content"):
        return 0

    content = list(memory.content)
    non_system_indices = [
        i
        for i, (msg, _) in enumerate(content)
        if not _is_protected_system_message(msg)
    ]

    if len(non_system_indices) <= min_keep:
        return 0

    cutoff_ts = datetime.now().timestamp() - max_age_hours * 3600
    # Indices of the last min_keep non-system messages — always kept
    always_keep = set(non_system_indices[-min_keep:])

    new_content = []
    pruned = 0
    for i, (msg, marks) in enumerate(content):
        if _is_protected_system_message(msg) or i in always_keep:
            new_content.append((msg, marks))
            continue

        ts_str = getattr(msg, "timestamp", None)
        if ts_str:
            try:
                ts = datetime.fromisoformat(
                    str(ts_str).replace("Z", "+00:00"),
                ).timestamp()
                if ts < cutoff_ts:
                    pruned += 1
                    continue
            except ValueError:
                pass  # Unparseable timestamp — keep the message

        new_content.append((msg, marks))

    if pruned:
        memory.content = new_content
        logger.info(
            "Pruned %d stale session message(s) older than %.1fh",
            pruned,
            max_age_hours,
        )

    return pruned


def _find_stale_session_messages(
    memory,
    *,
    max_age_hours: float,
    min_keep: int,
) -> list:
    """Return stale messages while preserving recent tool call pairs."""
    if not hasattr(memory, "content"):
        return []

    content = list(memory.content)
    candidate_indices = [
        i
        for i, item in enumerate(content)
        if isinstance(item, (list, tuple))
        and len(item) >= 1
        and not _is_protected_system_message(item[0])
    ]
    if len(candidate_indices) <= min_keep:
        return []

    always_keep = set(candidate_indices[-min_keep:])
    tool_use_indices: dict[str, int] = {}
    tool_result_indices: dict[str, int] = {}
    for index, item in enumerate(content):
        if not isinstance(item, (list, tuple)) or not item:
            continue
        blocks = getattr(item[0], "content", None)
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            call_id = block.get("id") or block.get("tool_use_id")
            if not call_id:
                continue
            if block.get("type") == "tool_use":
                tool_use_indices[str(call_id)] = index
            elif block.get("type") == "tool_result":
                tool_result_indices[str(call_id)] = index
    pairs = []
    for call_id in set(tool_use_indices) | set(tool_result_indices):
        pair = {
            tool_use_indices.get(call_id),
            tool_result_indices.get(call_id),
        }
        pair.discard(None)
        pairs.append(pair)
    changed = True
    while changed:
        changed = False
        for pair in pairs:
            if pair & always_keep and not pair <= always_keep:
                always_keep.update(pair)
                changed = True

    cutoff_ts = datetime.now().timestamp() - max_age_hours * 3600
    stale_messages = []
    for index, item in enumerate(content):
        if (
            index in always_keep
            or not isinstance(item, (list, tuple))
            or not item
        ):
            continue
        msg = item[0]
        if _is_protected_system_message(msg):
            continue
        timestamp = getattr(msg, "timestamp", None)
        if not timestamp:
            continue
        try:
            msg_ts = datetime.fromisoformat(
                str(timestamp).replace("Z", "+00:00"),
            ).timestamp()
        except ValueError:
            continue
        if msg_ts < cutoff_ts:
            stale_messages.append(msg)

    return stale_messages


async def compact_stale_session_messages_locally(
    memory,
    *,
    max_age_hours: float = 2.0,
    min_keep: int = 10,
) -> int:
    """Archive stale messages using a bounded local digest, without an LLM."""
    mark_compressed = getattr(memory, "mark_messages_compressed", None)
    update_summary = getattr(memory, "update_compressed_summary", None)
    if not callable(mark_compressed) or not callable(update_summary):
        logger.warning(
            "Local time compaction skipped: memory backend %s lacks archive support",
            memory.__class__.__name__,
        )
        return 0
    stale_messages = _find_stale_session_messages(
        memory,
        max_age_hours=max_age_hours,
        min_keep=min_keep,
    )
    if not stale_messages:
        return 0

    from ...core.memory.session_migration import build_extractive_summary

    get_summary = getattr(memory, "get_compressed_summary", None)
    previous_summary = get_summary() if callable(get_summary) else ""
    compact_content = build_extractive_summary(
        stale_messages,
        previous_summary=previous_summary or "",
    )
    archived = await mark_compressed(stale_messages)
    await update_summary(compact_content)
    logger.info(
        "Locally archived and compacted %d stale session message(s) older "
        "than %.1fh without an LLM call",
        archived,
        max_age_hours,
    )
    return int(archived or 0)


async def compact_stale_session_messages(
    memory,
    memory_manager,
    max_age_hours: float = 2.0,
    min_keep: int = 10,
) -> int:
    """Summarize and archive stale messages using the configured model."""
    mark_compressed = getattr(memory, "mark_messages_compressed", None)
    update_summary = getattr(memory, "update_compressed_summary", None)
    if not callable(mark_compressed) or not callable(update_summary):
        logger.warning(
            "Time compaction skipped: memory backend %s lacks archive support",
            memory.__class__.__name__,
        )
        return 0
    stale_messages = _find_stale_session_messages(
        memory,
        max_age_hours=max_age_hours,
        min_keep=min_keep,
    )
    if not stale_messages:
        return 0

    chunks: list[list] = []
    current_chunk: list = []
    current_chars = 0
    for msg in stale_messages:
        try:
            to_dict = getattr(msg, "to_dict", None)
            payload = (
                to_dict() if callable(to_dict) else getattr(msg, "content", "")
            )
            msg_chars = len(
                json.dumps(payload, ensure_ascii=False, default=str),
            )
        except Exception:
            msg_chars = len(str(getattr(msg, "content", "")))
        if (
            current_chunk
            and current_chars + msg_chars > _TIME_COMPACT_CHUNK_CHARS
        ):
            chunks.append(current_chunk)
            current_chunk = []
            current_chars = 0
        current_chunk.append(msg)
        current_chars += msg_chars
    if current_chunk:
        chunks.append(current_chunk)

    get_summary = getattr(memory, "get_compressed_summary", None)
    previous_summary = get_summary() if callable(get_summary) else ""
    archived = 0
    for chunk in chunks:
        try:
            compact_content = await memory_manager.compact_memory(
                messages=chunk,
                previous_summary=previous_summary or "",
            )
        except Exception:
            logger.warning(
                "Time-based memory compaction failed",
                exc_info=True,
            )
            break
        if not compact_content:
            logger.warning(
                "Time-based memory compaction returned an empty summary",
            )
            break
        try:
            chunk_archived = await mark_compressed(chunk)
            await update_summary(compact_content)
        except Exception:
            logger.warning(
                "Failed to archive time-compacted messages",
                exc_info=True,
            )
            break
        previous_summary = compact_content
        archived += int(chunk_archived or 0)

    logger.info(
        "Archived and compacted %d stale session message(s) older than %.1fh",
        archived,
        max_age_hours,
    )
    return archived


class SafeJSONSession(SessionBase):
    """SessionBase subclass with filename sanitization and async file I/O.

    Overrides all file-reading/writing methods to use :mod:`aiofiles` so
    that disk I/O does not block the event loop.
    """

    def __init__(
        self,
        save_dir: str = "./",
    ) -> None:
        """Initialize the JSON session class.

        Args:
            save_dir (`str`, defaults to `"./"):
                The directory to save the session state.
        """
        self.save_dir = save_dir

    def _get_save_path(self, session_id: str, user_id: str) -> str:
        """Return a filesystem-safe save path.

        Overrides the parent implementation to ensure the generated
        filename is valid on Windows, macOS and Linux.
        """
        os.makedirs(self.save_dir, exist_ok=True)
        safe_sid = sanitize_filename(session_id)
        safe_uid = sanitize_filename(user_id) if user_id else ""
        if safe_uid:
            file_path = f"{safe_uid}_{safe_sid}.json"
        else:
            file_path = f"{safe_sid}.json"
        return os.path.join(self.save_dir, file_path)

    async def save_session_state(
        self,
        session_id: str,
        user_id: str = "",
        **state_modules_mapping,
    ) -> None:
        """Save state modules to a JSON file using async I/O."""
        state_dicts = {
            name: state_module.state_dict()
            for name, state_module in state_modules_mapping.items()
        }
        session_save_path = self._get_save_path(session_id, user_id=user_id)
        with open(
            session_save_path,
            "w",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(state_dicts, ensure_ascii=False))

        logger.info(
            "Saved session state to %s successfully.",
            session_save_path,
        )

    async def load_session_state(
        self,
        session_id: str,
        user_id: str = "",
        allow_not_exist: bool = True,
        **state_modules_mapping,
    ) -> None:
        """Load state modules from a JSON file using async I/O."""
        session_save_path = self._get_save_path(session_id, user_id=user_id)
        if os.path.exists(session_save_path):
            async with aiofiles.open(
                session_save_path,
                "r",
                encoding="utf-8",
                errors="surrogatepass",
            ) as f:
                content = await f.read()
                states = json.loads(content)

            for name, state_module in state_modules_mapping.items():
                if name in states:
                    state_module.load_state_dict(states[name])
            logger.info(
                "Load session state from %s successfully.",
                session_save_path,
            )

        elif allow_not_exist:
            logger.info(
                "Session file %s does not exist. Skip loading session state.",
                session_save_path,
            )

        else:
            raise ValueError(
                f"Failed to load session state for file {session_save_path} "
                "because it does not exist.",
            )

    async def update_session_state(
        self,
        session_id: str,
        key: Union[str, Sequence[str]],
        value,
        user_id: str = "",
        create_if_not_exist: bool = True,
    ) -> None:
        session_save_path = self._get_save_path(session_id, user_id=user_id)

        if os.path.exists(session_save_path):
            async with aiofiles.open(
                session_save_path,
                "r",
                encoding="utf-8",
                errors="surrogatepass",
            ) as f:
                content = await f.read()
                states = json.loads(content)

        else:
            if not create_if_not_exist:
                raise ValueError(
                    f"Session file {session_save_path} does not exist.",
                )
            states = {}

        path = key.split(".") if isinstance(key, str) else list(key)
        if not path:
            raise ValueError("key path is empty")

        cur = states
        for k in path[:-1]:
            if k not in cur or not isinstance(cur[k], dict):
                cur[k] = {}
            cur = cur[k]

        cur[path[-1]] = value

        with open(
            session_save_path,
            "w",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(states, ensure_ascii=False))

        logger.info(
            "Updated session state key '%s' in %s successfully.",
            key,
            session_save_path,
        )

    async def get_session_state_dict(
        self,
        session_id: str,
        user_id: str = "",
        allow_not_exist: bool = True,
    ) -> dict:
        """Return the session state dict from the JSON file.

        Args:
            session_id (`str`):
                The session id.
            user_id (`str`, default to `""`):
                The user ID for the storage.
            allow_not_exist (`bool`, defaults to `True`):
                Whether to allow the session to not exist. If `False`, raises
                an error if the session does not exist.

        Returns:
            `dict`:
                The session state dict loaded from the JSON file. Returns an
                empty dict if the file does not exist and
                `allow_not_exist=True`.
        """
        session_save_path = self._get_save_path(session_id, user_id=user_id)
        if os.path.exists(session_save_path):
            async with aiofiles.open(
                session_save_path,
                "r",
                encoding="utf-8",
                errors="surrogatepass",
            ) as file:
                content = await file.read()
                states = json.loads(content)

            logger.info(
                "Get session state dict from %s successfully.",
                session_save_path,
            )
            return states

        if allow_not_exist:
            logger.info(
                "Session file %s does not exist. Return empty state dict.",
                session_save_path,
            )
            return {}

        raise ValueError(
            f"Failed to get session state for file {session_save_path} "
            "because it does not exist.",
        )
