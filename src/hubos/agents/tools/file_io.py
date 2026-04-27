# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=line-too-long
import os
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from agentscope.message import TextBlock
from agentscope.tool import ToolResponse

from .utils import (
    truncate_text_output,
    read_file_safe,
    DEFAULT_MAX_BYTES,
)
from ...config.context import (
    get_current_workspace_dir,
    get_current_recent_max_bytes,
    get_current_subagent_write_scope,
)
from ...constant import WORKING_DIR, TRUNCATION_NOTICE_MARKER


class SubAgentWriteDenied(Exception):
    """Raised internally when a sub-agent tries to write outside its scope."""


def _download_link(abs_path: str) -> str:
    """Return a markdown ``[name](/api/files/preview/...)`` link.

    The frontend chat UI renders markdown, so any tool-response text the GM
    forwards verbatim to the user will expose this link as clickable. The
    link points at the existing auth-protected ``/api/files/preview`` route
    (see ``hubos.app.routers.files.preview_file``). Empty string if the path
    doesn't resolve to a real file.
    """
    try:
        p = Path(abs_path).resolve()
    except Exception:  # noqa: BLE001
        return ""
    if not p.is_file():
        return ""
    # preview route expects a path without leading slash
    rel = str(p).lstrip("/")
    href = f"/api/files/preview/{quote(rel, safe='/')}"
    return f"[📄 {p.name}]({href})"


def _resolve_file_path(file_path: str) -> str:
    """Resolve file path: use absolute path as-is,
    resolve relative path from current workspace or WORKING_DIR.

    Args:
        file_path: The input file path (absolute or relative).

    Returns:
        The resolved absolute file path as string.
    """
    path = Path(file_path).expanduser()
    if path.is_absolute():
        return str(path)
    else:
        # Use current workspace_dir from context, fallback to WORKING_DIR
        workspace_dir = get_current_workspace_dir() or WORKING_DIR
        return str(workspace_dir / file_path)


def _resolve_write_path(file_path: str) -> str:
    """Same as ``_resolve_file_path`` but enforces sub-agent write scope.

    When a sub-agent is active (see
    ``config.context.current_subagent_write_scope``), writes are allowed
    anywhere under the agent's **own** workspace root, except for protected
    identity files (AGENTS.md, PROFILE.md, SOUL.md, agent.json, etc.).
    Returns the resolved absolute path; raises :class:`SubAgentWriteDenied`
    if the request would escape the scope or targets a protected file.
    """
    scope = get_current_subagent_write_scope()
    if scope is None:
        return _resolve_file_path(file_path)

    scope = Path(scope).expanduser().resolve()

    raw = Path(file_path).expanduser()
    if raw.is_absolute():
        candidate = raw.resolve()
    else:
        candidate = (scope / raw).resolve()

    # Check 1: must stay within the agent's own workspace
    try:
        candidate.relative_to(scope)
    except ValueError:
        raise SubAgentWriteDenied(
            f"sub-agent write denied: {file_path!r} resolves outside the "
            f"allowed scope {scope!s}. Sub-agents can only write within "
            f"their own workspace.",
        )

    # Check 2: protect identity/config files from being overwritten
    _PROTECTED_FILES = frozenset(
        {
            "AGENTS.md",
            "SOUL.md",
            "PROFILE.md",
            "BOOTSTRAP.md",
            "agent.json",
            "skill.json",
            "skills.json",
        },
    )
    if candidate.name in _PROTECTED_FILES:
        raise SubAgentWriteDenied(
            f"sub-agent write denied: {candidate.name!r} is a protected "
            f"identity file. Only the GM or a human can modify it.",
        )

    # Ensure parent directory exists
    candidate.parent.mkdir(parents=True, exist_ok=True)

    return str(candidate)


def _get_encoding_for_file(file_path: str) -> str:
    """Determine the appropriate encoding for a file based on its type.

    For cross-platform compatibility, especially with Windows Excel/Notepad:
    - CSV/TSV/TXT files: Use UTF-8-BOM (Windows Excel needs BOM to detect UTF-8)
    - All other files: Use UTF-8 (safer default, no BOM)

    Args:
        file_path: Path to the file

    Returns:
        Encoding string: "utf-8-sig" or "utf-8"
    """
    suffix = Path(file_path).suffix.lower()

    # Files that need BOM for Windows compatibility
    if suffix in {".csv", ".tsv", ".tab", ".txt", ".log"}:
        return "utf-8-sig"

    # Default: UTF-8 without BOM (safe for all other files)
    # This includes: .sh, .yaml, .json, .py, .js, .md, etc.
    return "utf-8"


async def read_file(  # pylint: disable=too-many-return-statements
    file_path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> ToolResponse:
    """Read a file. Relative paths resolve from WORKING_DIR.

    Use start_line/end_line to read a specific line range (output includes
    line numbers). Omit both to read the full file.

    Args:
        file_path (`str`):
            Path to the file.
        start_line (`int`, optional):
            First line to read (1-based, inclusive).
        end_line (`int`, optional):
            Last line to read (1-based, inclusive).
    """

    # Convert start_line/end_line to int if they are strings
    if start_line is not None:
        try:
            start_line = int(start_line)
        except (ValueError, TypeError):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: start_line must be an integer, got {start_line!r}.",
                    ),
                ],
            )

    if end_line is not None:
        try:
            end_line = int(end_line)
        except (ValueError, TypeError):
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: end_line must be an integer, got {end_line!r}.",
                    ),
                ],
            )

    file_path = _resolve_file_path(file_path)

    if not os.path.exists(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The file {file_path} does not exist.",
                ),
            ],
        )

    if not os.path.isfile(file_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The path {file_path} is not a file.",
                ),
            ],
        )

    try:
        content = read_file_safe(file_path)
        all_lines = content.split("\n")
        total = len(all_lines)

        # Determine read range
        s = max(1, start_line if start_line is not None else 1)
        e = min(total, end_line if end_line is not None else total)

        if s > total:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: start_line {s} exceeds file length ({total} lines).",
                    ),
                ],
            )

        if s > e:
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error: start_line ({s}) > end_line ({e}).",
                    ),
                ],
            )

        # Extract selected lines
        selected_content = "\n".join(all_lines[s - 1 : e])

        # Apply smart truncation (consistent with shell output format)
        max_bytes = get_current_recent_max_bytes() or DEFAULT_MAX_BYTES
        text = truncate_text_output(
            selected_content,
            start_line=s,
            total_lines=total,
            file_path=file_path,
            max_bytes=max_bytes,
        )

        # Add continuation hint if partial read without truncation.
        # Use TRUNCATION_NOTICE_MARKER format so ToolResultCompactor can
        # re-truncate with the correct start_line when compacting old messages.
        if text == selected_content and e < total:
            content_bytes = len(text.encode("utf-8"))
            notice = (
                TRUNCATION_NOTICE_MARKER + f"\nThe output above was truncated."
                f"\nThe full content is saved to the file "
                f"and contains {total} lines in total."
                f"\nThis excerpt starts at line {s} and "
                f"covers the next {content_bytes} bytes."
                "\nIf the current content is not enough, "
                f"call `read_file` with file_path={file_path} "
                f"start_line={e + 1} to read more."
            )
            text = text + notice

        return ToolResponse(
            content=[TextBlock(type="text", text=text)],
        )

    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Read file failed due to \n{e}",
                ),
            ],
        )


async def write_file(
    file_path: str,
    content: str,
) -> ToolResponse:
    """Create or overwrite a file. Relative paths resolve from WORKING_DIR.

    The tool's response includes a ``Download: [📄 name](/api/files/preview/...)``
    markdown link pointing at the created file. When you summarise the result
    to the user, **include this link verbatim** so they have a one-click way
    to open or download the file they just asked you to produce. Don't
    rewrite or paraphrase the URL; copying the markdown line as-is lets the
    chat UI render it as a clickable attachment.

    Args:
        file_path (`str`):
            Path to the file.
        content (`str`):
            Content to write.
    """

    if not file_path:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Error: No `file_path` provided.",
                ),
            ],
        )

    try:
        file_path = _resolve_write_path(file_path)
    except SubAgentWriteDenied as e:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: {e}")],
        )
    encoding = _get_encoding_for_file(file_path)

    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "w", encoding=encoding) as file:
            file.write(content)
        link = _download_link(file_path)
        suffix = f"\nDownload: {link}" if link else ""
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"Wrote {len(content)} bytes to {file_path}.{suffix}"
                    ),
                ),
            ],
        )
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Write file failed due to \n{e}",
                ),
            ],
        )


# pylint: disable=too-many-return-statements
async def edit_file(
    file_path: str,
    old_text: str,
    new_text: str,
) -> ToolResponse:
    """Find-and-replace text in a file. All occurrences of old_text are
    replaced with new_text. Relative paths resolve from WORKING_DIR.

    On success the tool returns a ``Download: [📄 name](/api/files/preview/...)``
    markdown link — forward it to the user verbatim in your reply so they
    can open the updated file in one click.

    Args:
        file_path (`str`):
            Path to the file.
        old_text (`str`):
            Exact text to find.
        new_text (`str`):
            Replacement text.
    """

    if not file_path:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Error: No `file_path` provided.",
                ),
            ],
        )

    resolved_path = _resolve_file_path(file_path)

    if not os.path.exists(resolved_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The file {resolved_path} does not exist.",
                ),
            ],
        )

    if not os.path.isfile(resolved_path):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The path {resolved_path} is not a file.",
                ),
            ],
        )

    try:
        content = read_file_safe(resolved_path)
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Read file failed due to \n{e}",
                ),
            ],
        )

    if old_text not in content:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: The text to replace was not found in {file_path}.",
                ),
            ],
        )

    new_content = content.replace(old_text, new_text)
    write_response = await write_file(
        file_path=resolved_path,
        content=new_content,
    )

    if write_response.content and len(write_response.content) > 0:
        write_text = write_response.content[0].get("text", "")
        if write_text.startswith("Error:"):
            return write_response

    link = _download_link(resolved_path)
    suffix = f"\nDownload: {link}" if link else ""
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Successfully replaced text in {file_path}.{suffix}",
            ),
        ],
    )


async def append_file(
    file_path: str,
    content: str,
) -> ToolResponse:
    """Append content to the end of a file. Relative paths resolve from
    WORKING_DIR.

    The tool's response includes a ``Download: [📄 name](/api/files/preview/...)``
    markdown link. Forward that line verbatim to the user — it renders in
    the chat UI as a clickable link to open/download the file.

    Args:
        file_path (`str`):
            Path to the file.
        content (`str`):
            Content to append.
    """

    if not file_path:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Error: No `file_path` provided.",
                ),
            ],
        )

    try:
        file_path = _resolve_write_path(file_path)
    except SubAgentWriteDenied as e:
        return ToolResponse(
            content=[TextBlock(type="text", text=f"Error: {e}")],
        )
    encoding = _get_encoding_for_file(file_path)

    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, "a", encoding=encoding) as file:
            file.write(content)
        link = _download_link(file_path)
        suffix = f"\nDownload: {link}" if link else ""
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=(
                        f"Appended {len(content)} bytes to {file_path}.{suffix}"
                    ),
                ),
            ],
        )
    except Exception as e:
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Error: Append file failed due to \n{e}",
                ),
            ],
        )
