# -*- coding: utf-8 -*-
"""MCP client manager for hot-reloadable client lifecycle management.

This module provides centralized management of MCP clients with support
for runtime updates without restarting the application.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any, Dict, List, TYPE_CHECKING

from agentscope.mcp import HttpStatefulClient, StdIOStatefulClient

if TYPE_CHECKING:
    from ...config.config import MCPClientConfig, MCPConfig

logger = logging.getLogger(__name__)


class MCPClientManager:
    """Manages MCP clients with hot-reload support.

    This manager handles the lifecycle of MCP clients, including:
    - Initial loading from config (background, non-blocking)
    - Runtime replacement when config changes
    - Cleanup on shutdown

    Design pattern mirrors ChannelManager for consistency.

    Lazy initialisation
    -------------------
    ``schedule_init_from_config`` records the MCP configuration without
    starting stdio/http clients immediately.  ``get_clients()`` starts and
    awaits the connection task on first use.  This keeps multi-user Feishu
    startup light: pre-warmed workspaces do not each spawn their own npx/uvx
    subprocesses until a request actually needs tools.
    """

    def __init__(self) -> None:
        """Initialize an empty MCP client manager."""
        self._clients: Dict[str, Any] = {}
        self._lock = asyncio.Lock()
        self._config: "MCPConfig | None" = None
        # Init task — started lazily by get_clients or eagerly by callers.
        self._init_task: asyncio.Task | None = None
        self._fully_initialized = False

    # ------------------------------------------------------------------
    # Public initialisation API
    # ------------------------------------------------------------------

    def schedule_init_from_config(
        self,
        config: "MCPConfig",
        *,
        eager: bool = False,
    ) -> None:
        """Record MCP configuration and optionally initialise eagerly.

        By default this is lazy: it returns immediately and does not spawn MCP
        subprocesses until ``get_clients()`` is called.  ``eager=True`` keeps
        the old background-start behavior for callers that explicitly need it.

        Args:
            config: MCP configuration containing client definitions.
            eager: Whether to start connecting in the background immediately.
        """
        self._config = config
        self._fully_initialized = False
        if eager:
            self._init_task = asyncio.create_task(
                self._init_from_config_impl(config),
                name="mcp_init",
            )

    def is_lazy_idle(self) -> bool:
        """Return True when MCP config exists but clients have not started.

        Config hot-reload can happen while a lazy workspace is merely
        pre-warmed.  In that state we should update the stored config without
        spawning stdio clients such as npx/uvx servers; otherwise saving
        agent.json for multiple department agents can trigger a burst of
        expensive subprocess starts before any user task actually needs them.
        """
        return not self._clients and self._init_task is None

    async def init_from_config(self, config: "MCPConfig") -> None:
        """Initialize clients from configuration (blocking variant).

        Prefer ``schedule_init_from_config`` for startup paths.
        This method exists for callers that must await completion
        (e.g. hot-reload triggered by config file watcher).
        """
        self._config = config
        await self._init_from_config_impl(config)

    async def _init_from_config_impl(self, config: "MCPConfig") -> None:
        """Connect all enabled MCP clients in parallel.

        All enabled MCP clients are started concurrently via
        ``asyncio.gather`` so that slow stdio subprocesses (npx / uvx)
        don't block each other.  Individual failures are caught and
        logged without aborting the others.
        """
        import time

        await self._connect_config_clients(config)
        self._fully_initialized = True

    async def prewarm_clients(
        self,
        *,
        transports: tuple[str, ...] = ("streamable_http", "sse"),
        timeout: float = 8.0,
        max_concurrency: int = 2,
    ) -> None:
        """Connect a safe subset of configured clients in the background.

        This is intentionally partial: startup warmup can connect lightweight
        HTTP/SSE clients while leaving stdio clients lazy.  A later
        ``get_clients()`` still performs full initialisation for anything not
        prewarmed.
        """
        if self._config is None:
            return
        if self._fully_initialized:
            return
        if self._init_task is not None and not self._init_task.done():
            return
        await self._connect_config_clients(
            self._config,
            transports=transports,
            timeout=timeout,
            max_concurrency=max_concurrency,
        )

    async def _connect_config_clients(
        self,
        config: "MCPConfig",
        *,
        transports: tuple[str, ...] | None = None,
        timeout: float = 60.0,
        max_concurrency: int | None = None,
    ) -> None:
        """Connect enabled clients from a config, optionally filtered."""
        import time

        enabled = dict(
            sorted(
                (
                    (key, cfg)
                    for key, cfg in config.clients.items()
                    if cfg.enabled
                    and (transports is None or cfg.transport in transports)
                ),
                key=lambda item: self._client_priority(item[0], item[1]),
            ),
        )
        if not enabled:
            return

        logger.info(
            "Connecting %d MCP client(s)%s: %s",
            len(enabled),
            (
                f" for transports={','.join(transports)}"
                if transports is not None
                else ""
            ),
            ", ".join(enabled),
        )
        t0 = time.monotonic()
        sem = asyncio.Semaphore(max_concurrency or len(enabled))

        async def _init_one(
            key: str,
            client_config: "MCPClientConfig",
        ) -> None:
            async with sem:
                async with self._lock:
                    if key in self._clients:
                        logger.debug("MCP client '%s' already connected", key)
                        return

                t = time.monotonic()
                try:
                    await self._add_client(
                        key,
                        client_config,
                        timeout=timeout,
                    )
                    logger.info(
                        "MCP client '%s' ready in %.1fs",
                        key,
                        time.monotonic() - t,
                    )
                except BaseException as exc:
                    if isinstance(
                        exc,
                        (
                            asyncio.CancelledError,
                            KeyboardInterrupt,
                            SystemExit,
                        ),
                    ):
                        raise
                    logger.warning(
                        "MCP client '%s' failed to connect (%.1fs): %s",
                        key,
                        time.monotonic() - t,
                        exc,
                        exc_info=True,
                    )

        await asyncio.gather(*[_init_one(k, v) for k, v in enabled.items()])
        logger.info(
            "All MCP clients connected in %.1fs",
            time.monotonic() - t0,
        )

    # ------------------------------------------------------------------
    # Client access
    # ------------------------------------------------------------------

    async def get_clients(self) -> List[Any]:
        """Get list of all active MCP clients.

        If a background initialisation task is still running, this
        method waits for it to finish before returning.  This ensures
        that callers always see the full set of connected clients even
        when MCP init was deferred to the background.

        Returns:
            List of connected MCP client instances
        """
        if (
            self._init_task is None
            and self._config is not None
            and not self._fully_initialized
        ):
            async with self._lock:
                if self._init_task is None and not self._fully_initialized:
                    self._init_task = asyncio.create_task(
                        self._init_from_config_impl(self._config),
                        name="mcp_init",
                    )

        if self._init_task is not None and not self._init_task.done():
            try:
                await self._init_task
            except Exception:
                pass  # errors already logged inside _init_from_config_impl

        async with self._lock:
            ordered = sorted(
                self._clients.items(),
                key=lambda item: self._client_priority(item[0], None),
            )
            return [client for _, client in ordered if client is not None]

    async def replace_client(
        self,
        key: str,
        client_config: "MCPClientConfig",
        timeout: float = 60.0,
    ) -> None:
        """Replace or add a client with new configuration.

        Flow: connect new (outside lock) → swap + close old (inside lock).
        This ensures minimal lock holding time.

        Args:
            key: Client identifier (from config)
            client_config: New client configuration
            timeout: Connection timeout in seconds (default 60s)
        """
        # 1. Create and connect new client outside lock (may be slow)
        logger.debug(f"Connecting new MCP client: {key}")
        new_client = self._build_client(client_config)

        try:
            # Add timeout to prevent indefinite blocking
            await asyncio.wait_for(new_client.connect(), timeout=timeout)
        except BaseException:
            await self._force_cleanup_client(new_client)
            raise

        # 2. Swap and close old client inside lock
        async with self._lock:
            old_client = self._clients.get(key)
            self._clients[key] = new_client

            if old_client is not None:
                logger.debug(f"Closing old MCP client: {key}")
                try:
                    await old_client.close()
                except Exception as e:
                    logger.warning(
                        f"Error closing old MCP client '{key}': {e}",
                    )
            else:
                logger.debug(f"Added new MCP client: {key}")

    async def remove_client(self, key: str) -> None:
        """Remove and close a client.

        Args:
            key: Client identifier to remove
        """
        async with self._lock:
            old_client = self._clients.pop(key, None)

        if old_client is not None:
            logger.debug(f"Removing MCP client: {key}")
            try:
                await old_client.close()
            except Exception as e:
                logger.warning(f"Error closing MCP client '{key}': {e}")

    async def close_all(self) -> None:
        """Close all MCP clients.

        Called during application shutdown.
        """
        if self._init_task is not None and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug(
                    "MCP init task failed during close",
                    exc_info=True,
                )

        async with self._lock:
            clients_snapshot = list(self._clients.items())
            self._clients.clear()
            self._fully_initialized = False

        logger.debug("Closing all MCP clients")
        for key, client in clients_snapshot:
            if client is not None:
                try:
                    await client.close()
                except Exception as e:
                    logger.warning(f"Error closing MCP client '{key}': {e}")

    async def _add_client(
        self,
        key: str,
        client_config: "MCPClientConfig",
        timeout: float = 60.0,
    ) -> None:
        """Add a new client (used during initial setup).

        Args:
            key: Client identifier
            client_config: Client configuration
            timeout: Connection timeout in seconds (default 60s)
        """
        client = self._build_client(client_config)

        try:
            await asyncio.wait_for(client.connect(), timeout=timeout)
        except BaseException:
            await self._force_cleanup_client(client)
            raise

        async with self._lock:
            self._clients[key] = client

    @staticmethod
    def _client_priority(
        key: str,
        client_config: "MCPClientConfig | None",
    ) -> tuple[int, str]:
        """Return stable MCP tool ordering.

        Search-capable clients are intentionally ordered to match HubOS search
        policy: GLM/Zhipu and MiniMax first, Tavily as the final paid fallback.
        The same order is used for connection order and the tool list passed to
        the model, which nudges tool selection without removing fallback tools.
        """
        name = (client_config.name if client_config else key).lower()
        ident = f"{key.lower()} {name}"
        if "zhipu_search" in ident or "web_search_prime" in ident:
            return (10, key)
        if "zhipu_reader" in ident or "webreader" in ident:
            return (20, key)
        if "zhipu_zread" in ident:
            return (25, key)
        if "search_doc" in ident or "repo_structure" in ident:
            return (30, key)
        if "zhipu" in ident or "glm" in ident:
            return (40, key)
        if "minimax" in ident:
            return (50, key)
        if "web_crawl" in ident or "xcrawl" in ident:
            return (80, key)
        if "tavily" in ident:
            return (900, key)
        if "browser" in ident:
            return (950, key)
        return (100, key)

    @staticmethod
    async def _force_cleanup_client(client: Any) -> None:
        """Force-close a client whose ``connect()`` was interrupted.

        ``StatefulClientBase.close()`` refuses to run when
        ``is_connected`` is still ``False`` (which is the case when
        ``connect()`` times out or raises).  We bypass that guard by
        closing the ``AsyncExitStack`` directly — this triggers the
        ``stdio_client`` finally-block that sends SIGTERM/SIGKILL to
        the child process.

        The ``ClientSession`` is registered on the same stack via
        ``enter_async_context``, so ``stack.aclose()`` exits it in
        LIFO order — no separate session teardown is needed.
        """
        if client is None:
            return

        stack = getattr(client, "stack", None)
        if stack is None:
            return

        try:
            await stack.aclose()
        except Exception:
            logger.debug(
                "Error during force-cleanup of MCP client",
                exc_info=True,
            )
        finally:
            for attr, default in (
                ("stack", None),
                ("session", None),
                ("is_connected", False),
            ):
                try:
                    setattr(client, attr, default)
                except Exception:
                    pass

    @staticmethod
    def _resolve_stdio_command(command: str) -> str:
        """Resolve stdio command for GUI/LaunchAgent environments.

        macOS LaunchAgents often run with a minimal PATH, so commands that are
        available in an interactive shell (for example ``npx`` from Homebrew)
        may fail with ``FileNotFoundError``. Resolve common binary locations up
        front and pass an absolute executable path to the MCP client.
        """
        expanded = os.path.expanduser(os.path.expandvars(command.strip()))
        if not expanded:
            return command

        if os.path.sep in expanded:
            return expanded

        path_parts = [
            os.environ.get("PATH", ""),
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/opt/homebrew/sbin",
            "/usr/local/sbin",
        ]
        resolved = shutil.which(
            expanded,
            path=os.pathsep.join(part for part in path_parts if part),
        )
        return resolved or expanded

    @staticmethod
    def _build_stdio_env(extra_env: Dict[str, str]) -> Dict[str, str]:
        """Merge stdio MCP env with a robust PATH for GUI launches."""
        env = dict(extra_env or {})
        current_path = env.get("PATH") or os.environ.get("PATH", "")
        additions = [
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
            "/opt/homebrew/sbin",
            "/usr/local/sbin",
        ]
        parts = [p for p in current_path.split(os.pathsep) if p]
        for item in additions:
            if item not in parts:
                parts.append(item)
        env["PATH"] = os.pathsep.join(parts)
        return env

    @staticmethod
    def _build_client(client_config: "MCPClientConfig") -> Any:
        """Build MCP client instance by configured transport."""
        command = MCPClientManager._resolve_stdio_command(
            client_config.command,
        )
        env = MCPClientManager._build_stdio_env(client_config.env)
        rebuild_info = {
            "name": client_config.name,
            "transport": client_config.transport,
            "url": client_config.url,
            "headers": client_config.headers or None,
            "command": command,
            "args": list(client_config.args),
            "env": dict(env),
            "cwd": client_config.cwd or None,
        }

        if client_config.transport == "stdio":
            client = StdIOStatefulClient(
                name=client_config.name,
                command=command,
                args=client_config.args,
                env=env,
                cwd=client_config.cwd or None,
            )
            setattr(client, "_hubos_rebuild_info", rebuild_info)
            return client

        headers = client_config.headers
        if headers:
            headers = {k: os.path.expandvars(v) for k, v in headers.items()}

        client = HttpStatefulClient(
            name=client_config.name,
            transport=client_config.transport,
            url=client_config.url,
            headers=headers or None,
        )
        setattr(client, "_hubos_rebuild_info", rebuild_info)
        return client
