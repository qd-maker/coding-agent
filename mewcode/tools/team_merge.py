"""Lead-side transactional merge of idle teammate worktree branches."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel

from mewcode.teams.manager import TeamError
from mewcode.tools.base import Tool, ToolResult


class TeamMergeParams(BaseModel):
    team_name: str = ""


class TeamMergeTool(Tool):
    name = "TeamMerge"
    description = "Merge every idle teammate branch; roll back the whole operation on conflict."
    params_model: ClassVar[type[BaseModel]] = TeamMergeParams
    category = "command"
    execution_timeout = None

    def __init__(self, team_manager: Any, parent_agent: Any) -> None:
        self.team_manager = team_manager
        self.parent_agent = parent_agent

    async def execute(self, params: TeamMergeParams) -> ToolResult:
        team_name = params.team_name or getattr(self.parent_agent, "team_name", "")
        try:
            result = await self.team_manager.merge_team(team_name)
        except TeamError as exc:
            return ToolResult(str(exc), is_error=True)
        branches = ", ".join(result.merged_branches) or "(no teammate commits)"
        return ToolResult(
            f"Merged {branches}. HEAD {result.original_head[:10]} -> {result.final_head[:10]}."
        )


__all__ = ["TeamMergeParams", "TeamMergeTool"]
