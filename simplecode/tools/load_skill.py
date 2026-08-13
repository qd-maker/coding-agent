"""System tool for progressively disclosing and activating a Skill SOP."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from simplecode.skills.directory import register_skill_tools
from simplecode.skills.executor import SkillDependencyError, validate_skill_dependencies
from simplecode.tools.base import Tool, ToolResult

if TYPE_CHECKING:
    from simplecode.agent import Agent
    from simplecode.skills.loader import SkillLoader


class LoadSkillParams(BaseModel):
    name: str = Field(min_length=1, description="Exact Skill name from Available Skills")


class LoadSkill(Tool):
    name = "LoadSkill"
    description = (
        "Load and activate the complete SOP for one Skill from the Available Skills catalog. "
        "Use this when the user's intent matches a Skill description."
    )
    params_model = LoadSkillParams
    category = "read"
    is_concurrency_safe = False
    is_system_tool = True
    is_plan_safe = True

    def __init__(self) -> None:
        self._loader: SkillLoader | None = None
        self._agent: Agent | None = None

    def set_loader(self, loader: SkillLoader) -> None:
        self._loader = loader

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent

    async def execute(self, params: Any) -> ToolResult:
        if self._loader is None or self._agent is None:
            return ToolResult("LoadSkill not properly initialized.", is_error=True)
        requested = str(params.name).strip().casefold()
        skill = self._loader.get(requested)
        if skill is None:
            available = ", ".join(name for name, _ in self._loader.get_catalog()) or "none"
            return ToolResult(
                f"Unknown Skill '{requested}'. Available Skills: {available}.",
                is_error=True,
            )

        registered = 0
        if skill.is_directory and skill.source_path is not None:
            registered = register_skill_tools(skill.source_path.parent, self._agent.registry)
        try:
            validate_skill_dependencies(
                [skill],
                self._agent.registry,
                allow_declared_directory_tools=False,
            )
        except SkillDependencyError as exc:
            return ToolResult(str(exc), is_error=True)

        self._agent.activate_skill(skill.name, skill.prompt_body, skill.allowed_tools)
        self._agent.recovery_state.record_skill_invocation(skill.name, skill.prompt_body)
        suffix = f" {registered} specialized tool(s) registered." if registered else ""
        return ToolResult(
            f"Skill '{skill.name}' activated. SOP pinned to environment context.{suffix}"
        )


__all__ = ["LoadSkill", "LoadSkillParams"]
