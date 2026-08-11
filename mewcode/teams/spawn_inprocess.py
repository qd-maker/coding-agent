"""In-process teammate runtime."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.agent import Agent
    from mewcode.conversation import ConversationManager


@dataclass(slots=True)
class InProcessTeammateHandle:
    agent: Agent
    task: asyncio.Task[str]
    name: str

    @property
    def done(self) -> bool:
        return self.task.done()

    @property
    def result(self) -> str | None:
        if not self.task.done() or self.task.cancelled():
            return None
        try:
            return self.task.result()
        except Exception:  # noqa: BLE001 - inspection must be safe
            return None

    def cancel(self) -> None:
        if not self.task.done():
            self.task.cancel()


def spawn_inprocess_teammate(
    agent: Agent,
    prompt: str,
    name: str,
    conversation: ConversationManager | None = None,
) -> InProcessTeammateHandle:
    if conversation is None:
        coroutine = agent.run_to_completion(prompt)
    else:
        coroutine = agent.run_to_completion(prompt, conversation)
    task = asyncio.create_task(coroutine, name=f"teammate-{name}")
    return InProcessTeammateHandle(agent=agent, task=task, name=name)


__all__ = ["InProcessTeammateHandle", "spawn_inprocess_teammate"]
