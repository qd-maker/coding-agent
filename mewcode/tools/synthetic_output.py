"""Coordinator-safe final structured output channel."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from mewcode.tools.base import Tool, ToolResult


class SyntheticOutputParams(BaseModel):
    output: str = Field(min_length=1)


class SyntheticOutputTool(Tool):
    name = "SyntheticOutput"
    description = "Return the Lead's final synthesized result without filesystem access."
    params_model: ClassVar[type[BaseModel]] = SyntheticOutputParams
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: SyntheticOutputParams) -> ToolResult:
        return ToolResult(params.output)


__all__ = ["SyntheticOutputParams", "SyntheticOutputTool"]
