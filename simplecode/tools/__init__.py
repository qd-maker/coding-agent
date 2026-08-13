"""Tool registry, schema conversion, and built-in tool assembly."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from simplecode.tools.base import (
    MAX_OUTPUT_CHARS,
    SKIP_DIRS,
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    Tool,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)


def _schema_for_protocol(schema: dict[str, Any], protocol: str) -> dict[str, Any]:
    if protocol != "openai":
        return schema
    return {
        "type": "function",
        "name": schema["name"],
        "description": schema.get("description", ""),
        "parameters": schema.get("input_schema", {"type": "object", "properties": {}}),
    }


class ToolRegistry:
    """Mutable-at-assembly registry and safe execution boundary."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._disabled: set[str] = set()
        self._discovered: set[str] = set()
        self._plan_mode = False

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        if name in self._disabled:
            return None
        tool = self._tools.get(name)
        if tool is not None and tool.plan_mode_only and not self._plan_mode:
            return None
        return tool

    def is_enabled(self, name: str) -> bool:
        tool = self._tools.get(name)
        return bool(
            tool is not None
            and name not in self._disabled
            and (self._plan_mode or not tool.plan_mode_only)
        )

    def set_plan_mode(self, enabled: bool) -> None:
        """Restrict model-visible schemas without changing explicit enable state."""
        self._plan_mode = enabled

    def set_work_dir(self, work_dir: str | Path) -> None:
        """Retarget work-directory-aware tools after a worktree transition."""

        resolved = Path(work_dir).resolve()
        for tool in self._tools.values():
            setter = getattr(tool, "set_work_dir", None)
            if callable(setter):
                setter(resolved)

    def _is_schema_visible(self, tool: Tool) -> bool:
        if not self.is_enabled(tool.name):
            return False
        if self._plan_mode:
            return tool.is_read_only or tool.is_plan_safe
        return not tool.plan_mode_only

    def is_model_visible(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and self._is_schema_visible(tool)

    def enable(self, name: str) -> None:
        self._require_name(name)
        self._disabled.discard(name)

    def disable(self, name: str) -> None:
        self._require_name(name)
        self._disabled.add(name)

    def enable_all(self) -> None:
        self._disabled.clear()

    def mark_discovered(self, name: str) -> None:
        self._require_name(name)
        self._discovered.add(name)

    def is_discovered(self, name: str) -> bool:
        return name in self._discovered

    def get_deferred_tool_names(self) -> list[str]:
        return [
            tool.name
            for tool in self._tools.values()
            if tool.should_defer
            and self._is_schema_visible(tool)
            and not self.is_discovered(tool.name)
        ]

    def search_deferred(
        self,
        query: str,
        max_results: int = 5,
        protocol: str = "anthropic",
    ) -> list[dict[str, Any]]:
        query_lower = query.casefold().strip()
        words = [word for word in query_lower.split() if word]
        scored: list[tuple[int, str, Tool]] = []
        for tool in self._tools.values():
            if not tool.should_defer or not self._is_schema_visible(tool):
                continue
            name_lower = tool.name.casefold()
            description_lower = tool.description.casefold()
            score = 0
            if query_lower and query_lower in name_lower:
                score += 10
            if query_lower and query_lower in description_lower:
                score += 5
            for word in words:
                if word in name_lower:
                    score += 3
                if word in description_lower:
                    score += 1
            if score:
                scored.append((score, tool.name, tool))
        scored.sort(key=lambda item: (-item[0], item[1].casefold()))
        return [
            _schema_for_protocol(tool.get_schema(), protocol)
            for _, _, tool in scored[: max(0, max_results)]
        ]

    def find_deferred_by_names(
        self,
        names: list[str],
        protocol: str = "anthropic",
    ) -> list[dict[str, Any]]:
        wanted = {name.casefold() for name in names}
        return [
            _schema_for_protocol(tool.get_schema(), protocol)
            for tool in self._tools.values()
            if tool.name.casefold() in wanted
            and tool.should_defer
            and self._is_schema_visible(tool)
        ]

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def get_all_schemas(self, protocol: str = "anthropic") -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if not self._is_schema_visible(tool):
                continue
            if tool.should_defer and not self.is_discovered(tool.name):
                continue
            schemas.append(_schema_for_protocol(tool.get_schema(), protocol))
        return schemas

    async def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        truncate: bool = True,
    ) -> ToolResult:
        tool = self.get(name)
        if tool is None:
            state = "disabled" if name in self._tools else "unknown"
            return ToolResult(f"Error: tool {name!r} is {state}.", is_error=True)
        try:
            params = tool.params_model.model_validate(arguments)
        except ValidationError as exc:
            return ToolResult(
                f"Error: invalid arguments for {name}: {exc}",
                is_error=True,
            )
        try:
            if tool.execution_timeout is None:
                result = await tool.execute(params)
            else:
                result = await asyncio.wait_for(
                    tool.execute(params),
                    timeout=tool.execution_timeout,
                )
        except TimeoutError:
            return ToolResult(
                f"Error: tool {name} timed out after {tool.execution_timeout:g}s",
                is_error=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ToolResult(
                f"Error: tool {name} failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )
        if not isinstance(result, ToolResult):
            return ToolResult(
                f"Error: tool {name} returned {type(result).__name__}, expected ToolResult",
                is_error=True,
            )
        if not truncate or len(result.output) <= MAX_OUTPUT_CHARS:
            return result
        marker = f"\n\n[output truncated from {len(result.output)} characters]"
        output = result.output[: MAX_OUTPUT_CHARS - len(marker)]
        return ToolResult(
            f"{output}{marker}",
            result.is_error,
            data=result.data,
            preview=result.preview,
            artifact_path=result.artifact_path,
            exit_code=result.exit_code,
            diagnostics=result.diagnostics,
        )

    def _require_name(self, name: str) -> None:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")


def create_default_registry(
    file_cache: Any | None = None,
    work_dir: str | Path | None = None,
) -> ToolRegistry:
    from simplecode.tools.bash import Bash
    from simplecode.tools.edit_file import EditFile
    from simplecode.tools.glob import Glob
    from simplecode.tools.grep import Grep
    from simplecode.tools.read_file import ReadFile
    from simplecode.tools.write_file import WriteFile

    registry = ToolRegistry()
    registry.register(ReadFile(file_cache=file_cache, work_dir=work_dir))
    registry.register(WriteFile(file_cache=file_cache, work_dir=work_dir))
    registry.register(EditFile(file_cache=file_cache, work_dir=work_dir))
    registry.register(Bash(work_dir=work_dir))
    registry.register(Glob(work_dir=work_dir))
    registry.register(Grep(work_dir=work_dir))
    return registry


def register_task_tools(registry: ToolRegistry, team_manager: Any, team_name: str) -> None:
    """Register the four shared task-board tools for a team-enabled Agent."""
    from simplecode.tools.task_create import TaskCreateTool
    from simplecode.tools.task_get import TaskGetTool
    from simplecode.tools.task_list import TaskListTool
    from simplecode.tools.task_update import TaskUpdateTool

    registry.register(TaskCreateTool(team_manager, team_name))
    registry.register(TaskGetTool(team_manager, team_name))
    registry.register(TaskListTool(team_manager, team_name))
    registry.register(TaskUpdateTool(team_manager, team_name))


__all__ = [
    "MAX_OUTPUT_CHARS",
    "SKIP_DIRS",
    "StreamEnd",
    "StreamEvent",
    "TextDelta",
    "ThinkingComplete",
    "ThinkingDelta",
    "Tool",
    "ToolCallComplete",
    "ToolCallDelta",
    "ToolCallStart",
    "ToolRegistry",
    "ToolResult",
    "create_default_registry",
    "register_task_tools",
]
