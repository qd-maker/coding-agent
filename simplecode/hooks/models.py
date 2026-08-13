"""Core Hook data structures and context-template expansion."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from simplecode.hooks.conditions import Condition, ConditionGroup

_TEMPLATE_PATTERN = re.compile(
    r"\$TOOL_ARGS\.([A-Za-z_][A-Za-z0-9_.-]*)"
    r"|\$([A-Z][A-Z0-9_]*)"
)


@dataclass(slots=True)
class Action:
    type: str
    command: str = ""
    message: str = ""
    url: str = ""
    method: str = "POST"
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    prompt: str = ""
    timeout: int = 30


@dataclass(frozen=True, slots=True)
class ActionResult:
    output: str
    success: bool = True


@dataclass(slots=True)
class HookContext:
    event_name: str
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    file_path: str = ""
    message: str = ""
    error: str = ""
    agent_id: str = ""
    result: str = ""

    @property
    def event(self) -> str:
        """Compatibility alias used by the pre-CH12 callback API."""

        return self.event_name

    @property
    def arguments(self) -> dict[str, Any]:
        """Compatibility alias for earlier Agent hook contexts."""

        return self.tool_args

    def get_field(self, name: str) -> str:
        normalized = name.strip()
        direct = {
            "event": self.event_name,
            "tool": self.tool_name,
            "tool_name": self.tool_name,
            "file": self.file_path,
            "file_path": self.file_path,
            "message": self.message,
            "error": self.error,
            "result": self.result,
        }
        if normalized in direct:
            return direct[normalized]
        if normalized.startswith("args."):
            value = _nested_value(self.tool_args, normalized[5:])
            return _stringify(value)
        return ""

    def expand(self, template: str) -> str:
        def replace(match: re.Match[str]) -> str:
            argument_path = match.group(1)
            if argument_path is not None:
                return _stringify(_nested_value(self.tool_args, argument_path))
            name = match.group(2) or ""
            values = {
                "EVENT": self.event_name,
                "TOOL_NAME": self.tool_name,
                "FILE_PATH": self.file_path,
                "MESSAGE": self.message,
                "ERROR": self.error,
            }
            return values.get(name, "")

        return _TEMPLATE_PATTERN.sub(replace, template)


def _nested_value(values: dict[str, Any], path: str) -> Any:
    current: Any = values
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return ""
        current = current[part]
    return current


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


@dataclass(slots=True)
class Hook:
    id: str
    event: str
    action: Action
    condition: Condition | ConditionGroup | None = None
    reject: bool = False
    once: bool = False
    async_exec: bool = False
    executed: bool = False

    def should_run(self, context: HookContext | None = None) -> bool:
        if self.once and self.executed:
            return False
        return self.condition is None or context is None or self.condition.evaluate(context)

    def mark_executed(self) -> None:
        self.executed = True


@dataclass(frozen=True, slots=True)
class HookNotification:
    hook_id: str
    event: str
    output: str
    success: bool


class ToolRejectedError(Exception):
    def __init__(self, tool: str, reason: str, hook_id: str) -> None:
        self.tool = tool
        self.reason = reason
        self.hook_id = hook_id
        super().__init__(f"Hook {hook_id!r} rejected {tool!r}: {reason}")


@dataclass(frozen=True, slots=True)
class HookResult:
    """Compatibility result for programmatic callbacks registered before CH12."""

    allowed: bool = True
    reason: str = ""
    notification: str = ""


HookCallback = Callable[[HookContext], HookResult | None | Awaitable[HookResult | None]]


__all__ = [
    "Action",
    "ActionResult",
    "Hook",
    "HookCallback",
    "HookContext",
    "HookNotification",
    "HookResult",
    "ToolRejectedError",
]
