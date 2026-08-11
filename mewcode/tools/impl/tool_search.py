"""Deferred tool discovery exposed as a regular model tool."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar, cast

from pydantic import BaseModel, Field

from mewcode.tools import ToolRegistry
from mewcode.tools.base import Tool, ToolResult


class ToolSearchParams(BaseModel):
    query: str = Field(min_length=1, description="Keywords or select:Tool1,Tool2")
    max_results: int = Field(default=5, ge=1, le=20)


def _strip_titles(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_titles(item) for key, item in value.items() if key != "title"}
    if isinstance(value, list):
        return [_strip_titles(item) for item in value]
    return value


class ToolSearchTool(Tool):
    name = "ToolSearch"
    description = "Discover deferred tools by keyword or exact names."
    params_model: ClassVar[type[BaseModel]] = ToolSearchParams
    category = "read"
    is_concurrency_safe = True
    should_defer = False

    def __init__(
        self,
        registry: ToolRegistry,
        protocol: str = "anthropic",
        external_initializer: Callable[[bool], Awaitable[None]] | None = None,
    ) -> None:
        self.registry = registry
        self.protocol = protocol
        self.external_initializer = external_initializer

    def get_schema(self) -> dict[str, Any]:
        return cast(dict[str, Any], _strip_titles(super().get_schema()))

    async def execute(self, params: ToolSearchParams) -> ToolResult:
        query = params.query.strip()
        retry_failed = query.casefold().startswith("retry:")
        if retry_failed:
            query = query[6:].strip() or "mcp"

        def search() -> list[dict[str, Any]]:
            if query.casefold().startswith("select:"):
                names = [name.strip() for name in query[7:].split(",") if name.strip()]
                return self.registry.find_deferred_by_names(names, self.protocol)
            return self.registry.search_deferred(
                query,
                max_results=params.max_results,
                protocol=self.protocol,
            )

        schemas = [] if retry_failed else search()
        if not schemas and self.external_initializer is not None:
            await self.external_initializer(retry_failed)
            schemas = search()
        if not schemas:
            available = ", ".join(self.registry.get_deferred_tool_names()) or "none"
            return ToolResult(f'No matching deferred tools for "{query}". Available: {available}')
        for schema in schemas:
            self.registry.mark_discovered(str(schema["name"]))
        payload = json.dumps(schemas, ensure_ascii=False, indent=2)
        return ToolResult(f"Found {len(schemas)} tool(s):\n{payload}")


__all__ = ["ToolSearchParams", "ToolSearchTool"]
