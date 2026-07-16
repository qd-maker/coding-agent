"""Isolated skill prompt execution using the common LLM interface."""

from __future__ import annotations

from mewcode.agent import Agent, StreamText
from mewcode.client import LLMClient
from mewcode.conversation import ConversationManager


class SkillExecutor:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def execute(self, instructions: str, prompt: str) -> str:
        conversation = ConversationManager()
        agent = Agent(self.client, system=instructions, conversation=conversation)
        chunks: list[str] = []
        async for event in agent.run(prompt):
            if isinstance(event, StreamText):
                chunks.append(event.text)
        return "".join(chunks)


__all__ = ["SkillExecutor"]
