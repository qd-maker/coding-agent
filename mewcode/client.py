"""Unified async streaming clients for Anthropic and OpenAI."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import anthropic as anthropic_sdk
import openai as openai_sdk

from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)

_EPHEMERAL: dict[str, str] = {"type": "ephemeral"}


def _mark_last_user_tail_for_cache(messages: list[dict[str, Any]]) -> None:
    """Attach cache_control to the last block of the final user message (in-place)."""
    for message in reversed(messages):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = [
                {"type": "text", "text": content, "cache_control": dict(_EPHEMERAL)}
            ]
            return
        if isinstance(content, list) and content:
            last = content[-1]
            if isinstance(last, dict):
                last["cache_control"] = dict(_EPHEMERAL)
            return
        return


def _mark_last_tool_for_cache(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Shallow-copy tools and mark the last schema for prompt caching."""
    if not tools:
        return tools
    copied = list(tools)
    last = dict(copied[-1])
    last["cache_control"] = dict(_EPHEMERAL)
    copied[-1] = last
    return copied


class LLMError(Exception):
    """Base class for all provider-independent LLM failures."""


class AuthenticationError(LLMError):
    """The provider rejected or did not receive an API key."""


class RateLimitError(LLMError):
    """A provider rate limit, optionally carrying Retry-After seconds."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class NetworkError(LLMError):
    """A transport-level provider failure."""


class LLMClient(ABC):
    """Single streaming interface consumed by every MewCode caller."""

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.max_output_tokens = config.get_max_output_tokens()

    @abstractmethod
    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        if False:  # pragma: no cover - makes this abstract method an async generator
            yield StreamEnd("unreachable")
        raise NotImplementedError

    def set_max_output_tokens(self, tokens: int) -> None:
        if tokens < 1:
            raise ValueError("max output tokens must be positive")
        self.max_output_tokens = tokens


def _supports_adaptive_thinking(model: str) -> bool:
    for prefix in ("claude-opus-4-", "claude-sonnet-4-"):
        if model.startswith(prefix):
            suffix = model[len(prefix) :]
            return bool(suffix and suffix[0].isdigit() and int(suffix[0]) >= 6)
    return False


def _retry_after(error: Any) -> float | None:
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {}) if response is not None else {}
    raw = headers.get("retry-after") if headers is not None else None
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class _AnthropicThinkingState:
    text: str = ""
    signature: str = ""


@dataclass(slots=True)
class _ToolState:
    tool_id: str
    name: str
    json_text: str = ""
    started: bool = False


class AnthropicClient(LLMClient):
    """Anthropic Messages API adapter."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError("Invalid API key: API key is missing")
        self._client = anthropic_sdk.AsyncAnthropic(
            api_key=api_key,
            base_url=config.base_url_string(),
        )

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        messages = conversation.serialize("anthropic")
        _mark_last_user_tail_for_cache(messages)
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.max_output_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = [{"type": "text", "text": system, "cache_control": dict(_EPHEMERAL)}]
        if tools:
            kwargs["tools"] = _mark_last_tool_for_cache(tools)
        if self.config.thinking:
            if _supports_adaptive_thinking(self.config.model):
                kwargs["thinking"] = {"type": "adaptive"}
            else:
                kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": max(self.max_output_tokens - 1, 1024),
                }

        thinking_states: dict[int, _AnthropicThinkingState] = {}
        tool_states: dict[int, _ToolState] = {}
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    raw_event: Any = event
                    event_type = getattr(raw_event, "type", "")
                    index = int(getattr(raw_event, "index", 0) or 0)
                    if event_type == "content_block_start":
                        block = raw_event.content_block
                        block_type = getattr(block, "type", "")
                        if block_type == "thinking":
                            thinking_states[index] = _AnthropicThinkingState(
                                text=getattr(block, "thinking", "") or "",
                                signature=getattr(block, "signature", "") or "",
                            )
                        elif block_type == "tool_use":
                            state = _ToolState(
                                tool_id=str(getattr(block, "id", "")),
                                name=str(getattr(block, "name", "")),
                            )
                            tool_states[index] = state
                            state.started = True
                            yield ToolCallStart(state.tool_id, state.name)
                    elif event_type == "content_block_delta":
                        delta: Any = raw_event.delta
                        delta_type = getattr(delta, "type", "")
                        if delta_type == "text_delta":
                            yield TextDelta(str(delta.text))
                        elif delta_type == "thinking_delta":
                            text = str(delta.thinking)
                            thinking_state = thinking_states.setdefault(
                                index, _AnthropicThinkingState()
                            )
                            thinking_state.text += text
                            yield ThinkingDelta(text)
                        elif delta_type == "signature_delta":
                            thinking_states.setdefault(
                                index, _AnthropicThinkingState()
                            ).signature += str(delta.signature)
                        elif delta_type == "input_json_delta":
                            partial = str(delta.partial_json)
                            tool_state = tool_states.setdefault(index, _ToolState("", ""))
                            tool_state.json_text += partial
                            yield ToolCallDelta(tool_state.tool_id, partial)
                    elif event_type == "content_block_stop":
                        if index in thinking_states:
                            completed_thinking = thinking_states.pop(index)
                            yield ThinkingComplete(
                                completed_thinking.text, completed_thinking.signature
                            )
                        if index in tool_states:
                            completed_tool = tool_states.pop(index)
                            try:
                                arguments = json.loads(completed_tool.json_text or "{}")
                            except json.JSONDecodeError as exc:
                                raise LLMError(
                                    f"Invalid tool arguments for {completed_tool.name}: {exc}"
                                ) from exc
                            yield ToolCallComplete(
                                completed_tool.tool_id, completed_tool.name, arguments
                            )

                final = await stream.get_final_message()
                conversation.last_input_tokens = int(final.usage.input_tokens)
                yield StreamEnd(
                    stop_reason=str(final.stop_reason or "end_turn"),
                    input_tokens=int(final.usage.input_tokens),
                    output_tokens=int(final.usage.output_tokens),
                )
        except anthropic_sdk.AuthenticationError as exc:
            raise AuthenticationError(f"Invalid API key: {exc}") from exc
        except anthropic_sdk.RateLimitError as exc:
            raise RateLimitError(str(exc), retry_after=_retry_after(exc)) from exc
        except anthropic_sdk.APIConnectionError as exc:
            raise NetworkError(f"Network error: {exc}") from exc
        except anthropic_sdk.APIStatusError as exc:
            raise LLMError(f"API error ({exc.status_code}): {exc}") from exc


def _openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for tool in tools:
        if tool.get("type") == "function":
            normalized.append(tool)
            continue
        normalized.append(
            {
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
            }
        )
    return normalized


class OpenAIClient(LLMClient):
    """OpenAI Responses API adapter."""

    def __init__(self, config: ProviderConfig) -> None:
        super().__init__(config)
        api_key = config.resolve_api_key()
        if not api_key:
            raise AuthenticationError("Invalid API key: API key is missing")
        self._client = openai_sdk.AsyncOpenAI(
            api_key=api_key,
            base_url=config.base_url_string(),
        )

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "input": conversation.serialize("openai"),
            "max_output_tokens": self.max_output_tokens,
            "stream": True,
        }
        if system:
            kwargs["instructions"] = system
        if tools:
            kwargs["tools"] = _openai_tools(tools)

        states: dict[str, _ToolState] = {}
        try:
            response_stream = await self._client.responses.create(**kwargs)
            async for event in response_stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    yield TextDelta(str(event.delta))
                elif event_type == "response.output_item.added":
                    item = event.item
                    if getattr(item, "type", "") == "function_call":
                        item_id = str(getattr(item, "id", ""))
                        call_id = str(getattr(item, "call_id", item_id))
                        state = _ToolState(call_id, str(getattr(item, "name", "")), started=True)
                        states[item_id] = state
                        yield ToolCallStart(state.tool_id, state.name)
                elif event_type == "response.function_call_arguments.delta":
                    item_id = str(getattr(event, "item_id", ""))
                    state = states.setdefault(item_id, _ToolState(item_id, ""))
                    if not state.started:
                        state.started = True
                        yield ToolCallStart(state.tool_id, state.name)
                    delta = str(event.delta)
                    state.json_text += delta
                    yield ToolCallDelta(state.tool_id, delta)
                elif event_type == "response.function_call_arguments.done":
                    item_id = str(getattr(event, "item_id", ""))
                    state = states.pop(item_id, _ToolState(item_id, str(event.name)))
                    state.name = str(getattr(event, "name", state.name))
                    if not state.started:
                        yield ToolCallStart(state.tool_id, state.name)
                    raw_arguments = str(getattr(event, "arguments", "") or state.json_text or "{}")
                    try:
                        arguments = json.loads(raw_arguments)
                    except json.JSONDecodeError as exc:
                        raise LLMError(f"Invalid tool arguments for {state.name}: {exc}") from exc
                    yield ToolCallComplete(state.tool_id, state.name, arguments)
                elif event_type == "response.completed":
                    usage = getattr(event.response, "usage", None)
                    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
                    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
                    conversation.last_input_tokens = input_tokens
                    yield StreamEnd("end_turn", input_tokens, output_tokens)
        except openai_sdk.AuthenticationError as exc:
            raise AuthenticationError(f"Invalid API key: {exc}") from exc
        except openai_sdk.RateLimitError as exc:
            raise RateLimitError(str(exc), retry_after=_retry_after(exc)) from exc
        except openai_sdk.APIConnectionError as exc:
            raise NetworkError(f"Network error: {exc}") from exc
        except openai_sdk.APIStatusError as exc:
            raise LLMError(f"API error ({exc.status_code}): {exc}") from exc


def create_client(config: ProviderConfig) -> LLMClient:
    if config.protocol == "anthropic":
        return AnthropicClient(config)
    if config.protocol == "openai":
        return OpenAIClient(config)
    raise ValueError(f"Unknown protocol: {config.protocol}")


__all__ = [
    "AnthropicClient",
    "AuthenticationError",
    "LLMClient",
    "LLMError",
    "NetworkError",
    "OpenAIClient",
    "RateLimitError",
    "_supports_adaptive_thinking",
    "create_client",
]
