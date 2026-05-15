# -*- coding: utf-8 -*-
"""Factory for creating chat models and formatters.

This module provides a unified factory for creating chat model instances
and their corresponding formatters based on configuration.

Example:
    >>> from hubos.agents.model_factory import create_model_and_formatter
    >>> model, formatter = create_model_and_formatter()
"""


import base64
import logging
import os
from typing import List, Sequence, Tuple, Type, Any, Union, Optional
from urllib.parse import urlparse

# Token-optimization constants
_CACHE_MAX_CHECKPOINTS = (
    4  # Anthropic allows up to 4 ephemeral cache breakpoints
)
_TOOL_RESULT_MAX_CHARS = (
    3_000  # Truncate old tool-result text beyond this length
)
_TOOL_RESULT_KEEP_RECENT = (
    6  # Keep last N formatted messages intact (≈3 turns)
)

from agentscope.formatter import FormatterBase, OpenAIChatFormatter
from agentscope.model import ChatModelBase, OpenAIChatModel

try:
    from agentscope.formatter import AnthropicChatFormatter
    from agentscope.model import AnthropicChatModel
except ImportError:  # pragma: no cover - compatibility fallback
    AnthropicChatFormatter = None
    AnthropicChatModel = None

try:
    from agentscope.formatter import GeminiChatFormatter
    from agentscope.model import GeminiChatModel
except ImportError:  # pragma: no cover - compatibility fallback
    GeminiChatFormatter = None
    GeminiChatModel = None

from .utils.tool_message_utils import _sanitize_tool_messages
from ..providers import ProviderManager
from ..providers.retry_chat_model import (
    RetryChatModel,
    RetryConfig,
    RateLimitConfig,
)
from ..token_usage import TokenRecordingModelWrapper


def _file_url_to_path(url: str) -> str:
    """
    Strip file:// to path. On Windows file:///C:/path -> C:/path not /C:/path.
    """
    s = url.removeprefix("file://")
    # Windows: file:///C:/path yields "/C:/path"; remove leading slash.
    if len(s) >= 3 and s.startswith("/") and s[1].isalpha() and s[2] == ":":
        s = s[1:]
    return s


logger = logging.getLogger(__name__)

_SUPPORTED_IMAGE_EXTENSIONS: dict[str, str] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_SUPPORTED_VIDEO_EXTENSIONS: dict[str, str] = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mpeg": "video/mpeg",
    ".mov": "video/quicktime",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}


# TODO: remove after agentscope anthropic formatter updated
def _format_anthropic_media_block(block: dict) -> dict:
    """Format an image or video block for Anthropic API.

    If the source is a URLSource pointing to a local file it will be
    converted to base64.  Web URLs are passed through as-is.

    Args:
        block (`dict`):
            A block dict with ``type`` of ``"image"`` or ``"video"``.

    Returns:
        `dict`: Formatted block for the Anthropic API.

    Raises:
        `ValueError`:
            If the source type or media format is not supported.
    """
    typ = block["type"]
    extensions = (
        _SUPPORTED_IMAGE_EXTENSIONS
        if typ == "image"
        else _SUPPORTED_VIDEO_EXTENSIONS
    )

    source = block["source"]

    if source["type"] == "base64":
        return {**block}

    url = source["url"]
    raw_url = _file_url_to_path(url)

    if os.path.exists(raw_url) and os.path.isfile(raw_url):
        ext = os.path.splitext(raw_url)[1].lower()
        media_type = extensions.get(ext)
        if media_type:
            with open(raw_url, "rb") as f:
                data = base64.b64encode(f.read()).decode(
                    "utf-8",
                )
            return {
                "type": typ,
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": data,
                },
            }

    parsed_url = urlparse(raw_url)
    if parsed_url.scheme not in ("", "file"):
        return {
            "type": typ,
            "source": {
                "type": "url",
                "url": url,
            },
        }

    raise ValueError(
        f'Invalid {typ} URL: "{url}". '
        "It should be a local file or a web URL.",
    )


def _format_openai_video_block(video_block: dict) -> dict:
    """Format a video block for OpenAI-compatible API.

    Local files are converted to base64 data URLs; web URLs are
    passed through directly.

    Args:
        video_block (`dict`):
            The video block to format.

    Returns:
        `dict`:
            ``{"type": "video_url", "video_url": {"url": ...}}``.

    Raises:
        `ValueError`:
            If the source type or video format is not supported.
    """
    source = video_block["source"]
    if source["type"] == "base64":
        media_type = source["media_type"]
        url = f"data:{media_type};base64,{source['data']}"
    elif source["type"] == "url":
        raw_url = source["url"].removeprefix("file://")
        if os.path.exists(raw_url) and os.path.isfile(raw_url):
            ext = os.path.splitext(raw_url)[1].lower()
            media_type = _SUPPORTED_VIDEO_EXTENSIONS.get(ext)
            if not media_type:
                raise ValueError(
                    f"Unsupported video extension: {ext}",
                )
            with open(raw_url, "rb") as f:
                data = base64.b64encode(
                    f.read(),
                ).decode("utf-8")
            url = f"data:{media_type};base64,{data}"
        else:
            parsed = urlparse(raw_url)
            if parsed.scheme not in ("", "file"):
                url = source["url"]
            else:
                raise ValueError(
                    f"Invalid video URL: "
                    f'"{source["url"]}". '
                    "It should be a local file "
                    "or a web URL.",
                )
    else:
        raise ValueError(
            "Unsupported video source type: " f"{source['type']}",
        )

    return {
        "type": "video_url",
        "video_url": {"url": url},
    }


def _replace_video_placeholders(
    messages: list[dict],
    video_subs: dict[str, dict],
) -> None:
    """Replace video placeholder text blocks with formatted
    video blocks in OpenAI-formatted messages."""
    for fmt_msg in messages:
        content = fmt_msg.get("content")
        if not isinstance(content, list):
            continue
        new_content = []
        for item in content:
            if (
                isinstance(item, dict)
                and item.get("type") == "text"
                and item.get("text") in video_subs
            ):
                new_content.append(
                    _format_openai_video_block(
                        video_subs[item["text"]],
                    ),
                )
            else:
                new_content.append(item)
        fmt_msg["content"] = new_content


def _format_anthropic_output_items(output: list) -> list:
    """Format a list of tool_result output blocks for Anthropic API,
    converting image and video blocks as needed."""
    return [
        _format_anthropic_media_block(item)
        if item.get("type") in ("image", "video")
        else item
        for item in output
    ]


# TODO: remove after agentscope anthropic formatter updated
def _format_anthropic_messages(  # pylint: disable=too-many-branches
    msgs: list,
) -> list[dict]:
    """Format messages for Anthropic API with image/video block support.

    This replaces the default ``AnthropicChatFormatter._format`` so that
    ``_format_anthropic_media_block`` is applied to both top-level media
    blocks and media blocks nested inside ``tool_result`` outputs.
    """
    messages: list[dict] = []
    for index, msg in enumerate(msgs):
        content_blocks: list[dict] = []

        for block in msg.get_content_blocks():
            typ = block.get("type")
            if typ in ["thinking", "text"]:
                content_blocks.append({**block})

            elif typ in ("image", "video"):
                content_blocks.append(
                    _format_anthropic_media_block(block),
                )

            elif typ == "tool_use":
                content_blocks.append(
                    {
                        "id": block.get("id"),
                        "type": "tool_use",
                        "name": block.get("name"),
                        "input": block.get("input", {}),
                    },
                )

            elif typ == "tool_result":
                output = block.get("output")
                if output is None:
                    content_value: list = [
                        {"type": "text", "text": None},
                    ]
                elif isinstance(output, list):
                    content_value = _format_anthropic_output_items(output)
                else:
                    content_value = [
                        {"type": "text", "text": str(output)},
                    ]
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block.get("id"),
                                "content": content_value,
                            },
                        ],
                    },
                )

        if msg.role == "system" and index != 0:
            role = "user"
        else:
            role = msg.role

        msg_anthropic: dict = {
            "role": role,
            "content": content_blocks or None,
        }

        if msg_anthropic["content"] or msg_anthropic.get(
            "tool_calls",
        ):
            messages.append(msg_anthropic)

    return messages


# Mapping from chat model class to formatter class
_CHAT_MODEL_FORMATTER_MAP: dict[Type[ChatModelBase], Type[FormatterBase]] = {
    OpenAIChatModel: OpenAIChatFormatter,
}
if AnthropicChatModel is not None and AnthropicChatFormatter is not None:
    _CHAT_MODEL_FORMATTER_MAP[AnthropicChatModel] = AnthropicChatFormatter
if GeminiChatModel is not None and GeminiChatFormatter is not None:
    _CHAT_MODEL_FORMATTER_MAP[GeminiChatModel] = GeminiChatFormatter


def _get_formatter_for_chat_model(
    chat_model_class: Type[ChatModelBase],
) -> Type[FormatterBase]:
    """Get the appropriate formatter class for a chat model.

    Args:
        chat_model_class: The chat model class

    Returns:
        Corresponding formatter class, defaults to OpenAIChatFormatter
    """
    return _CHAT_MODEL_FORMATTER_MAP.get(
        chat_model_class,
        OpenAIChatFormatter,
    )


def _assistant_has_thinking_content(msg: Any) -> bool:
    """Return whether an AgentScope assistant message has thinking text."""
    if getattr(msg, "role", None) != "assistant":
        return False
    try:
        blocks = msg.get_content_blocks()
    except Exception:
        return False
    for block in blocks:
        if (
            isinstance(block, dict)
            and block.get("type") == "thinking"
            and block.get("thinking")
        ):
            return True
    return False


def _assistant_is_thinking_only(msg: Any) -> bool:
    """Return whether an assistant message contains only thinking blocks."""
    if getattr(msg, "role", None) != "assistant":
        return False
    content = getattr(msg, "content", None)
    return (
        isinstance(content, list)
        and bool(content)
        and all(
            isinstance(block, dict) and block.get("type") == "thinking"
            for block in content
        )
    )


def _extract_assistant_reasoning_candidates(msgs: list) -> list[str | None]:
    """Extract reasoning text from assistant messages in original order.

    Some OpenAI-compatible reasoning models, notably DeepSeek thinking mode,
    require the prior assistant ``reasoning_content`` to be passed back on
    follow-up requests. AgentScope stores that content as ``thinking`` blocks,
    while the upstream OpenAI formatter drops those blocks. We therefore keep a
    compact ordered list so the formatted OpenAI messages can be annotated after
    the parent formatter has produced them.
    """
    candidates: list[str | None] = []
    for msg in (m for m in msgs if getattr(m, "role", None) == "assistant"):
        if _assistant_is_thinking_only(msg):
            continue
        reasoning = None
        try:
            blocks = msg.get_content_blocks()
        except Exception:
            blocks = []
        for block in blocks:
            if (
                isinstance(block, dict)
                and block.get("type") == "thinking"
                and block.get("thinking")
            ):
                reasoning = block["thinking"]
                break
        candidates.append(reasoning)
    return candidates


def _map_reasoning_by_tool_call_id(msgs: list) -> dict[str, str]:
    """Map original tool_use ids to the thinking text on the same message."""
    result: dict[str, str] = {}
    for msg in (m for m in msgs if getattr(m, "role", None) == "assistant"):
        reasoning = None
        tool_ids: list[str] = []
        try:
            blocks = msg.get_content_blocks()
        except Exception:
            blocks = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "thinking"
                and block.get("thinking")
                and reasoning is None
            ):
                reasoning = block["thinking"]
            elif block.get("type") == "tool_use" and block.get("id"):
                tool_ids.append(str(block["id"]))
        if reasoning:
            for tool_id in tool_ids:
                result[tool_id] = reasoning
    return result


def _inject_reasoning_content_best_effort(
    msgs: list,
    messages: list[dict],
) -> None:
    """Attach reasoning_content to formatted assistant messages.

    The normal case is one-to-one alignment between original assistant messages
    and formatted assistant messages. In real sessions, sanitization and
    provider-specific formatting may drop some assistant/status messages. The
    previous all-or-nothing behavior skipped every reasoning value on mismatch,
    which breaks DeepSeek thinking mode. This helper keeps the exact path for
    aligned messages and falls back to a conservative ordered injection when
    counts diverge.
    """
    if not any(_assistant_has_thinking_content(msg) for msg in msgs):
        return

    candidates = _extract_assistant_reasoning_candidates(msgs)
    out_assistant = [
        message for message in messages if message.get("role") == "assistant"
    ]
    if not candidates or not out_assistant:
        return

    reasoning_by_tool_id = _map_reasoning_by_tool_call_id(msgs)
    if reasoning_by_tool_id:
        for out_msg in out_assistant:
            if out_msg.get("reasoning_content"):
                continue
            for tool_call in out_msg.get("tool_calls") or []:
                tool_id = tool_call.get("id")
                reasoning = reasoning_by_tool_id.get(tool_id)
                if reasoning:
                    out_msg["reasoning_content"] = reasoning
                    break

    if len(candidates) != len(out_assistant):
        logger.warning(
            "Assistant message count mismatch after formatting "
            "(%d expected survivors, %d actual). Applying best-effort "
            "reasoning_content injection.",
            len(candidates),
            len(out_assistant),
        )

    unresolved = [
        out_msg
        for out_msg in out_assistant
        if "reasoning_content" not in out_msg
    ]
    for out_msg, reasoning in zip(unresolved, candidates):
        if reasoning:
            out_msg["reasoning_content"] = reasoning

    # DeepSeek thinking mode rejects prior assistant tool-call messages when
    # the key is absent. Some HubOS status/tool messages are synthetic and have
    # no actual reasoning text, so use an empty string as an explicit marker.
    for out_msg in out_assistant:
        if out_msg.get("tool_calls") and "reasoning_content" not in out_msg:
            out_msg["reasoning_content"] = ""


def _substitute_video_blocks(
    msgs: list,
) -> dict[str, dict]:
    """Replace video blocks in msgs with text placeholders.

    Returns a mapping from placeholder text to the original video
    block so they can be restored later.
    """
    video_subs: dict[str, dict] = {}
    for msg in msgs:
        if not isinstance(msg.content, list):
            continue
        for i, blk in enumerate(msg.content):
            if isinstance(blk, dict) and blk.get("type") == "video":
                ph = f"__HUBOS_VID_{id(blk)}__"
                video_subs[ph] = blk
                msg.content[i] = {
                    "type": "text",
                    "text": ph,
                }
    return video_subs


def _restore_video_blocks(
    msgs: list,
    video_subs: dict[str, dict],
) -> None:
    """Restore original video blocks in msgs after formatting."""
    for msg in msgs:
        if not isinstance(msg.content, list):
            continue
        for i, blk in enumerate(msg.content):
            if (
                isinstance(blk, dict)
                and blk.get("type") == "text"
                and blk.get("text") in video_subs
            ):
                msg.content[i] = video_subs[blk["text"]]


def _promote_tool_result_videos(
    msgs: list,
    messages: list[dict],
) -> list[dict]:
    """Inject promoted video user messages after tool result messages.

    Mirrors the image promotion that agentscope's formatter does
    for ``promote_tool_result_images``, but for video blocks.
    """
    promotions: dict[str, tuple[str, list]] = {}
    for msg in msgs:
        for block in msg.get_content_blocks():
            if block.get("type") != "tool_result":
                continue
            output = block.get("output")
            if not isinstance(output, list):
                continue
            videos = [
                (
                    item.get("source", {}).get("url", ""),
                    item,
                )
                for item in output
                if isinstance(item, dict) and item.get("type") == "video"
            ]
            if videos:
                promotions[block.get("id")] = (
                    block.get("name", ""),
                    videos,
                )

    if not promotions:
        return messages

    new_messages: list[dict] = []
    for fmt_msg in messages:
        new_messages.append(fmt_msg)
        tcid = fmt_msg.get("tool_call_id")
        if tcid not in promotions:
            continue
        tool_name, videos = promotions[tcid]
        promoted: list[dict] = [
            {
                "type": "text",
                "text": "<system-info>The following are "
                "the video contents from the tool "
                f"result of '{tool_name}':",
            },
        ]
        for url, vid_block in videos:
            promoted.append(
                {
                    "type": "text",
                    "text": f"\n- The video from '{url}': ",
                },
            )
            promoted.append(
                _format_openai_video_block(vid_block),
            )
        promoted.append(
            {"type": "text", "text": "</system-info>"},
        )
        new_messages.append(
            {"role": "user", "content": promoted},
        )
    return new_messages


def _inject_prompt_cache_control(messages: list) -> None:
    """Add Anthropic ``cache_control`` breakpoints to formatted messages (in-place).

    Targets up to ``_CACHE_MAX_CHECKPOINTS`` positions:
    1. The system message (always) — stable across every request.
    2. Up to 3 most-recent stable messages (all except the very last
       message, which changes with each request).

    Skips blocks that already carry ``cache_control``.
    """
    ephemeral = {"type": "ephemeral"}
    placed = 0

    def _mark_last_block(msg: dict) -> bool:
        content = msg.get("content")
        if isinstance(content, list) and content:
            block = content[-1]
            if isinstance(block, dict) and "cache_control" not in block:
                block["cache_control"] = ephemeral
                return True
        return False

    # Checkpoint 1 — system message
    for msg in messages:
        if msg.get("role") == "system":
            if _mark_last_block(msg):
                placed += 1
            break

    if placed >= _CACHE_MAX_CHECKPOINTS:
        return

    # Checkpoints 2-4 — last 3 stable (non-system) messages, excluding the
    # last message which varies with every request.
    non_system = [m for m in messages if m.get("role") != "system"]
    stable = non_system[:-1] if len(non_system) > 1 else []
    for msg in stable[-(_CACHE_MAX_CHECKPOINTS - placed) :]:
        if placed >= _CACHE_MAX_CHECKPOINTS:
            break
        if _mark_last_block(msg):
            placed += 1


def _truncate_tool_results(
    messages: list,
    is_anthropic: bool,
) -> list:
    """Truncate large tool-result text in *old* messages.

    Old messages are all formatted messages except the last
    ``_TOOL_RESULT_KEEP_RECENT`` ones.  This preserves full fidelity for
    the current tool-call turn while reducing token consumption for results
    from earlier turns.

    When truncation occurs, the full output is archived to a refs file
    and replaced with a structured summary that helps subsequent turns
    continue from where the context left off.

    Args:
        messages: Formatted message list (Anthropic or OpenAI format).
        is_anthropic: True when messages are in Anthropic format.

    Returns:
        New list where old tool-result text is truncated.
    """
    if len(messages) <= _TOOL_RESULT_KEEP_RECENT:
        return messages

    from hubos.core.tool_output_archive import archive_tool_output

    old = messages[:-_TOOL_RESULT_KEEP_RECENT]
    recent = messages[-_TOOL_RESULT_KEEP_RECENT:]
    suffix = "…[truncated]"

    def _trunc(text: str, *, tool_name: str = "") -> str:
        if len(text) > _TOOL_RESULT_MAX_CHARS:
            # Try to archive full output and use structured summary
            archived = archive_tool_output(
                text,
                tool_name=tool_name,
            )
            if archived:
                return archived
            # Fallback to simple truncation
            return text[:_TOOL_RESULT_MAX_CHARS] + suffix
        return text

    def _truncate_anthropic(msg: dict) -> dict:
        content = msg.get("content")
        if not isinstance(content, list):
            return msg
        new_content = []
        changed = False
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            if block.get("type") == "tool_result":
                result_content = block.get("content")
                # Extract tool name from the preceding tool_use message
                tool_name = ""
                if isinstance(result_content, list):
                    new_rc = []
                    block_changed = False
                    for rb in result_content:
                        if isinstance(rb, dict) and rb.get("type") == "text":
                            orig = rb.get("text", "")
                            truncated = _trunc(orig, tool_name=tool_name)
                            if truncated != orig:
                                rb = {**rb, "text": truncated}
                                block_changed = True
                        new_rc.append(rb)
                    if block_changed:
                        block = {**block, "content": new_rc}
                        changed = True
            new_content.append(block)
        return {**msg, "content": new_content} if changed else msg

    def _truncate_openai(msg: dict) -> dict:
        if msg.get("role") == "tool":
            content = msg.get("content", "")
            tool_name = msg.get("name", "")
            if isinstance(content, str):
                truncated = _trunc(content, tool_name=tool_name)
                if truncated != content:
                    return {**msg, "content": truncated}
        return msg

    truncate_fn = _truncate_anthropic if is_anthropic else _truncate_openai
    return [truncate_fn(m) for m in old] + recent


# pylint: disable-next=too-many-statements
def _create_file_block_support_formatter(
    base_formatter_class: Type[FormatterBase],
) -> Type[FormatterBase]:
    """Create a formatter class with file block support.

    This factory function extends any Formatter class to support file blocks
    in tool results, which are not natively supported by AgentScope.

    Args:
        base_formatter_class: Base formatter class to extend

    Returns:
        Enhanced formatter class with file block support
    """

    class FileBlockSupportFormatter(base_formatter_class):
        """Formatter with file block support for tool results."""

        # pylint: disable=too-many-branches
        async def _format(self, msgs):
            """Override to sanitize tool messages, handle thinking blocks,
            and relay ``extra_content`` (Gemini thought_signature).

            This prevents OpenAI API errors from improperly paired
            tool messages, preserves reasoning_content from "thinking"
            blocks that the base formatter skips, and ensures
            ``extra_content`` on tool_use blocks (e.g. Gemini
            thought_signature) is carried through to the API request.
            """
            msgs = _sanitize_tool_messages(msgs)

            extra_contents: dict[str, Any] = {}
            for msg in msgs:
                if msg.role != "assistant":
                    continue
                for block in msg.get_content_blocks():
                    if (
                        block.get("type") == "tool_use"
                        and "extra_content" in block
                    ):
                        extra_contents[block["id"]] = block["extra_content"]

            # Convert file:// URLs to paths for all media blocks,
            # TODO: remove this after AgentScope updated
            for msg in msgs:
                for block in msg.get_content_blocks():
                    if block.get("type") in ("image", "audio", "video"):
                        source = block.get("source")
                        if (
                            isinstance(source, dict)
                            and source.get("type") == "url"
                            and isinstance(source.get("url"), str)
                            and source["url"].startswith("file://")
                        ):
                            source["url"] = _file_url_to_path(source["url"])

            # For Anthropic, fully override formatting to handle
            # media blocks (top-level & inside tool_result output).
            # TODO: remove after agentscope anthropic formatter updated
            if AnthropicChatFormatter is not None and issubclass(
                base_formatter_class,
                AnthropicChatFormatter,
            ):
                messages = _format_anthropic_messages(msgs)
                # Phase-1 token optimizations (Anthropic-specific)
                messages = _truncate_tool_results(messages, is_anthropic=True)
                _inject_prompt_cache_control(messages)
            else:
                # Gemini handles video natively; for others
                # (OpenAI) we inject it via placeholders.
                _needs_video = not (
                    GeminiChatFormatter is not None
                    and issubclass(
                        base_formatter_class,
                        GeminiChatFormatter,
                    )
                )
                video_subs: dict[str, dict] = {}
                if _needs_video:
                    video_subs = _substitute_video_blocks(
                        msgs,
                    )

                messages = await super()._format(msgs)

                if video_subs:
                    _replace_video_placeholders(
                        messages,
                        video_subs,
                    )
                    _restore_video_blocks(msgs, video_subs)

                if _needs_video and getattr(
                    self,
                    "promote_tool_result_images",
                    False,
                ):
                    messages = _promote_tool_result_videos(
                        msgs,
                        messages,
                    )

            if extra_contents:
                for message in messages:
                    for tc in message.get("tool_calls", []):
                        ec = extra_contents.get(tc.get("id"))
                        if ec:
                            tc["extra_content"] = ec

            _inject_reasoning_content_best_effort(msgs, messages)

            # Phase-1 token optimization: truncate old tool results
            # (OpenAI / Gemini path; Anthropic handles this above)
            is_anthropic_fmt = (
                AnthropicChatFormatter is not None
                and issubclass(
                    base_formatter_class,
                    AnthropicChatFormatter,
                )
            )
            if not is_anthropic_fmt:
                messages = _truncate_tool_results(messages, is_anthropic=False)

            return _strip_top_level_message_name(messages)

        @staticmethod
        def convert_tool_result_to_string(
            output: Union[str, List[dict]],
        ) -> tuple[str, Sequence[Tuple[str, dict]]]:
            """Extend parent class to support file blocks.

            Uses try-first strategy for compatibility with parent class.

            Args:
                output: Tool result output (string or list of blocks)

            Returns:
                Tuple of (text_representation, multimodal_data)
            """
            if isinstance(output, str):
                return output, []

            # Try parent class method first
            try:
                return base_formatter_class.convert_tool_result_to_string(
                    output,
                )
            except ValueError as e:
                if "Unsupported block type: file" not in str(e):
                    raise

                # Handle output containing file blocks
                textual_output = []
                multimodal_data = []

                for block in output:
                    if not isinstance(block, dict) or "type" not in block:
                        raise ValueError(
                            f"Invalid block: {block}, "
                            "expected a dict with 'type' key",
                        ) from e

                    if block["type"] == "file":
                        file_path = block.get("path", "") or block.get(
                            "url",
                            "",
                        )
                        file_name = block.get("name", file_path)

                        textual_output.append(
                            f"The returned file '{file_name}' "
                            f"can be found at: {file_path}",
                        )
                        multimodal_data.append((file_path, block))
                    else:
                        # Delegate other block types to parent class
                        (
                            text,
                            data,
                        ) = base_formatter_class.convert_tool_result_to_string(
                            [block],
                        )
                        textual_output.append(text)
                        multimodal_data.extend(data)

                if len(textual_output) == 0:
                    return "", multimodal_data
                elif len(textual_output) == 1:
                    return textual_output[0], multimodal_data
                else:
                    return (
                        "\n".join("- " + _ for _ in textual_output),
                        multimodal_data,
                    )

    FileBlockSupportFormatter.__name__ = (
        f"FileBlockSupport{base_formatter_class.__name__}"
    )
    return FileBlockSupportFormatter


def _strip_top_level_message_name(
    messages: list[dict],
) -> list[dict]:
    """Strip top-level `name` from OpenAI chat messages.

    Some strict OpenAI-compatible backends reject `messages[*].name`
    (especially for assistant/tool roles) and may return 500/400 on
    follow-up turns. Keep function/tool names unchanged.
    """
    for message in messages:
        message.pop("name", None)
    return messages


def create_model_and_formatter(
    agent_id: Optional[str] = None,
) -> Tuple[ChatModelBase, FormatterBase]:
    """Factory method to create model and formatter instances.

    This method handles both local and remote models, selecting the
    appropriate chat model class and formatter based on configuration.

    Args:
        agent_id: Optional agent ID to load agent-specific model config.
            If None, tries to get from context, then falls back to global.

    Returns:
        Tuple of (model_instance, formatter_instance)

    Example:
        >>> model, formatter = create_model_and_formatter()
    """
    from ..app.agent_context import get_current_agent_id
    from ..config.config import load_agent_config

    # Determine agent_id (parameter > context > None)
    if agent_id is None:
        try:
            agent_id = get_current_agent_id()
        except Exception:
            pass

    # Try to get agent-specific model first
    model_slot = None
    retry_config = None
    rate_limit_config = None

    # Check for runtime model override (e.g. from cron jobs)
    from ..config.context import get_current_model_override

    _override = get_current_model_override()

    if agent_id:
        try:
            agent_config = load_agent_config(agent_id)
            model_slot = agent_config.active_model
            retry_config = RetryConfig(
                enabled=agent_config.running.llm_retry_enabled,
                max_retries=agent_config.running.llm_max_retries,
                backoff_base=agent_config.running.llm_backoff_base,
                backoff_cap=agent_config.running.llm_backoff_cap,
            )
            rate_limit_config = RateLimitConfig(
                max_concurrent=agent_config.running.llm_max_concurrent,
                max_qpm=agent_config.running.llm_max_qpm,
                pause_seconds=agent_config.running.llm_rate_limit_pause,
                jitter_range=agent_config.running.llm_rate_limit_jitter,
                acquire_timeout=agent_config.running.llm_acquire_timeout,
            )
        except Exception:
            pass

    # Model override takes highest priority (e.g. cron wants DeepSeek)
    if (
        _override is not None
        and getattr(_override, "provider_id", None)
        and getattr(_override, "model", None)
    ):
        model_slot = _override

    # Create chat model from agent-specific or global config
    if model_slot and model_slot.provider_id and model_slot.model:
        # Use agent-specific model
        manager = ProviderManager.get_instance()
        provider = manager.get_provider(model_slot.provider_id)
        if provider is None:
            raise ValueError(
                f"Provider '{model_slot.provider_id}' not found.",
            )

        model = provider.get_chat_model_instance(model_slot.model)
        provider_id = model_slot.provider_id
    else:
        # Fallback to global active model
        model = ProviderManager.get_active_chat_model()
        global_model = ProviderManager.get_instance().get_active_model()
        if not global_model:
            raise ValueError(
                "No active model configured. "
                "Please configure a model using 'hubos models config' "
                "or set an agent-specific model.",
            )
        provider_id = global_model.provider_id

    # Create the formatter based on the real model class
    formatter = _create_formatter_instance(model.__class__)

    # Wrap with retry logic for transient LLM API errors
    wrapped_model = TokenRecordingModelWrapper(provider_id, model)
    wrapped_model = RetryChatModel(
        wrapped_model,
        retry_config=retry_config,
        rate_limit_config=rate_limit_config,
    )

    return wrapped_model, formatter


def _create_formatter_instance(
    chat_model_class: Type[ChatModelBase],
) -> FormatterBase:
    """Create a formatter instance for the given chat model class.

    The formatter is enhanced with file block support for handling
    file outputs in tool results.

    Args:
        chat_model_class: The chat model class

    Returns:
        Formatter instance with file block support
    """
    base_formatter_class = _get_formatter_for_chat_model(chat_model_class)
    formatter_class = _create_file_block_support_formatter(
        base_formatter_class,
    )
    kwargs: dict[str, Any] = {}
    if issubclass(
        base_formatter_class,
        (OpenAIChatFormatter, GeminiChatFormatter),
    ):
        kwargs["promote_tool_result_images"] = True
    return formatter_class(**kwargs)


def create_model_and_formatter_by_name(
    provider_id: str,
    model_name: str,
) -> Tuple[ChatModelBase, FormatterBase]:
    """Create model and formatter by explicit provider/model names.

    This bypasses agent config and directly creates a model instance
    from the specified provider. Useful for dedicated tasks like
    context compaction where a different (faster/cheaper) model is
    desired.

    Args:
        provider_id: Provider identifier (e.g. "minimax", "zhipuai")
        model_name: Model name (e.g. "MiniMax-M2.7-highspeed")

    Returns:
        Tuple of (model_instance, formatter_instance)

    Raises:
        ValueError: If provider not found
    """
    from ..providers.provider_manager import ProviderManager

    manager = ProviderManager.get_instance()
    provider = manager.get_provider(provider_id)
    if provider is None:
        raise ValueError(
            f"Provider '{provider_id}' not found. "
            "Available providers: "
            f"{list(manager._providers.keys()) if hasattr(manager, '_providers') else 'unknown'}",
        )

    model = provider.get_chat_model_instance(model_name)
    formatter = _create_formatter_instance(model.__class__)
    return model, formatter


__all__ = [
    "create_model_and_formatter",
    "create_model_and_formatter_by_name",
]
