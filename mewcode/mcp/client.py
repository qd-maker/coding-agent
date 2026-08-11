"""MCP client: single-server session lifecycle."""

from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import AsyncExitStack
from typing import cast

import httpx
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client

from mewcode.config import MCPServerConfig, build_child_env, resolve_env_vars

logger = logging.getLogger(__name__)


class MCPClient:
    """Single-server MCP session handle.

    Lifecycle::

        client = MCPClient(config)
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("name", {"arg": "val"})
        await client.close()
    """

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.name = config.name
        self._session: ClientSession | None = None
        self._stack = AsyncExitStack()
        self._alive = False

    @property
    def is_alive(self) -> bool:
        return self._alive

    async def connect(self) -> None:
        """Establish transport + session.  Rolls back via _cleanup_stack on failure."""
        try:
            if self.config.is_stdio:
                await self._connect_stdio()
            else:
                await self._connect_http()
            self._alive = True
        except BaseException:
            await self._cleanup_stack()
            raise

    async def _connect_stdio(self) -> None:
        assert self.config.command is not None  # guarded by is_stdio
        command = shutil.which(self.config.command) or self.config.command
        params = StdioServerParameters(
            command=command,
            args=self.config.args,
            env=build_child_env(self.config.env),
        )
        read_stream, write_stream = await self._stack.enter_async_context(stdio_client(params))
        session = ClientSession(read_stream, write_stream)
        await self._stack.enter_async_context(session)
        await session.initialize()
        self._session = session

    async def _connect_http(self) -> None:
        assert self.config.url is not None  # guarded by is_stdio

        # Resolve ${VAR} in each header value
        resolved_headers = {
            key: resolve_env_vars(value) for key, value in self.config.headers.items()
        }

        http_client = httpx.AsyncClient(headers=resolved_headers)
        await self._stack.enter_async_context(http_client)

        read_stream, write_stream, _get_session_id = await self._stack.enter_async_context(
            streamable_http_client(
                self.config.url,
                http_client=http_client,
                terminate_on_close=True,
            )
        )
        session = ClientSession(read_stream, write_stream)
        await self._stack.enter_async_context(session)
        await session.initialize()
        self._session = session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[types.Tool]:
        """Return the server's tool list."""
        if self._session is None:
            raise RuntimeError("MCPClient is not connected; call connect() first")
        response = await self._session.list_tools()
        return cast(list[types.Tool], response.tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, object] | None = None,
    ) -> types.CallToolResult:
        """Invoke a tool and return the raw CallToolResult."""
        if self._session is None:
            raise RuntimeError("MCPClient is not connected; call connect() first")
        return await asyncio.wait_for(
            self._session.call_tool(name, arguments),
            timeout=self.config.tool_timeout,
        )

    async def close(self) -> None:
        """Tear down the session and transport.  Idempotent."""
        if not self._alive:
            return
        self._alive = False
        self._session = None
        await self._cleanup_stack()

    async def _cleanup_stack(self) -> None:
        """Close the AsyncExitStack, silencing known anyio cancel-scope races."""
        try:
            await self._stack.aclose()
        except RuntimeError as exc:
            if "cancel scope" in str(exc).lower():
                logger.debug("Suppressed known anyio cancel-scope RuntimeError: %s", exc)
            else:
                logger.debug("Unexpected RuntimeError during MCP cleanup: %s", exc)
        except Exception as exc:
            logger.debug("Exception during MCP cleanup: %s", exc)
