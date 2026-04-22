# -*- coding: utf-8 -*-
"""Auth-protected file preview for workspace-produced artefacts.

Scope (security-critical):
    The resolved path MUST land under ``WORKING_DIR/workspaces/``. Anything
    else (``/etc/passwd``, ``~/.hubos/config.json``, another user's home)
    returns 403. This is the only thing standing between a prompt-injected
    tool-produced link and arbitrary file disclosure.
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from starlette.responses import FileResponse

from ...constant import WORKING_DIR

router = APIRouter(prefix="/files", tags=["files"])


def _workspaces_root() -> Path:
    return (WORKING_DIR / "workspaces").resolve()


@router.api_route(
    "/preview/{filepath:path}",
    methods=["GET", "HEAD"],
    summary="Preview a workspace file",
    description=(
        "Stream a file for preview / download. The requested path must "
        "resolve to a real file under the HubOS workspaces tree "
        "(`WORKING_DIR/workspaces/...`); any path that would escape that "
        "root is rejected with 403. Symlinks are followed via "
        "`Path.resolve()`, so sym-linking out of the workspace does not "
        "bypass the check."
    ),
)
async def preview_file(filepath: str):
    """Preview / download a workspace file."""
    if not filepath:
        raise HTTPException(status_code=400, detail="Empty path")

    raw = Path(filepath)
    if not raw.is_absolute():
        raw = Path("/" + filepath)

    try:
        resolved = raw.resolve()
    except (OSError, RuntimeError):
        # RuntimeError from infinite-loop symlinks, OSError from perm/IO errors.
        raise HTTPException(status_code=400, detail="Invalid path")

    root = _workspaces_root()
    try:
        resolved.relative_to(root)
    except ValueError:
        # Path escapes the sanctioned root → refuse, do NOT leak existence.
        raise HTTPException(status_code=403, detail="Forbidden")

    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="Not found")

    return FileResponse(resolved, filename=resolved.name)
