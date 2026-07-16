"""Provider selection helper reserved for future sub-agent tool calls."""

from __future__ import annotations

from mewcode.agent import Agent, StreamText
from mewcode.client import LLMClient, create_client
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager


class AgentTool:
    def __init__(self, provider_config: ProviderConfig, system: str = "") -> None:
        self._provider_config = provider_config
        self._system = system

    def _select_llm(self, model_override: str | None = None) -> LLMClient:
        if model_override:
            return self._create_client_for_model(model_override)
        return create_client(self._provider_config)

    def _create_client_for_model(self, model_alias: str) -> LLMClient:
        model_map = {
            "haiku": "claude-haiku-4-5",
            "sonnet": "claude-sonnet-4-6",
            "opus": "claude-opus-4-6",
        }
        model = model_map.get(model_alias, model_alias)
        config = self._provider_config.model_copy(
            update={"name": f"subagent-{model_alias}", "model": model}
        )
        return create_client(config)

    async def run(self, prompt: str, model: str | None = None) -> str:
        conversation = ConversationManager()
        agent = Agent(
            client=self._select_llm(model),
            system=self._system,
            conversation=conversation,
        )
        text: list[str] = []
        async for event in agent.run(prompt):
            if isinstance(event, StreamText):
                text.append(event.text)
        return "".join(text)


__all__ = ["AgentTool"]
