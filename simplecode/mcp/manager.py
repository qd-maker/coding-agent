"""MCP manager: multi-server orchestration."""

from __future__ import annotations

import asyncio
import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from simplecode.config import MCPServerConfig
from simplecode.mcp.client import MCPClient
from simplecode.mcp.tool_wrapper import MCPToolWrapper

if TYPE_CHECKING:
    from simplecode.tools import ToolRegistry

logger = logging.getLogger(__name__)


class MCPServerState(StrEnum):
    IDLE = "idle"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    FAILED = "failed"
    EXPIRED = "expired"


class MCPManager:
    """Orchestrates connections to multiple MCP servers.

    Usage::

        manager = MCPManager()
        manager.load_configs(config.mcp_servers)
        errors = await manager.register_all_tools(registry)
        # ... use tools ...
        await manager.shutdown()
    """

    def __init__(self) -> None:
        self._configs: dict[str, MCPServerConfig] = {}
        self._clients: dict[str, MCPClient] = {}
        self._states: dict[str, MCPServerState] = {}
        self._registration_lock = asyncio.Lock()

    def load_configs(self, configs: list[MCPServerConfig]) -> None:
        """Register server configs by name."""
        for cfg in configs:
            self._configs[cfg.name] = cfg
            self._states.setdefault(cfg.name, MCPServerState.IDLE)

    async def register_all_tools(
        self,
        registry: ToolRegistry,
        *,
        retry_failed: bool = False,
    ) -> list[str]:
        """Lazily connect/register servers once; concurrent callers share one critical section."""

        async with self._registration_lock:
            return await self._register_pending_tools(registry, retry_failed=retry_failed)

    async def _register_pending_tools(
        self,
        registry: ToolRegistry,
        *,
        retry_failed: bool,
    ) -> list[str]:
        errors: list[str] = []

        async def prepare(
            name: str,
            cfg: MCPServerConfig,
        ) -> tuple[str, MCPClient, list[MCPToolWrapper], str | None]:
            client = MCPClient(cfg)
            self._states[name] = MCPServerState.CONNECTING
            try:
                await asyncio.wait_for(client.connect(), timeout=cfg.startup_timeout)
                tool_defs = await asyncio.wait_for(
                    client.list_tools(),
                    timeout=cfg.startup_timeout,
                )
                wrappers = [
                    MCPToolWrapper(manager=self, server_name=name, tool_def=tool_def)
                    for tool_def in tool_defs
                ]
                return name, client, wrappers, None
            except asyncio.CancelledError:
                await client.close()
                raise
            except Exception as exc:
                await client.close()
                if isinstance(exc, TimeoutError):
                    detail = f"timed out after {cfg.startup_timeout:g}s"
                else:
                    detail = str(exc)
                return name, client, [], detail

        pending = [
            (name, cfg)
            for name, cfg in self._configs.items()
            if self._states.get(name, MCPServerState.IDLE)
            in (
                {MCPServerState.IDLE, MCPServerState.EXPIRED, MCPServerState.FAILED}
                if retry_failed
                else {MCPServerState.IDLE, MCPServerState.EXPIRED}
            )
        ]
        if not pending:
            return errors
        prepared = await asyncio.gather(
            *(prepare(name, cfg) for name, cfg in pending)
        )
        for name, client, wrappers, failure in prepared:
            if failure is not None:
                self._clients.pop(name, None)
                self._states[name] = MCPServerState.FAILED
                message = f"MCP server {name!r} failed to connect: {failure}"
                logger.warning(message)
                errors.append(message)
                continue

            try:
                wrapper_names = [wrapper.name for wrapper in wrappers]
                if len(wrapper_names) != len(set(wrapper_names)):
                    raise ValueError(f"MCP server {name!r} returned duplicate tool names")
                existing_names = {tool.name for tool in registry.list_tools()}
                conflicts = sorted(existing_names.intersection(wrapper_names))
                if conflicts:
                    raise ValueError(
                        f"MCP server {name!r} tool name conflict(s): {', '.join(conflicts)}"
                    )

                for wrapper in wrappers:
                    registry.register(wrapper)
                self._clients[name] = client
                self._states[name] = MCPServerState.CONNECTED
                logger.info("MCP server %r connected; %d tool(s) registered", name, len(wrappers))
            except Exception as exc:
                await client.close()
                self._clients.pop(name, None)
                self._states[name] = MCPServerState.FAILED
                message = f"MCP server {name!r} failed to register tools: {exc}"
                logger.warning(message)
                errors.append(message)

        return errors

    @property
    def connected_server_names(self) -> list[str]:
        """Return configured server names that currently have a live client."""
        return [name for name, client in self._clients.items() if client.is_alive]

    @property
    def server_states(self) -> dict[str, MCPServerState]:
        return dict(self._states)

    async def get_client(self, server_name: str) -> MCPClient:
        """Return the live client, reconnecting if needed (lazy reconnect)."""
        client = self._clients.get(server_name)
        cfg = self._configs.get(server_name)

        if cfg is None:
            raise KeyError(f"Unknown MCP server: {server_name!r}")

        if client is not None and client.is_alive:
            return client

        if client is not None:
            self._states[server_name] = MCPServerState.EXPIRED

        # Reconnect
        new_client = MCPClient(cfg)
        self._states[server_name] = MCPServerState.CONNECTING
        try:
            await asyncio.wait_for(new_client.connect(), timeout=cfg.startup_timeout)
        except BaseException:
            self._states[server_name] = MCPServerState.FAILED
            await new_client.close()
            raise
        self._clients[server_name] = new_client
        self._states[server_name] = MCPServerState.CONNECTED
        return new_client

    async def shutdown(self) -> None:
        """Close all live clients.  Idempotent."""
        for name, client in list(self._clients.items()):
            try:
                await client.close()
            except Exception as exc:
                logger.debug("Exception closing MCP client %r: %s", name, exc)
        self._clients.clear()


__all__ = ["MCPManager", "MCPServerState"]
