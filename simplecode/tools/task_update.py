"""Update a task on a shared team board."""

from __future__ import annotations

import json
from typing import ClassVar, Literal

from pydantic import BaseModel, Field, model_validator

from simplecode.teams import TeamManager
from simplecode.tools.base import Tool, ToolResult

VALID_STATUSES = {"pending", "in_progress", "completed", "blocked"}


class TaskUpdateParams(BaseModel):
    task_id: str = Field(min_length=1)
    subject: str | None = Field(default=None, min_length=1)
    description: str | None = None
    status: Literal["pending", "in_progress", "completed", "blocked"] | None = None
    blocks: list[str] | None = None
    blocked_by: list[str] | None = None

    @model_validator(mode="after")
    def require_update(self) -> TaskUpdateParams:
        if all(
            value is None
            for value in (
                self.subject,
                self.description,
                self.status,
                self.blocks,
                self.blocked_by,
            )
        ):
            raise ValueError("at least one task field must be supplied")
        return self


class TaskUpdateTool(Tool):
    name = "TaskUpdate"
    description = "Update status, content, or dependencies of a shared team task."
    params_model: ClassVar[type[BaseModel]] = TaskUpdateParams
    category = "write"
    is_concurrency_safe = True

    def __init__(self, team_manager: TeamManager, team_name: str) -> None:
        self.team_manager = team_manager
        self.team_name = team_name

    async def execute(self, params: TaskUpdateParams) -> ToolResult:
        updates = params.model_dump(exclude={"task_id"}, exclude_none=True)
        task = self.team_manager.get_task_store(self.team_name).update(params.task_id, **updates)
        if task is None:
            return ToolResult(f"Task {params.task_id!r} was not found", is_error=True)
        return ToolResult(json.dumps(task.as_dict(), ensure_ascii=False))


__all__ = ["TaskUpdateParams", "TaskUpdateTool", "VALID_STATUSES"]
