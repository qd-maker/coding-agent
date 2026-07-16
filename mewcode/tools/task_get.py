"""Read one task from a shared team board."""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, Field

from mewcode.teams import TeamManager
from mewcode.tools.base import Tool, ToolResult


class TaskGetParams(BaseModel):
    task_id: str = Field(min_length=1)


class TaskGetTool(Tool):
    name = "TaskGet"
    description = "Get one task from the shared team task board."
    params_model: ClassVar[type[BaseModel]] = TaskGetParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, team_manager: TeamManager, team_name: str) -> None:
        self.team_manager = team_manager
        self.team_name = team_name

    async def execute(self, params: TaskGetParams) -> ToolResult:
        task = self.team_manager.get_task_store(self.team_name).get(params.task_id)
        if task is None:
            return ToolResult(f"Task {params.task_id!r} was not found", is_error=True)
        return ToolResult(json.dumps(task.as_dict(), ensure_ascii=False))


__all__ = ["TaskGetParams", "TaskGetTool"]
