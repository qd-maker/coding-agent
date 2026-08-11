"""Directory-style Skill tools loaded from tool.json and references/*.py."""

from __future__ import annotations

import importlib.util
import inspect
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict

from mewcode.tools import ToolRegistry
from mewcode.tools.base import Tool, ToolResult

log = logging.getLogger(__name__)
_SAFE_MODULE_PART = re.compile(r"[^a-zA-Z0-9_]")


def parse_tool_json(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        return []
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        log.warning("Cannot parse Skill tool schema %s: %s", source, exc)
        return []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        log.warning("Skill tool schema %s must contain an object or list", source)
        return []
    return [dict(item) for item in payload if isinstance(item, dict)]


def load_tool_implementation(
    references_dir: str | Path,
    tool_name: str,
) -> Callable[..., Any] | None:
    source = Path(references_dir) / f"{tool_name}.py"
    if not source.is_file():
        log.warning("Skill tool implementation is missing: %s", source)
        return None
    module_name = "mewcode_skill_tool_" + _SAFE_MODULE_PART.sub("_", tool_name)
    try:
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create module spec for {source}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        implementation = getattr(module, "execute", None)
        if not callable(implementation):
            raise AttributeError("module does not define callable execute")
        return cast(Callable[..., Any], implementation)
    except Exception as exc:  # noqa: BLE001 - one optional tool must not break catalog loading
        log.warning("Cannot load Skill tool implementation %s: %s", source, exc)
        return None


class _DynamicParams(BaseModel):
    model_config = ConfigDict(extra="allow")


class SkillCustomTool(Tool):
    """Tool adapter backed by a Python function from a Skill directory."""

    def __init__(
        self,
        tool_name: str,
        description: str,
        schema: dict[str, Any],
        implementation: Callable[..., Any],
    ) -> None:
        self.name = tool_name  # type: ignore[misc]
        self.description = description  # type: ignore[misc]
        self.params_model = _DynamicParams  # type: ignore[misc]
        self.category = "read"  # type: ignore[misc]
        self.is_concurrency_safe = False  # type: ignore[misc]
        self._input_schema = dict(
            schema.get("parameters")
            or schema.get("input_schema")
            or {"type": "object", "properties": {}}
        )
        self._implementation = implementation

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self._input_schema,
        }

    async def execute(self, params: Any) -> ToolResult:
        kwargs = params.model_dump() if isinstance(params, BaseModel) else dict(params)
        try:
            result = self._implementation(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            return ToolResult(str(result))
        except Exception as exc:  # noqa: BLE001 - dynamic tools return structured failures
            return ToolResult(
                f"Skill tool {self.name} failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )


def declared_skill_tool_names(skill_dir: str | Path) -> set[str]:
    return {
        str(schema.get("name"))
        for schema in parse_tool_json(Path(skill_dir) / "tool.json")
        if isinstance(schema.get("name"), str) and str(schema["name"]).strip()
    }


def register_skill_tools(skill_dir: str | Path, registry: ToolRegistry) -> int:
    root = Path(skill_dir)
    count = 0
    registered = {tool.name for tool in registry.list_tools()}
    for schema in parse_tool_json(root / "tool.json"):
        name = schema.get("name")
        if not isinstance(name, str) or not name.strip() or name in registered:
            continue
        implementation = load_tool_implementation(root / "references", name)
        if implementation is None:
            continue
        description = schema.get("description")
        tool = SkillCustomTool(
            name,
            str(description) if description else f"Skill tool {name}",
            schema,
            implementation,
        )
        registry.register(tool)
        registered.add(name)
        count += 1
    return count


__all__ = [
    "SkillCustomTool",
    "declared_skill_tool_names",
    "load_tool_implementation",
    "parse_tool_json",
    "register_skill_tools",
]
