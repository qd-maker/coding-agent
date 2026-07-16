"""Create a task on a shared team board."""

from __future__ import annotations

import json
from typing import ClassVar

from pydantic import BaseModel, Field

from mewcode.teams import TeamManager
from mewcode.tools.base import Tool, ToolResult


class TaskCreateParams(BaseModel):
    subject: str = Field(min_length=1)
    description: str = ""
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)


class TaskCreateTool(Tool):
    name = "TaskCreate"
    description = "Create a task on the shared team task board."
    params_model: ClassVar[type[BaseModel]] = TaskCreateParams
    category = "write"
    is_concurrency_safe = True

    def __init__(self, team_manager: TeamManager, team_name: str) -> None:
        self.team_manager = team_manager
        self.team_name = team_name

    async def execute(self, params: TaskCreateParams) -> ToolResult:
        task = self.team_manager.get_task_store(self.team_name).create(
            params.subject,
            params.description,
            params.blocks,
            params.blocked_by,
        )
        return ToolResult(json.dumps(task.as_dict(), ensure_ascii=False))


__all__ = ["TaskCreateParams", "TaskCreateTool"]
