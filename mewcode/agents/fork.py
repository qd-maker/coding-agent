"""A small isolated-conversation runner for future forked agents."""

from __future__ import annotations

from collections.abc import AsyncIterator

from mewcode.agent import Agent, AgentEvent
from mewcode.client import LLMClient
from mewcode.conversation import ConversationManager


async def run_fork(client: LLMClient, prompt: str, system: str = "") -> AsyncIterator[AgentEvent]:
    conversation = ConversationManager()
    agent = Agent(client, system=system, conversation=conversation)
    async for event in agent.run(prompt):
        yield event


__all__ = ["run_fork"]
