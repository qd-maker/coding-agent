"""Deferred bridge from an Agent tool call to interactive TUI input."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from simplecode.tools.base import Tool, ToolResult


class QuestionItem(BaseModel):
    type: Literal["text", "select", "confirm"] = "text"
    name: str = Field(min_length=1)
    message: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)


class AskUserParams(BaseModel):
    questions: list[QuestionItem] = Field(min_length=1, max_length=3)


@dataclass(slots=True)
class AskUserEvent:
    questions: list[QuestionItem]
    future: asyncio.Future[dict[str, str]]


class AskUserTool(Tool):
    name = "AskUserQuestion"
    description = "Ask the user up to three short questions and wait for their answers."
    params_model: ClassVar[type[BaseModel]] = AskUserParams
    category = "read"
    is_system_tool = True
    should_defer = True
    execution_timeout = 305.0

    def __init__(self) -> None:
        self._pending_event: AskUserEvent | None = None

    @property
    def pending_event(self) -> AskUserEvent | None:
        return self._pending_event

    async def execute(self, params: AskUserParams) -> ToolResult:
        if self._pending_event is not None:
            return ToolResult("Error: a user question is already pending", is_error=True)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, str]] = loop.create_future()
        event = AskUserEvent(list(params.questions), future)
        self._pending_event = event
        try:
            answers = await asyncio.wait_for(future, timeout=300)
        except TimeoutError:
            return ToolResult("User did not respond within 5 minutes", is_error=True)
        finally:
            self._pending_event = None
        output = "\n".join(
            f"{question.name}: {answers.get(question.name, '')}" for question in params.questions
        )
        return ToolResult(output)


__all__ = ["AskUserEvent", "AskUserParams", "AskUserTool", "QuestionItem"]
