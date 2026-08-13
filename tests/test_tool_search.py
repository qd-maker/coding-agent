"""Deferred tool discovery and AskUserQuestion tests."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from pydantic import BaseModel

from simplecode.tools import ToolRegistry
from simplecode.tools.ask_user import AskUserParams, AskUserTool, QuestionItem
from simplecode.tools.base import Tool, ToolResult
from simplecode.tools.impl import ToolSearchParams, ToolSearchTool


class Params(BaseModel):
    value: str = ""


class DeferredTool(Tool):
    name = "DeferredDemo"
    description = "Review deployment configuration"
    params_model: ClassVar[type[BaseModel]] = Params
    category = "read"
    should_defer = True

    async def execute(self, params: Params) -> ToolResult:
        return ToolResult(params.value)


@pytest.mark.asyncio
async def test_tool_search_discovers_by_keyword() -> None:
    registry = ToolRegistry()
    registry.register(DeferredTool())
    search = ToolSearchTool(registry)
    registry.register(search)

    assert registry.get_deferred_tool_names() == ["DeferredDemo"]
    assert [schema["name"] for schema in registry.get_all_schemas()] == ["ToolSearch"]
    result = await search.execute(ToolSearchParams(query="deployment"))
    assert "Found 1 tool" in result.output
    assert registry.is_discovered("DeferredDemo")
    assert {schema["name"] for schema in registry.get_all_schemas()} == {
        "DeferredDemo",
        "ToolSearch",
    }


@pytest.mark.asyncio
async def test_tool_search_exact_selection_and_no_match() -> None:
    registry = ToolRegistry()
    registry.register(DeferredTool())
    search = ToolSearchTool(registry, protocol="openai")
    selected = await search.execute(ToolSearchParams(query="select:DeferredDemo"))
    missing = await search.execute(ToolSearchParams(query="nothing"))
    assert '"type": "function"' in selected.output
    assert "No matching deferred tools" in missing.output


@pytest.mark.asyncio
async def test_ask_user_waits_for_tui_result() -> None:
    tool = AskUserTool()
    params = AskUserParams(
        questions=[QuestionItem(name="choice", message="Choose", options=["A", "B"])]
    )
    task = asyncio.create_task(tool.execute(params))
    for _ in range(20):
        if tool.pending_event is not None:
            break
        await asyncio.sleep(0)
    assert tool.pending_event is not None
    tool.pending_event.future.set_result({"choice": "B"})
    result = await task
    assert result.output == "choice: B"
    assert tool.pending_event is None
