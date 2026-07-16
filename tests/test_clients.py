"""Provider event translation tests without network access."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from mewcode.client import AnthropicClient, AuthenticationError, OpenAIClient
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.tools.base import (
    StreamEnd,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


def config(protocol: str, **updates: Any) -> ProviderConfig:
    data: dict[str, Any] = {
        "protocol": protocol,
        "model": "claude-sonnet-4-6" if protocol == "anthropic" else "gpt-5.5",
        "base_url": "https://example.com/v1",
        "api_key": "test-key",
    }
    data.update(updates)
    return ProviderConfig.model_validate(data)


class FakeAnthropicStream:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    async def __aenter__(self) -> FakeAnthropicStream:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[Any]:
        async def generate() -> AsyncIterator[Any]:
            for event in self.events:
                yield event

        return generate()

    async def get_final_message(self) -> Any:
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=11, output_tokens=7),
            stop_reason="end_turn",
        )


class FakeAnthropicMessages:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.kwargs: dict[str, Any] = {}

    def stream(self, **kwargs: Any) -> FakeAnthropicStream:
        self.kwargs = kwargs
        return FakeAnthropicStream(self.events)


@pytest.mark.asyncio
async def test_anthropic_stream_translates_thinking_text_and_tool_events() -> None:
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="thinking", thinking="", signature=""),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="plan"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="signature_delta", signature="sig"),
        ),
        SimpleNamespace(type="content_block_stop", index=0),
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(type="text_delta", text="answer"),
        ),
        SimpleNamespace(
            type="content_block_start",
            index=2,
            content_block=SimpleNamespace(type="tool_use", id="call-1", name="echo"),
        ),
        SimpleNamespace(
            type="content_block_delta",
            index=2,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"text":"hi"}'),
        ),
        SimpleNamespace(type="content_block_stop", index=2),
    ]
    messages = FakeAnthropicMessages(events)
    client = AnthropicClient(config("anthropic", thinking=True))
    client._client = SimpleNamespace(messages=messages)
    conversation = ConversationManager()
    conversation.add_user_message("hello")

    translated = [event async for event in client.stream(conversation, system="system")]

    assert translated == [
        ThinkingDelta("plan"),
        ThinkingComplete("plan", "sig"),
        TextDelta("answer"),
        ToolCallStart("call-1", "echo"),
        ToolCallDelta("call-1", '{"text":"hi"}'),
        ToolCallComplete("call-1", "echo", {"text": "hi"}),
        StreamEnd("end_turn", 11, 7),
    ]
    assert messages.kwargs["thinking"] == {"type": "enabled", "budget_tokens": 0}
    assert messages.kwargs["system"] == "system"
    assert conversation.last_input_tokens == 11


class FakeOpenAIStream:
    def __init__(self, events: list[Any]) -> None:
        self.events = events

    def __aiter__(self) -> AsyncIterator[Any]:
        async def generate() -> AsyncIterator[Any]:
            for event in self.events:
                yield event

        return generate()


class FakeResponses:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.kwargs: dict[str, Any] = {}

    async def create(self, **kwargs: Any) -> FakeOpenAIStream:
        self.kwargs = kwargs
        return FakeOpenAIStream(self.events)


@pytest.mark.asyncio
async def test_openai_stream_translates_responses_api_events() -> None:
    events = [
        SimpleNamespace(type="response.output_text.delta", delta="answer"),
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call",
                id="item-1",
                call_id="call-1",
                name="echo",
            ),
        ),
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            item_id="item-1",
            delta='{"text":"hi"}',
        ),
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id="item-1",
            name="echo",
            arguments='{"text":"hi"}',
        ),
        SimpleNamespace(
            type="response.completed",
            response=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=5, output_tokens=3),
            ),
        ),
    ]
    responses = FakeResponses(events)
    client = OpenAIClient(config("openai"))
    client._client = SimpleNamespace(responses=responses)
    conversation = ConversationManager()
    conversation.add_user_message("hello")

    translated = [
        event
        async for event in client.stream(
            conversation,
            tools=[{"name": "echo", "description": "Echo", "input_schema": {"type": "object"}}],
        )
    ]

    assert translated == [
        TextDelta("answer"),
        ToolCallStart("call-1", "echo"),
        ToolCallDelta("call-1", '{"text":"hi"}'),
        ToolCallComplete("call-1", "echo", {"text": "hi"}),
        StreamEnd("end_turn", 5, 3),
    ]
    assert responses.kwargs["stream"] is True
    assert responses.kwargs["tools"][0]["type"] == "function"
    assert responses.kwargs["tools"][0]["parameters"] == {"type": "object"}


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
def test_missing_environment_api_key_is_authentication_error(
    monkeypatch: pytest.MonkeyPatch, protocol: str
) -> None:
    monkeypatch.delenv("MISSING_MEWCODE_TEST_KEY", raising=False)
    provider = config(protocol, api_key="${MISSING_MEWCODE_TEST_KEY}")
    client_type = AnthropicClient if protocol == "anthropic" else OpenAIClient
    with pytest.raises(AuthenticationError, match="Invalid API key"):
        client_type(provider)
