"""Offline tests for the MCP layer (T1-T6 completion).

All tests are fully offline – no MCP server process or network required.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mewcode.config import (
    ConfigurationError,
    MCPServerConfig,
    build_child_env,
    load_config,
    resolve_env_vars,
)
from mewcode.mcp.tool_wrapper import MCPToolWrapper, _extract_text
from mewcode.tools import ToolRegistry

# ---------------------------------------------------------------------------
# Helpers / Fakes
# ---------------------------------------------------------------------------


def _make_tool_def(
    name: str = "search",
    description: str = "Search something",
    input_schema: dict[str, Any] | None = None,
) -> Any:
    """Build a fake types.Tool-like object without importing mcp types directly."""
    from mcp import types

    schema = input_schema or {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }
    return types.Tool(name=name, description=description, inputSchema=schema)


def _make_manager(server_name: str = "ctx7") -> Any:
    """Return a minimal fake MCPManager."""
    mgr = MagicMock()
    mgr.get_client = AsyncMock()
    return mgr


# ===========================================================================
# TestResolveEnvVars – resolve_env_vars
# ===========================================================================


class TestResolveEnvVars:
    def test_single_placeholder_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "hello")
        assert resolve_env_vars("${MY_KEY}") == "hello"

    def test_multiple_placeholders_in_one_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOST", "localhost")
        monkeypatch.setenv("PORT", "8080")
        result = resolve_env_vars("http://${HOST}:${PORT}/v1")
        assert result == "http://localhost:8080/v1"

    def test_missing_var_preserved_as_placeholder(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("MISSING_XYZ", raising=False)
        result = resolve_env_vars("Bearer ${MISSING_XYZ}")
        assert result == "Bearer ${MISSING_XYZ}"

    def test_no_placeholder_unchanged(self) -> None:
        assert resolve_env_vars("plain-text-value") == "plain-text-value"

    def test_partial_placeholder_preserved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """${PRESENT} resolved, ${ABSENT} kept."""
        monkeypatch.setenv("PRESENT", "ok")
        monkeypatch.delenv("ABSENT", raising=False)
        result = resolve_env_vars("${PRESENT}/${ABSENT}")
        assert result == "ok/${ABSENT}"


# ===========================================================================
# TestBuildChildEnv – build_child_env
# ===========================================================================


class TestBuildChildEnv:
    def test_path_always_included(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        env = build_child_env()
        assert env["PATH"] == "/usr/bin:/bin"

    def test_no_extra_host_vars_leaked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SECRET_API_KEY", "sk-should-not-leak")
        monkeypatch.setenv("PATH", "/usr/bin")
        env = build_child_env()
        assert "SECRET_API_KEY" not in env

    def test_explicit_env_overlays_and_expands(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TOKEN", "tok123")
        monkeypatch.setenv("PATH", "/usr/bin")
        env = build_child_env({"AUTH": "Bearer ${TOKEN}", "STATIC": "yes"})
        assert env["AUTH"] == "Bearer tok123"
        assert env["STATIC"] == "yes"

    def test_windows_runtime_vars_are_allowlisted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SYSTEMROOT", "C:\\Windows")
        monkeypatch.setenv("COMSPEC", "C:\\Windows\\System32\\cmd.exe")
        monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        monkeypatch.setenv("PATH", "C:\\Windows\\System32")
        env = build_child_env()
        if os.name == "nt":
            assert env["SYSTEMROOT"] == "C:\\Windows"
            assert env["COMSPEC"].endswith("cmd.exe")
            assert ".CMD" in env["PATHEXT"]
        else:
            assert "SYSTEMROOT" not in env

    def test_missing_windows_vars_not_injected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("COMSPEC", raising=False)
        monkeypatch.setenv("PATH", "/usr/bin")
        env = build_child_env()
        assert "COMSPEC" not in env


# ===========================================================================
# TestLoadConfigMCP – load_config with mcp_servers
# ===========================================================================


class TestLoadConfigMCP:
    def _base_yaml(self) -> str:
        return (
            "providers:\n"
            "  - name: anthropic-official\n"
            "    protocol: anthropic\n"
            "    model: claude-sonnet-4-6\n"
            "    base_url: https://api.anthropic.com\n"
            "    api_key: test-key\n"
        )

    def test_canonical_stdio_server_list_parsed(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  - name: context7\n"
            "    command: npx\n"
            '    args: ["-y", "@upstash/context7-mcp"]\n',
            encoding="utf-8",
        )

        config = load_config(cfg_path)

        assert len(config.mcp_servers) == 1
        assert config.mcp_servers[0].name == "context7"
        assert config.mcp_servers[0].args == ["-y", "@upstash/context7-mcp"]

    def test_stdio_server_parsed(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  ctx7:\n"
            "    command: npx\n"
            "    args: [-y, \"@upstash/context7-mcp\"]\n",
            encoding="utf-8",
        )
        config = load_config(cfg_path)
        assert len(config.mcp_servers) == 1
        srv = config.mcp_servers[0]
        assert srv.name == "ctx7"
        assert srv.command == "npx"
        assert srv.is_stdio is True

    def test_http_server_parsed(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  remote:\n"
            "    url: https://mcp.example.com/v1\n"
            "    headers:\n"
            "      Authorization: Bearer ${MY_TOKEN}\n",
            encoding="utf-8",
        )
        config = load_config(cfg_path)
        srv = config.mcp_servers[0]
        assert srv.url == "https://mcp.example.com/v1"
        assert srv.is_stdio is False
        assert "Authorization" in srv.headers

    def test_both_command_and_url_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  bad:\n"
            "    command: npx\n"
            "    url: https://example.com\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError):
            load_config(cfg_path)

    def test_both_command_and_url_errors(self, tmp_path: Path) -> None:
        """Checklist: error message must contain 'cannot have both'."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  bad:\n"
            "    command: npx\n"
            "    url: https://example.com\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="cannot have both"):
            load_config(cfg_path)

    def test_neither_command_nor_url_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  bad:\n"
            "    args: [-y]\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError):
            load_config(cfg_path)

    def test_neither_command_nor_url_errors(self, tmp_path: Path) -> None:
        """Checklist: error message must contain 'must have either'."""
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  bad:\n"
            "    args: [-y]\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="must have either"):
            load_config(cfg_path)

    def test_no_mcp_servers_returns_empty_list(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(self._base_yaml(), encoding="utf-8")
        config = load_config(cfg_path)
        assert config.mcp_servers == []

    def test_duplicate_server_names_raise(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  - name: duplicate\n"
            "    command: first\n"
            "  - name: duplicate\n"
            "    command: second\n",
            encoding="utf-8",
        )

        with pytest.raises(ConfigurationError, match="MCP server names must be unique"):
            load_config(cfg_path)

    def test_invalid_server_name_raises(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml()
            + "mcp_servers:\n"
            "  bad-name!:\n"
            "    command: npx\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError):
            load_config(cfg_path)

    def test_server_config_must_be_mapping(self, tmp_path: Path) -> None:
        cfg_path = tmp_path / "config.yaml"
        cfg_path.write_text(
            self._base_yaml() + "mcp_servers:\n  context7: npx\n",
            encoding="utf-8",
        )
        with pytest.raises(ConfigurationError, match="must be a YAML mapping"):
            load_config(cfg_path)


# ===========================================================================
# TestMCPServerConfig – direct model validation
# ===========================================================================


class TestMCPServerConfig:
    def test_stdio_config_valid(self) -> None:
        cfg = MCPServerConfig(name="myserver", command="python", args=["-m", "mcp"])
        assert cfg.is_stdio is True
        assert cfg.startup_timeout == 20
        assert cfg.tool_timeout == 120

    def test_http_config_valid(self) -> None:
        cfg = MCPServerConfig(name="remote", url="https://example.com")
        assert cfg.is_stdio is False

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises((ValueError, TypeError)):
            MCPServerConfig(name="x", command="y", unknown_field="z")  # type: ignore[call-arg]


# ===========================================================================
# TestMCPToolWrapper – MCPToolWrapper
# ===========================================================================


class TestMCPToolWrapper:
    def _make_wrapper(
        self,
        server_name: str = "ctx7",
        tool_name: str = "resolve_library_id",
        schema: dict[str, Any] | None = None,
    ) -> MCPToolWrapper:
        mgr = _make_manager(server_name)
        tool_def = _make_tool_def(tool_name, "Resolve a library ID", schema)
        return MCPToolWrapper(manager=mgr, server_name=server_name, tool_def=tool_def)

    def test_name_formatted_correctly(self) -> None:
        w = self._make_wrapper("ctx7", "search")
        assert w.name == "mcp_ctx7_search"

    def test_hyphenated_remote_name_is_normalized(self) -> None:
        wrapper = self._make_wrapper("context7", "resolve-library-id")
        assert wrapper.name == "mcp_context7_resolve_library_id"
        assert wrapper._tool_def.name == "resolve-library-id"

    def test_category_and_defer(self) -> None:
        w = self._make_wrapper()
        assert w.category == "command"
        assert w.should_defer is True

    def test_get_schema_returns_original_input_schema(self) -> None:
        schema = {
            "type": "object",
            "properties": {"libraryName": {"type": "string"}},
            "required": ["libraryName"],
        }
        w = self._make_wrapper(schema=schema)
        s = w.get_schema()
        assert s["input_schema"] == schema
        assert s["name"] == w.name

    def test_params_model_has_required_field(self) -> None:
        w = self._make_wrapper()
        model = w.params_model
        with pytest.raises(ValueError):
            model()

    def test_params_model_supports_six_json_types(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "count": {"type": "integer"},
                "ratio": {"type": "number"},
                "enabled": {"type": "boolean"},
                "metadata": {"type": "object"},
                "items": {"type": "array"},
                "optional": {"type": "string"},
            },
            "required": ["text", "count", "ratio", "enabled", "metadata", "items"],
        }
        wrapper = self._make_wrapper(schema=schema)
        params = wrapper.params_model(
            text="value",
            count=2,
            ratio=0.5,
            enabled=True,
            metadata={"source": "test"},
            items=["a", "b"],
        )
        assert params.model_dump(exclude_none=True)["count"] == 2
        assert "optional" not in params.model_dump(exclude_none=True)

    def test_params_model_preserves_nullable_scalar_type(self) -> None:
        schema = {
            "type": "object",
            "properties": {"query": {"type": ["string", "null"]}},
            "required": ["query"],
        }
        wrapper = self._make_wrapper(schema=schema)
        assert wrapper.params_model(query=None).query is None
        with pytest.raises(ValueError):
            wrapper.params_model(query=["not", "a", "string"])

    @pytest.mark.asyncio
    async def test_execute_returns_text_output(self) -> None:
        from mcp import types

        mgr = _make_manager()
        tool_def = _make_tool_def("search", "Search", {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        })
        wrapper = MCPToolWrapper(manager=mgr, server_name="ctx7", tool_def=tool_def)

        fake_result = MagicMock()
        fake_result.content = [types.TextContent(type="text", text="found it")]
        fake_result.structuredContent = None
        fake_result.isError = False

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=fake_result)
        mgr.get_client.return_value = mock_client

        params = wrapper.params_model(query="hello")
        result = await wrapper.execute(params)

        assert not result.is_error
        assert "found it" in result.output

    @pytest.mark.asyncio
    async def test_execute_propagates_is_error(self) -> None:
        from mcp import types

        mgr = _make_manager()
        tool_def = _make_tool_def()
        wrapper = MCPToolWrapper(manager=mgr, server_name="ctx7", tool_def=tool_def)

        fake_result = MagicMock()
        fake_result.content = [types.TextContent(type="text", text="bad")]
        fake_result.structuredContent = None
        fake_result.isError = True

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=fake_result)
        mgr.get_client.return_value = mock_client

        result = await wrapper.execute(wrapper.params_model(query="q"))
        assert result.is_error

    @pytest.mark.asyncio
    async def test_cancelled_error_not_swallowed(self) -> None:
        mgr = _make_manager()
        tool_def = _make_tool_def()
        wrapper = MCPToolWrapper(manager=mgr, server_name="ctx7", tool_def=tool_def)

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(side_effect=asyncio.CancelledError())
        mgr.get_client.return_value = mock_client

        with pytest.raises(asyncio.CancelledError):
            await wrapper.execute(wrapper.params_model(query="q"))


# ===========================================================================
# TestExtractText – _extract_text
# ===========================================================================


class TestExtractText:
    def test_text_content_extracted(self) -> None:
        from mcp import types

        blocks = [types.TextContent(type="text", text="hello world")]
        assert _extract_text(blocks) == "hello world"

    def test_image_content_placeholder(self) -> None:
        from mcp import types

        blocks = [types.ImageContent(type="image", mimeType="image/png", data="abc")]
        result = _extract_text(blocks)
        assert "image/png" in result

    def test_empty_content_returns_no_output(self) -> None:
        assert _extract_text([]) == "(no output)"

    def test_multiple_blocks_joined(self) -> None:
        from mcp import types

        blocks = [
            types.TextContent(type="text", text="line1"),
            types.TextContent(type="text", text="line2"),
        ]
        result = _extract_text(blocks)
        assert "line1" in result and "line2" in result


# ===========================================================================
# TestMCPManagerPartialFailure – MCPManager partial failure
# ===========================================================================


class TestMCPManagerPartialFailure:
    @pytest.mark.asyncio
    async def test_slow_servers_timeout_concurrently(self) -> None:
        from time import perf_counter

        from mewcode.mcp.manager import MCPManager
        from mewcode.tools import ToolRegistry

        manager = MCPManager()
        manager.load_configs(
            [
                MCPServerConfig(name="slow_a", command="slow", startup_timeout=0.02),
                MCPServerConfig(name="slow_b", command="slow", startup_timeout=0.02),
            ]
        )

        class SlowClient:
            def __init__(self, cfg: MCPServerConfig) -> None:
                self.config = cfg
                self.is_alive = False

            async def connect(self) -> None:
                await asyncio.sleep(1)

            async def list_tools(self) -> list[Any]:
                return []

            async def close(self) -> None:
                self.is_alive = False

        started = perf_counter()
        with patch("mewcode.mcp.manager.MCPClient", SlowClient):
            errors = await manager.register_all_tools(ToolRegistry())
        elapsed = perf_counter() - started

        assert len(errors) == 2
        assert all("timed out after 0.02s" in error for error in errors)
        assert elapsed < 0.08

    @pytest.mark.asyncio
    async def test_one_failure_does_not_block_others(self) -> None:
        """When one server fails to connect, the manager continues with others."""
        from mewcode.mcp.manager import MCPManager
        from mewcode.tools import ToolRegistry

        manager = MCPManager()

        good_cfg = MCPServerConfig(name="good", command="echo")
        bad_cfg = MCPServerConfig(name="bad", command="nonexistent_cmd_xyz")
        manager.load_configs([good_cfg, bad_cfg])

        registry = ToolRegistry()

        # Patch MCPClient.connect so 'good' succeeds and 'bad' raises
        good_tool_def = _make_tool_def("do_something")

        class FakeClient:
            def __init__(self, cfg: MCPServerConfig) -> None:
                self._cfg = cfg
                self.is_alive = False

            async def connect(self) -> None:
                if self._cfg.name == "bad":
                    raise OSError("process not found")
                self.is_alive = True

            async def list_tools(self) -> list:
                return [good_tool_def]

            async def close(self) -> None:
                self.is_alive = False

        with patch("mewcode.mcp.manager.MCPClient", FakeClient):
            errors = await manager.register_all_tools(registry)

        assert len(errors) == 1
        assert "bad" in errors[0]
        # 'good' tools should be registered
        registered = [t.name for t in registry.list_tools()]
        assert any("good" in n for n in registered)

    @pytest.mark.asyncio
    async def test_name_conflict_does_not_partially_register_server(self) -> None:
        from mewcode.mcp.manager import MCPManager
        from mewcode.tools import ToolRegistry

        manager = MCPManager()
        manager.load_configs([MCPServerConfig(name="good", command="echo")])
        registry = ToolRegistry()
        existing = MagicMock()
        existing.name = "mcp_good_do_something"
        registry.register(existing)
        closed = False

        class FakeClient:
            def __init__(self, cfg: MCPServerConfig) -> None:
                self.is_alive = False

            async def connect(self) -> None:
                self.is_alive = True

            async def list_tools(self) -> list[Any]:
                return [_make_tool_def("do_something"), _make_tool_def("other")]

            async def close(self) -> None:
                nonlocal closed
                closed = True
                self.is_alive = False

        with patch("mewcode.mcp.manager.MCPClient", FakeClient):
            errors = await manager.register_all_tools(registry)

        assert len(errors) == 1
        assert closed is True
        assert [tool.name for tool in registry.list_tools()] == ["mcp_good_do_something"]
        assert manager.connected_server_names == []

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self) -> None:
        from mewcode.mcp.manager import MCPManager

        manager = MCPManager()
        await manager.shutdown()
        await manager.shutdown()


# ===========================================================================
# TestClientTransportLifecycle – MCPClient lifecycle (offline, mocked transport)
# ===========================================================================


class TestClientTransportLifecycle:
    def test_client_exposes_config_and_name(self) -> None:
        from mewcode.mcp.client import MCPClient

        config = MCPServerConfig(name="named_server", command="echo")
        client = MCPClient(config)
        assert client.config is config
        assert client.name == "named_server"

    @pytest.mark.asyncio
    async def test_connect_sets_alive_and_close_clears_it(self) -> None:
        """connect() sets is_alive=True; close() sets it back to False."""
        from unittest.mock import AsyncMock, patch

        from mewcode.mcp.client import MCPClient

        cfg = MCPServerConfig(name="test_server", command="echo")

        # Stub out the internal _connect_stdio so no real process is spawned
        async def fake_connect_stdio(self: MCPClient) -> None:
            self._session = MagicMock()  # type: ignore[assignment]
            self._alive = True

        with patch.object(MCPClient, "_connect_stdio", fake_connect_stdio):
            client = MCPClient(cfg)
            assert client.is_alive is False

            await client.connect()
            assert client.is_alive is True

            # Close should mark the client dead
            # Patch _cleanup_stack to avoid real async stack teardown
            client._cleanup_stack = AsyncMock()  # type: ignore[method-assign]
            await client.close()
            assert client.is_alive is False

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        """Calling close() twice must not raise."""
        from unittest.mock import AsyncMock, patch

        from mewcode.mcp.client import MCPClient

        cfg = MCPServerConfig(name="test_server2", command="echo")

        async def fake_connect_stdio(self: MCPClient) -> None:
            self._session = MagicMock()  # type: ignore[assignment]
            self._alive = True

        with patch.object(MCPClient, "_connect_stdio", fake_connect_stdio):
            client = MCPClient(cfg)
            await client.connect()
            client._cleanup_stack = AsyncMock()  # type: ignore[method-assign]
            await client.close()
            await client.close()  # second call must not raise

    @pytest.mark.asyncio
    async def test_tool_call_timeout_is_enforced(self) -> None:
        from mewcode.mcp.client import MCPClient

        cfg = MCPServerConfig(
            name="slow_tool",
            command="echo",
            tool_timeout=0.01,
        )
        client = MCPClient(cfg)

        class SlowSession:
            async def call_tool(self, name: str, arguments: object) -> object:
                del name, arguments
                await asyncio.sleep(1)
                return object()

        client._session = SlowSession()  # type: ignore[assignment]
        with pytest.raises(TimeoutError):
            await client.call_tool("slow", {})


# ===========================================================================
# TestDeferredSchemaEnvelope – get_schema envelope validation
# ===========================================================================


class TestDeferredSchemaEnvelope:
    def test_get_schema_full_envelope(self) -> None:
        """get_schema() must return {name, description, input_schema} envelope."""
        from mewcode.mcp.tool_wrapper import MCPToolWrapper

        input_schema = {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["repo"],
        }
        mgr = _make_manager("gh")
        tool_def = _make_tool_def("list_prs", "List pull requests", input_schema)
        wrapper = MCPToolWrapper(manager=mgr, server_name="gh", tool_def=tool_def)

        schema = wrapper.get_schema()
        assert "name" in schema
        assert "description" in schema
        assert "input_schema" in schema
        # input_schema must be the raw MCP schema, not a pydantic-derived one
        assert schema["input_schema"] is wrapper._input_schema
        assert schema["input_schema"]["properties"]["repo"]["type"] == "string"

    def test_schema_name_matches_wrapper_name(self) -> None:
        """Schema name must match the mcp_<server>_<tool> format."""
        from mewcode.mcp.tool_wrapper import MCPToolWrapper

        mgr = _make_manager("svc")
        tool_def = _make_tool_def("do_work", "Do work")
        wrapper = MCPToolWrapper(manager=mgr, server_name="svc", tool_def=tool_def)

        schema = wrapper.get_schema()
        assert schema["name"] == "mcp_svc_do_work"


# ===========================================================================
# TestStructuredContent – structuredContent fallback in execute
# ===========================================================================


class TestStructuredContent:
    @pytest.mark.asyncio
    async def test_structured_content_serialized_when_no_content(self) -> None:
        """When content is empty but structuredContent exists, serialize as JSON."""
        import json

        from mewcode.mcp.tool_wrapper import MCPToolWrapper

        mgr = _make_manager("svc2")
        tool_def = _make_tool_def("analyze")
        wrapper = MCPToolWrapper(manager=mgr, server_name="svc2", tool_def=tool_def)

        fake_result = MagicMock()
        fake_result.content = []
        fake_result.structuredContent = {"status": "ok", "count": 3}
        fake_result.isError = False

        mock_client = MagicMock()
        mock_client.call_tool = AsyncMock(return_value=fake_result)
        mgr.get_client.return_value = mock_client

        params = wrapper.params_model(query="q")
        result = await wrapper.execute(params)
        assert not result.is_error
        parsed = json.loads(result.output)
        assert parsed["status"] == "ok"


# ===========================================================================
# TestExtractTextEmbeddedResource – EmbeddedResource text extraction
# ===========================================================================


class TestExtractTextEmbeddedResource:
    def test_embedded_resource_with_text_attribute(self) -> None:
        """EmbeddedResource whose inner resource has .text should extract that text."""
        from mcp import types

        from mewcode.mcp.tool_wrapper import _extract_text

        # Build a TextResourceContents with text and a URI
        resource = types.TextResourceContents(
            uri="file:///example.txt",
            text="resource body text",
        )
        block = types.EmbeddedResource(type="resource", resource=resource)
        result = _extract_text([block])
        assert "resource body text" in result

    def test_build_child_env_excludes_anthropic_api_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ANTHROPIC_API_KEY must never leak into the child process env."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
        monkeypatch.setenv("PATH", "/usr/bin")
        env = build_child_env()
        assert "ANTHROPIC_API_KEY" not in env
# ---------------------------------------------------------------------------
# Lazy registration state and idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lazy_registration_is_idempotent_for_concurrent_callers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mewcode.mcp.manager as manager_module

    class LazyClient:
        connect_calls = 0

        def __init__(self, config: MCPServerConfig) -> None:
            self.config = config
            self.is_alive = False

        async def connect(self) -> None:
            type(self).connect_calls += 1
            await asyncio.sleep(0.02)
            self.is_alive = True

        async def list_tools(self) -> list[Any]:
            return [_make_tool_def("lookup")]

        async def close(self) -> None:
            self.is_alive = False

    monkeypatch.setattr(manager_module, "MCPClient", LazyClient)
    manager = manager_module.MCPManager()
    manager.load_configs([MCPServerConfig(name="ctx", command="fake")])
    registry = ToolRegistry()

    first, second = await asyncio.gather(
        manager.register_all_tools(registry),
        manager.register_all_tools(registry),
    )

    assert first == []
    assert second == []
    assert LazyClient.connect_calls == 1
    assert [tool.name for tool in registry.list_tools()] == ["mcp_ctx_lookup"]
    assert str(manager.server_states["ctx"]) == "connected"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_failed_lazy_server_requires_explicit_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import mewcode.mcp.manager as manager_module

    class FlakyClient:
        connect_calls = 0

        def __init__(self, config: MCPServerConfig) -> None:
            self.config = config
            self.is_alive = False

        async def connect(self) -> None:
            type(self).connect_calls += 1
            if type(self).connect_calls == 1:
                raise OSError("temporary failure")
            self.is_alive = True

        async def list_tools(self) -> list[Any]:
            return [_make_tool_def("lookup")]

        async def close(self) -> None:
            self.is_alive = False

    monkeypatch.setattr(manager_module, "MCPClient", FlakyClient)
    manager = manager_module.MCPManager()
    manager.load_configs([MCPServerConfig(name="ctx", command="fake")])
    registry = ToolRegistry()

    errors = await manager.register_all_tools(registry)
    assert errors and str(manager.server_states["ctx"]) == "failed"

    assert await manager.register_all_tools(registry) == []
    assert FlakyClient.connect_calls == 1

    assert await manager.register_all_tools(registry, retry_failed=True) == []
    assert FlakyClient.connect_calls == 2
    assert str(manager.server_states["ctx"]) == "connected"
