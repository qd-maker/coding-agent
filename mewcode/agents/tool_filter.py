"""Layered least-privilege tool selection for child Agents."""

from __future__ import annotations

from pathlib import Path

from mewcode.agents.parser import AgentDefinition
from mewcode.cache import FileCache
from mewcode.teams.models import BackendType
from mewcode.tools import ToolRegistry, create_default_registry
from mewcode.tools.base import Tool
from mewcode.tools.impl import ToolSearchTool

ALL_AGENT_DISALLOWED_TOOLS = frozenset(
    {
        "TaskOutput",
        "ExitPlanMode",
        "EnterPlanMode",
        "Agent",
        "AskUserQuestion",
        "TaskStop",
        "Workflow",
        "EnterWorktree",
        "ExitWorktree",
        "TeamCreate",
        "TeamDelete",
        "TeamMerge",
        "TeamStop",
        "SendMessage",
    }
)
CUSTOM_AGENT_DISALLOWED_TOOLS = frozenset(
    {"TaskCreate", "TaskUpdate", "SendMessage", "CronCreate", "CronDelete", "CronList"}
)
ASYNC_AGENT_ALLOWED_TOOLS = frozenset(
    {
        "ReadFile",
        "WebSearch",
        "TodoWrite",
        "Grep",
        "WebFetch",
        "Glob",
        "Bash",
        "EditFile",
        "WriteFile",
        "NotebookEdit",
        "Skill",
        "LoadSkill",
        "SyntheticOutput",
        "ToolSearch",
        "EnterWorktree",
        "ExitWorktree",
    }
)
IN_PROCESS_TEAMMATE_ALLOWED_TOOLS = ASYNC_AGENT_ALLOWED_TOOLS | frozenset(
    {
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskUpdate",
        "SendMessage",
        "CronCreate",
        "CronDelete",
        "CronList",
    }
)
TEAMMATE_COORDINATION_TOOLS = frozenset(
    {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate", "SendMessage"}
)
# The Lead is deliberately unable to inspect or mutate code. The second lock lives in
# teams.coordinator.is_coordinator_mode; this set is only applied after both locks agree.
COORDINATOR_MODE_ALLOWED_TOOLS = frozenset(
    {
        "Agent",
        "SendMessage",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskUpdate",
        "TeamCreate",
        "TeamDelete",
        "TeamMerge",
        "TeamStop",
        "SyntheticOutput",
    }
)


def _is_mcp_tool(name: str) -> bool:
    return name.startswith(("mcp_", "mcp__"))


def _isolated_core_tools(work_dir: Path | None = None) -> dict[str, Tool]:
    """Create core tools with a child-local FileCache."""

    return {
        tool.name: tool
        for tool in create_default_registry(FileCache(), work_dir=work_dir).list_tools()
    }


def resolve_agent_tools(
    registry: ToolRegistry,
    definition: AgentDefinition | None = None,
    *,
    is_background: bool = False,
    work_dir: str | Path | None = None,
) -> ToolRegistry:
    """Apply MCP exemption, global/custom/background and definition restrictions."""

    custom = definition is not None and definition.source in {"project", "user", "plugin"}
    allowed_by_definition = set(definition.tools) if definition and definition.tools else None
    denied_by_definition = set(definition.disallowed_tools) if definition else set()
    isolated = _isolated_core_tools(Path(work_dir).resolve() if work_dir is not None else None)
    filtered = ToolRegistry()
    tool_search: ToolSearchTool | None = None
    for original in registry.list_tools():
        name = original.name
        if _is_mcp_tool(name):
            filtered.register(original)
            if original.should_defer and registry.is_discovered(name):
                filtered.mark_discovered(name)
            continue
        if name in ALL_AGENT_DISALLOWED_TOOLS:
            continue
        if custom and name in CUSTOM_AGENT_DISALLOWED_TOOLS:
            continue
        if is_background and name not in ASYNC_AGENT_ALLOWED_TOOLS:
            continue
        if name in denied_by_definition:
            continue
        if allowed_by_definition is not None and name not in allowed_by_definition:
            continue
        if isinstance(original, ToolSearchTool):
            tool_search = original
            continue
        tool = isolated.get(name, original)
        filtered.register(tool)
        if tool.should_defer and registry.is_discovered(name):
            filtered.mark_discovered(name)
    if tool_search is not None:
        filtered.register(ToolSearchTool(filtered, protocol=tool_search.protocol))
    return filtered


def _copy_selected(registry: ToolRegistry, allowed: set[str] | frozenset[str]) -> ToolRegistry:
    filtered = ToolRegistry()
    for tool in registry.list_tools():
        if tool.name not in allowed or not registry.is_enabled(tool.name):
            continue
        filtered.register(tool)
        if tool.should_defer and registry.is_discovered(tool.name):
            filtered.mark_discovered(tool.name)
    return filtered


def apply_coordinator_filter(registry: ToolRegistry) -> ToolRegistry:
    return _copy_selected(registry, COORDINATOR_MODE_ALLOWED_TOOLS)


def build_teammate_tools(
    registry: ToolRegistry,
    backend: BackendType,
    *,
    work_dir: str | Path | None = None,
) -> ToolRegistry:
    """Give only actual teammates the shared-board/message tools."""

    isolated = _isolated_core_tools(Path(work_dir).resolve() if work_dir else None)
    if backend is BackendType.IN_PROCESS:
        allowed = IN_PROCESS_TEAMMATE_ALLOWED_TOOLS | TEAMMATE_COORDINATION_TOOLS
        filtered = ToolRegistry()
        for original in registry.list_tools():
            if original.name in TEAMMATE_COORDINATION_TOOLS:
                continue
            if original.name not in allowed and not _is_mcp_tool(original.name):
                continue
            filtered.register(isolated.get(original.name, original))
        return filtered
    # A separate CLI process already owns isolated file tools. Only prevent it from
    # recursively creating or deleting the team itself.
    return _copy_selected(
        registry,
        {tool.name for tool in registry.list_tools()}
        - {
            "Agent",
            "TeamCreate",
            "TeamDelete",
            "TeamMerge",
            "TeamStop",
            "ToolSearch",
        },
    )


__all__ = [
    "ALL_AGENT_DISALLOWED_TOOLS",
    "ASYNC_AGENT_ALLOWED_TOOLS",
    "CUSTOM_AGENT_DISALLOWED_TOOLS",
    "IN_PROCESS_TEAMMATE_ALLOWED_TOOLS",
    "TEAMMATE_COORDINATION_TOOLS",
    "COORDINATOR_MODE_ALLOWED_TOOLS",
    "apply_coordinator_filter",
    "build_teammate_tools",
    "resolve_agent_tools",
]
