"""Delete an idle team and restore the normal Lead tool registry."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from mewcode.teams.manager import TeamError
from mewcode.tools.base import Tool, ToolResult
from mewcode.tools.team_create import TEAM_RUNTIME_TOOLS


class TeamDeleteParams(BaseModel):
    team_name: str = ""
    discard_worktrees: bool = False


class TeamDeleteTool(Tool):
    name = "TeamDelete"
    description = "Delete an idle team and optionally discard protected teammate worktrees."
    params_model: ClassVar[type[BaseModel]] = TeamDeleteParams
    category = "write"
    execution_timeout = None

    def __init__(self, team_manager: Any, parent_agent: Any) -> None:
        self.team_manager = team_manager
        self.parent_agent = parent_agent

    async def execute(self, params: TeamDeleteParams) -> ToolResult:
        team_name = params.team_name or getattr(self.parent_agent, "team_name", "")
        try:
            await self.team_manager.delete_team(
                team_name,
                discard=params.discard_worktrees,
            )
        except TeamError as exc:
            return ToolResult(str(exc), is_error=True)
        if getattr(self.parent_agent, "coordinator_mode", False):
            self.parent_agent.registry = self.parent_agent._full_registry
            self.parent_agent.coordinator_mode = False
            suffix = " Coordinator Mode deactivated."
        else:
            suffix = ""
        self.parent_agent.team_name = ""
        for name in TEAM_RUNTIME_TOOLS:
            try:
                self.parent_agent.registry.disable(name)
            except KeyError:
                continue
        return ToolResult(f"Team {team_name!r} deleted.{suffix}")


__all__ = ["TeamDeleteParams", "TeamDeleteTool"]
