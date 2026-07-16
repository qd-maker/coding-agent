"""List tasks from a shared team board."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel

from mewcode.teams import TeamManager
from mewcode.tools.base import Tool, ToolResult


class TaskListParams(BaseModel):
    status: Literal["pending", "in_progress", "completed", "blocked"] | None = None


class TaskListTool(Tool):
    name = "TaskList"
    description = "List tasks on the shared team task board, optionally filtered by status."
    params_model: ClassVar[type[BaseModel]] = TaskListParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self, team_manager: TeamManager, team_name: str) -> None:
        self.team_manager = team_manager
        self.team_name = team_name

    async def execute(self, params: TaskListParams) -> ToolResult:
        icons = {"pending": "○", "in_progress": "◐", "completed": "●", "blocked": "✕"}
        tasks = self.team_manager.get_task_store(self.team_name).list_tasks(params.status)
        if not tasks:
            return ToolResult("No tasks found.")
        lines = [
            f"{icons[task.status]} #{task.id} [{task.status}] {task.subject}" for task in tasks
        ]
        return ToolResult("\n".join(lines))


__all__ = ["TaskListParams", "TaskListTool"]
