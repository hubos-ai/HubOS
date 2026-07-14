# -*- coding: utf-8 -*-
"""Background warmup tasks for HubOS startup.

The coordinator runs only after the HTTP app and channel gateways are ready.
Warmup failures are logged but never allowed to break Feishu/console ingress.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Iterable

from ..constant import WORKING_DIR

logger = logging.getLogger(__name__)


class WarmupCoordinator:
    """Run non-critical startup warmups in the background."""

    def __init__(
        self,
        multi_agent_manager,
        *,
        working_dir: Path = WORKING_DIR,
    ) -> None:
        self._multi_agent_manager = multi_agent_manager
        self._working_dir = Path(working_dir)

    async def run(self) -> None:
        """Run the warmup plan.

        The order is intentional: load workspaces first, warm cheap local
        indexes next, then warm network MCPs with tight timeouts.
        """
        started = time.monotonic()
        logger.info("HubOS background warmup started")

        await self._run_step(
            "lazy agents",
            self._multi_agent_manager.warmup_lazy_agents(max_concurrency=2),
            timeout=90.0,
        )
        workspaces = list(self._multi_agent_manager.agents.values())

        await self._run_step(
            "skills",
            asyncio.to_thread(self._warmup_skills, workspaces),
            timeout=20.0,
        )
        await self._run_step(
            "knowledge files",
            asyncio.to_thread(self._warmup_knowledge_files, workspaces),
            timeout=20.0,
        )
        await self._run_step(
            "work experience cards",
            asyncio.to_thread(self._warmup_work_experience_cards),
            timeout=20.0,
        )
        mcp_workspaces = self._select_mcp_warmup_workspaces(workspaces)
        await self._run_step(
            "HTTP MCP clients",
            self._warmup_mcp_clients(
                mcp_workspaces,
                transports=("streamable_http", "sse"),
                per_client_timeout=_float_env(
                    "HUBOS_WARMUP_HTTP_MCP_TIMEOUT",
                    8.0,
                ),
                workspace_concurrency=_int_env(
                    "HUBOS_WARMUP_HTTP_MCP_WORKSPACES",
                    2,
                ),
                client_concurrency=2,
            ),
            timeout=_float_env("HUBOS_WARMUP_HTTP_MCP_TOTAL_TIMEOUT", 120.0),
        )

        if _bool_env("HUBOS_WARMUP_STDIO_MCP", False):
            delay = _float_env("HUBOS_WARMUP_STDIO_MCP_DELAY", 15.0)
            if delay > 0:
                await asyncio.sleep(delay)
            await self._run_step(
                "stdio MCP clients",
                self._warmup_mcp_clients(
                    mcp_workspaces,
                    transports=("stdio",),
                    per_client_timeout=_float_env(
                        "HUBOS_WARMUP_STDIO_MCP_TIMEOUT",
                        15.0,
                    ),
                    workspace_concurrency=1,
                    client_concurrency=1,
                ),
                timeout=_float_env(
                    "HUBOS_WARMUP_STDIO_MCP_TOTAL_TIMEOUT",
                    180.0,
                ),
            )

        logger.info(
            "HubOS background warmup finished in %.1fs",
            time.monotonic() - started,
        )

    async def _run_step(
        self,
        name: str,
        awaitable,
        *,
        timeout: float,
    ) -> None:
        started = time.monotonic()
        try:
            await asyncio.wait_for(awaitable, timeout=timeout)
            logger.info(
                "Warmup step '%s' completed in %.1fs",
                name,
                time.monotonic() - started,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Warmup step '%s' timed out after %.1fs",
                name,
                timeout,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Warmup step '%s' failed",
                name,
                exc_info=True,
            )

    @staticmethod
    def _warmup_skills(workspaces: Iterable) -> None:
        from ..agents.skills_manager import ensure_skills_initialized

        for workspace in workspaces:
            ensure_skills_initialized(workspace.workspace_dir)

    @staticmethod
    def _warmup_knowledge_files(workspaces: Iterable) -> None:
        for workspace in workspaces:
            knowledge_dir = workspace.workspace_dir / "memory" / "knowledge"
            if not knowledge_dir.is_dir():
                continue
            for md_file in sorted(knowledge_dir.glob("*.md")):
                try:
                    md_file.read_text(encoding="utf-8")
                except Exception:
                    logger.debug(
                        "Failed to warm knowledge file %s",
                        md_file,
                        exc_info=True,
                    )

    @staticmethod
    def _warmup_work_experience_cards() -> None:
        from ..core.work_experience.store_v4 import CardStore

        CardStore().list_all()

    @staticmethod
    def _select_mcp_warmup_workspaces(workspaces: list) -> list:
        """Return the workspaces whose MCP clients should be pre-connected.

        Every workspace has its own MCP manager. Warming all department and
        Feishu workspaces duplicates HTTP/SSE connections and creates noisy
        startup bursts. By default we only warm the entrypoint and the most
        common delegated research agent; first use still lazily connects MCPs
        for any other agent.
        """
        raw = os.environ.get("HUBOS_WARMUP_MCP_AGENTS", "default,research")
        wanted = {part.strip() for part in raw.split(",") if part.strip()}
        if not wanted or "*" in wanted:
            selected = list(workspaces)
        else:
            selected = [
                ws
                for ws in workspaces
                if getattr(ws, "agent_id", "") in wanted
            ]
        logger.info(
            "MCP warmup workspaces: %s",
            ", ".join(getattr(ws, "agent_id", "?") for ws in selected)
            or "(none)",
        )
        return selected

    async def _warmup_mcp_clients(
        self,
        workspaces: Iterable,
        *,
        transports: tuple[str, ...],
        per_client_timeout: float,
        workspace_concurrency: int,
        client_concurrency: int,
    ) -> None:
        sem = asyncio.Semaphore(max(1, workspace_concurrency))

        async def _warm_workspace(workspace) -> None:
            async with sem:
                mcp_manager = getattr(workspace, "mcp_manager", None)
                if mcp_manager is None:
                    return
                await mcp_manager.prewarm_clients(
                    transports=transports,
                    timeout=per_client_timeout,
                    max_concurrency=client_concurrency,
                )

        await asyncio.gather(*[_warm_workspace(ws) for ws in workspaces])


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default
