"""Create a persistent Agent team and activate Lead-only collaboration tools."""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field

from simplecode.agents.tool_filter import apply_coordinator_filter
from simplecode.teams.coordinator import is_coordinator_mode
from simplecode.tools.base import Tool, ToolResult

TEAM_RUNTIME_TOOLS = {
    "SendMessage",
    "TaskCreate",
    "TaskGet",
    "TaskList",
    "TaskUpdate",
    "TeamDelete",
    "TeamMerge",
    "TeamStop",
}


class TeamCreateParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=63, alias="team_name")
    description: str = ""


class TeamCreateTool(Tool):
    name = "TeamCreate"
    description = "Create a persistent team before spawning Agent teammates."
    params_model: ClassVar[type[BaseModel]] = TeamCreateParams
    category = "write"

    def __init__(
        self,
        team_manager: Any,
        parent_agent: Any,
        teammate_mode: str = "",
        *,
        is_interactive: bool = True,
        enable_coordinator_mode: bool = False,
    ) -> None:
        self.team_manager = team_manager
        self.parent_agent = parent_agent
        self.teammate_mode = teammate_mode
        self.is_interactive = is_interactive
        self.enable_coordinator_mode = enable_coordinator_mode

    async def execute(self, params: TeamCreateParams) -> ToolResult:
        try:
            team = self.team_manager.create_team(
                params.name,
                self.parent_agent.agent_id,
                description=params.description,
                teammate_mode=self.teammate_mode,
                is_interactive=self.is_interactive,
            )
        except Exception as exc:  # noqa: BLE001 - configuration failures are model-visible
            return ToolResult(f"Cannot create team: {exc}", is_error=True)
        self.parent_agent.team_name = team.name
        registry = self.parent_agent.registry
        for name in TEAM_RUNTIME_TOOLS:
            try:
                registry.enable(name)
            except KeyError:
                continue
        if is_coordinator_mode(self.enable_coordinator_mode):
            self.parent_agent.coordinator_mode = True
            self.parent_agent._full_registry = registry
            self.parent_agent.registry = apply_coordinator_filter(registry)
            suffix = " Coordinator Mode activated (pure scheduling; no code or shell tools)."
        else:
            suffix = ""
        backend = self.team_manager.detect_backend(
            self.teammate_mode,
            is_interactive=self.is_interactive,
        )
        return ToolResult(
            f"Team {team.name!r} created with {backend.value} backend.{suffix} "
            "Use Agent with team_name to add members."
        )


__all__ = ["TEAM_RUNTIME_TOOLS", "TeamCreateParams", "TeamCreateTool"]
