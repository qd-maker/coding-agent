"""Shared tool contracts and provider-neutral streaming events."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from pydantic import BaseModel

SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".tox", ".mypy_cache"}
MAX_OUTPUT_CHARS = 10_000

ToolCategory = Literal["read", "write", "command"]


@dataclass(frozen=True, slots=True)
class ToolResult:
    output: str
    is_error: bool = False
    data: dict[str, Any] | None = None
    preview: str | None = None
    artifact_path: str | None = None
    exit_code: int | None = None
    diagnostics: tuple[str, ...] = ()


class Tool(ABC):
    """Uniform contract implemented by every Simple Code tool."""

    name: ClassVar[str]
    description: ClassVar[str]
    params_model: ClassVar[type[BaseModel]]
    category: ClassVar[ToolCategory]
    is_concurrency_safe: ClassVar[bool] = False
    is_destructive: ClassVar[bool] = False
    is_system_tool: ClassVar[bool] = False
    should_defer: ClassVar[bool] = False
    is_plan_safe: ClassVar[bool] = False
    plan_mode_only: ClassVar[bool] = False
    execution_timeout: ClassVar[float | None] = 30.0

    @property
    def is_read_only(self) -> bool:
        return self.category == "read"

    def concurrency_safe_for(self, arguments: dict[str, Any]) -> bool:
        """Whether this specific call may run beside other safe tools."""

        del arguments
        return self.is_concurrency_safe

    def get_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.params_model.model_json_schema(),
        }

    @abstractmethod
    async def execute(self, params: Any) -> ToolResult:
        """Execute validated parameters and return a model-visible result."""


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStart:
    tool_id: str
    tool_name: str


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    tool_id: str
    arguments_delta: str


@dataclass(frozen=True, slots=True)
class ToolCallComplete:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ThinkingDelta:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingComplete:
    thinking: str
    signature: str


@dataclass(frozen=True, slots=True)
class StreamEnd:
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


StreamEvent = (
    TextDelta
    | ThinkingDelta
    | ThinkingComplete
    | ToolCallStart
    | ToolCallDelta
    | ToolCallComplete
    | StreamEnd
)


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
    "ToolCategory",
    "ToolResult",
]
