"""Plan Mode-only writer restricted to the Agent's current plan file."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from mewcode.tools.base import Tool, ToolResult


class WritePlanParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, description="Complete Markdown plan content")


class WritePlanTool(Tool):
    name = "WritePlan"
    description = "Save the complete Markdown implementation plan."
    params_model: ClassVar[type[BaseModel]] = WritePlanParams
    category = "write"
    is_system_tool = True
    is_plan_safe = True
    plan_mode_only = True

    def __init__(self, plan_path: str | Path) -> None:
        self.plan_path = Path(plan_path).resolve()

    def get_schema(self) -> dict[str, Any]:
        schema = super().get_schema()
        schema["description"] = (
            "Save the complete Plan Mode Markdown document to the only permitted plan file: "
            f"{self.plan_path}. This tool accepts content only; no path can be supplied."
        )
        return schema

    async def execute(self, params: WritePlanParams) -> ToolResult:
        self.plan_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.plan_path.with_suffix(self.plan_path.suffix + ".tmp")
        temporary.write_text(params.content, encoding="utf-8")
        temporary.replace(self.plan_path)
        return ToolResult(f"Plan saved to {self.plan_path} ({len(params.content)} characters).")


__all__ = ["WritePlanParams", "WritePlanTool"]
