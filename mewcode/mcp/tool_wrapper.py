"""MCP tool wrapper: adapts MCP tools to the MewCode Tool base class."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from mcp import types
from pydantic import BaseModel, create_model

from mewcode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from mewcode.mcp.manager import MCPManager

_TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_INVALID_TOOL_NAME_CHARS = re.compile(r"[^A-Za-z0-9_]")


def _normalize_tool_name(name: str) -> str:
    """Map an MCP tool name to the provider-safe MewCode tool namespace."""
    normalized = _INVALID_TOOL_NAME_CHARS.sub("_", name)
    if not normalized or not _TOOL_NAME_RE.fullmatch(normalized):
        raise ValueError(f"MCP tool name {name!r} cannot be normalized safely")
    return normalized


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------


def _json_type_to_python(json_type: str) -> type:
    """Map a JSON Schema primitive type name to a Python type."""
    mapping: dict[str, type] = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "object": dict,
        "array": list,
        "null": type(None),
    }
    return mapping.get(json_type, Any)


def _build_params_model(tool_name: str, input_schema: dict[str, Any]) -> type[BaseModel]:
    """Dynamically build a Pydantic BaseModel from a JSON Schema object.

    Covers object/array/string/integer/number/boolean/null JSON types.
    extra='forbid' is set so the registry's validate-before-execute catches typos.
    """
    from pydantic import ConfigDict

    properties: dict[str, Any] = input_schema.get("properties") or {}
    required_fields: set[str] = set(input_schema.get("required") or [])

    field_definitions: dict[str, Any] = {}

    for field_name, field_schema in properties.items():
        raw_type = field_schema.get("type", "string")
        if isinstance(raw_type, list):
            types_list = [_json_type_to_python(str(item)) for item in raw_type]
            non_null_types = [item for item in types_list if item is not type(None)]
            if len(non_null_types) == 1 and len(non_null_types) != len(types_list):
                python_type: Any = non_null_types[0] | None
            else:
                python_type = types_list[0] if len(types_list) == 1 else Any
        else:
            python_type = _json_type_to_python(str(raw_type))

        if field_name in required_fields:
            field_definitions[field_name] = (python_type, ...)
        else:
            field_definitions[field_name] = (python_type | None, None)

    model_name = f"{tool_name}Params"

    return create_model(
        model_name,
        __config__=ConfigDict(extra="forbid"),
        **field_definitions,
    )


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def _extract_text(content: list[Any]) -> str:
    """Convert MCP content blocks to a plain string."""
    if not content:
        return "(no output)"

    parts: list[str] = []
    for block in content:
        if isinstance(block, types.TextContent):
            parts.append(block.text)
        elif isinstance(block, types.ImageContent):
            parts.append(f"[image/{block.mimeType}]")
        elif isinstance(block, types.EmbeddedResource):
            resource = block.resource
            if hasattr(resource, "text"):
                parts.append(resource.text)
            elif hasattr(resource, "uri"):
                parts.append(f"[resource: {resource.uri}]")
            else:
                parts.append("[embedded resource]")
        else:
            # Fallback for unknown block types
            text = getattr(block, "text", None)
            if text is not None:
                parts.append(str(text))
            else:
                parts.append(str(block))

    result = "\n".join(parts)
    return result if result else "(no output)"


# ---------------------------------------------------------------------------
# MCPToolWrapper
# ---------------------------------------------------------------------------


class MCPToolWrapper(Tool):
    """Adapts a single MCP tool definition to the MewCode Tool interface."""

    # MCPToolWrapper uses instance attributes instead of ClassVar because each
    # wrapper carries a different name/description/params_model.

    def __init__(
        self,
        *,
        manager: MCPManager,
        server_name: str,
        tool_def: types.Tool,
    ) -> None:
        normalized_tool_name = _normalize_tool_name(tool_def.name)
        wrapper_name = f"mcp_{server_name}_{normalized_tool_name}"
        if not _TOOL_NAME_RE.fullmatch(wrapper_name):
            raise ValueError(f"Derived MCP tool name {wrapper_name!r} is invalid")

        # Override ClassVar attributes with instance values (allowed in Python)
        self.name = wrapper_name  # type: ignore[misc]
        self.description = tool_def.description or f"MCP tool {wrapper_name}"  # type: ignore[misc]
        self.category = "command"  # type: ignore[misc]
        self.should_defer = True  # type: ignore[misc]
        self.is_concurrency_safe = False  # type: ignore[misc]
        self.is_system_tool = False  # type: ignore[misc]
        self.is_plan_safe = False  # type: ignore[misc]
        self.plan_mode_only = False  # type: ignore[misc]

        self._manager = manager
        self._server_name = server_name
        self._tool_def = tool_def
        self._input_schema: dict[str, Any] = dict(tool_def.inputSchema)

        self.params_model = _build_params_model(  # type: ignore[misc]
            tool_def.name, self._input_schema
        )

    def get_schema(self) -> dict[str, Any]:
        """Return the MCP-native input schema (not the Pydantic-derived one)."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._input_schema,
        }

    async def execute(self, params: Any) -> ToolResult:
        """Execute via MCPManager (supports lazy reconnect)."""
        import asyncio

        try:
            client = await self._manager.get_client(self._server_name)
        except Exception as exc:
            return ToolResult(
                output=f"MCP connection error for {self._server_name!r}: {exc}",
                is_error=True,
            )

        arguments: dict[str, Any]
        if isinstance(params, BaseModel):
            arguments = params.model_dump(exclude_none=True)
        elif isinstance(params, dict):
            arguments = params
        else:
            arguments = {}

        try:
            result = await client.call_tool(self._tool_def.name, arguments)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult(
                output=f"MCP tool call failed: {exc}",
                is_error=True,
            )

        # Handle structuredContent text fallback
        output: str
        if result.content:
            output = _extract_text(list(result.content))
        elif result.structuredContent is not None:
            import json

            output = json.dumps(result.structuredContent)
        else:
            output = "(no output)"

        return ToolResult(output=output, is_error=bool(result.isError))
