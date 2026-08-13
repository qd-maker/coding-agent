"""Explicitly stop one team member."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from simplecode.teams.manager import TeamError
from simplecode.tools.base import Tool, ToolResult


class TeamStopParams(BaseModel):
    member: str = Field(min_length=1)
    team_name: str = ""


class TeamStopTool(Tool):
    name = "TeamStop"
    description = "Cancel or terminate one teammate while retaining its persisted team record."
    params_model: ClassVar[type[BaseModel]] = TeamStopParams
    category = "command"

    def __init__(self, team_manager: Any, parent_agent: Any) -> None:
        self.team_manager = team_manager
        self.parent_agent = parent_agent

    async def execute(self, params: TeamStopParams) -> ToolResult:
        team_name = params.team_name or getattr(self.parent_agent, "team_name", "")
        try:
            await self.team_manager.stop_member(team_name, params.member)
        except TeamError as exc:
            return ToolResult(str(exc), is_error=True)
        return ToolResult(f"Stopped teammate {params.member!r}.")


__all__ = ["TeamStopParams", "TeamStopTool"]
