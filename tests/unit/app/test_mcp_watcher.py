# -*- coding: utf-8 -*-
import pytest

from hubos.app.mcp.manager import MCPClientManager
from hubos.app.mcp.watcher import MCPConfigWatcher
from hubos.config.config import MCPClientConfig, MCPConfig


@pytest.mark.asyncio
async def test_mcp_watcher_keeps_idle_manager_lazy_on_config_change():
    """Hot-reload should not spawn stdio clients for idle lazy workspaces."""

    old_config = MCPConfig(clients={})
    new_config = MCPConfig(
        clients={
            "minimax": MCPClientConfig(
                name="minimax_mcp",
                enabled=True,
                command="uvx",
                args=["minimax-coding-plan-mcp"],
            ),
        },
    )
    current_config = old_config
    manager = MCPClientManager()
    manager.schedule_init_from_config(old_config)

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("idle MCP watcher should not connect clients")

    manager.replace_client = fail_if_called  # type: ignore[method-assign]
    watcher = MCPConfigWatcher(manager, lambda: current_config)
    watcher._snapshot()  # pylint: disable=protected-access

    current_config = new_config
    await watcher._check()  # pylint: disable=protected-access
    if watcher._reload_task is not None:  # pylint: disable=protected-access
        await watcher._reload_task  # pylint: disable=protected-access

    assert manager._config == new_config  # pylint: disable=protected-access
    assert manager.is_lazy_idle()


@pytest.mark.asyncio
async def test_mcp_partial_prewarm_does_not_hide_remaining_clients():
    """HTTP prewarm should not prevent later full MCP initialisation."""

    config = MCPConfig(
        clients={
            "zhipu_search": MCPClientConfig(
                name="web-search-prime",
                enabled=True,
                transport="streamable_http",
                url="https://example.test/mcp",
            ),
            "minimax": MCPClientConfig(
                name="minimax_mcp",
                enabled=True,
                command="uvx",
                args=["minimax-coding-plan-mcp"],
            ),
        },
    )
    manager = MCPClientManager()
    manager.schedule_init_from_config(config)

    async def fake_add_client(key, _client_config, timeout=60.0):
        manager._clients[
            key
        ] = f"client:{key}:{timeout}"  # pylint: disable=protected-access

    manager._add_client = fake_add_client  # type: ignore[method-assign]  # pylint: disable=protected-access

    await manager.prewarm_clients(
        transports=("streamable_http",),
        timeout=3.0,
    )

    assert set(manager._clients) == {
        "zhipu_search",
    }  # pylint: disable=protected-access
    assert not manager._fully_initialized  # pylint: disable=protected-access

    clients = await manager.get_clients()

    assert set(manager._clients) == {  # pylint: disable=protected-access
        "zhipu_search",
        "minimax",
    }
    assert manager._fully_initialized  # pylint: disable=protected-access
    assert clients
