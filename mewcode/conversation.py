"""Provider-neutral conversation history and serializers."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ToolUseBlock:
    tool_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ThinkingBlock:
    thinking: str
    signature: str


@dataclass(slots=True)
class Message:
    role: Literal["user", "assistant"]
    content: str = ""
    tool_uses: list[ToolUseBlock] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)


@dataclass
class ConversationManager:
    """Mutable single-consumer conversation with lossless provider serialization."""

    history: list[Message] = field(default_factory=list)
    env_injected: bool = False
    ltm_injected: bool = field(default=False, init=False)
    last_input_tokens: int = 0

    def add_user_message(self, content: str) -> Message:
        message = Message(role="user", content=content)
        self.history.append(message)
        return message

    def add_assistant_message(
        self,
        content: str,
        tool_uses: list[ToolUseBlock] | None = None,
        thinking_blocks: list[ThinkingBlock] | None = None,
    ) -> Message:
        message = Message(
            role="assistant",
            content=content,
            tool_uses=list(tool_uses or []),
            thinking_blocks=list(thinking_blocks or []),
        )
        self.history.append(message)
        return message

    def add_system_reminder(self, content: str) -> Message:
        reminder = f"<system-reminder>\n{content}\n</system-reminder>"
        return self.add_user_message(reminder)

    def add_tool_results_message(self, results: list[ToolResultBlock]) -> Message:
        message = Message(role="user", tool_results=list(results))
        self.history.append(message)
        return message

    def inject_environment(self, context: str) -> bool:
        if self.env_injected:
            return False
        self.history.insert(0, Message(role="user", content=context))
        self.env_injected = True
        return True

    def refresh_environment(self, context: str) -> None:
        """Replace the pinned environment message or inject it when absent."""

        if self.env_injected and self.history:
            self.history[0] = Message(role="user", content=context)
            return
        self.inject_environment(context)

    def inject_long_term_memory(
        self,
        instructions: str,
        memories: str | list[str],
    ) -> bool:
        if self.ltm_injected:
            return False
        if isinstance(memories, str):
            memory_content = memories.strip()
        else:
            memory_content = "\n\n".join(memory.strip() for memory in memories if memory.strip())
        instructions = instructions.strip()
        if not instructions and not memory_content:
            return False
        insert_at = 1 if self.env_injected and self.history else 0
        injected: list[Message] = []
        if instructions:
            injected.append(Message(role="user", content=f"## 项目指令\n{instructions}"))
        if memory_content:
            injected.append(Message(role="user", content=f"## 自动记忆\n{memory_content}"))
        injected.append(Message(role="assistant", content="好的，我已了解项目背景和记忆。"))
        self.history[insert_at:insert_at] = injected
        self.ltm_injected = True
        return True

    def replace_history(self, messages: list[Message]) -> None:
        self.history = list(messages)
        self.env_injected = False
        self.ltm_injected = False

    def get_messages(self) -> list[Message]:
        return list(self.history)

    def serialize(self, protocol: str) -> list[dict[str, Any]]:
        if protocol == "anthropic":
            return self._serialize_anthropic()
        if protocol == "openai":
            return self._serialize_openai()
        raise ValueError(f"Unknown protocol: {protocol}")

    @staticmethod
    def _append_text_to_content(content: Any, text: str) -> Any:
        if isinstance(content, str):
            return f"{content}\n\n{text}" if content else text
        blocks = list(content)
        blocks.append({"type": "text", "text": text})
        return blocks

    def _serialize_anthropic(self) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for message in self.history:
            if message.role == "assistant" and (message.thinking_blocks or message.tool_uses):
                content_blocks: list[dict[str, Any]] = []
                for thinking_block in message.thinking_blocks:
                    content_blocks.append(
                        {
                            "type": "thinking",
                            "thinking": thinking_block.thinking,
                            "signature": thinking_block.signature,
                        }
                    )
                if message.content:
                    content_blocks.append({"type": "text", "text": message.content})
                for tool_use in message.tool_uses:
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tool_use.tool_id,
                            "name": tool_use.name,
                            "input": tool_use.input,
                        }
                    )
                content: str | list[dict[str, Any]] = content_blocks
            elif message.tool_results:
                content = [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_result.tool_use_id,
                        "content": tool_result.content,
                        "is_error": tool_result.is_error,
                    }
                    for tool_result in message.tool_results
                ]
            else:
                content = message.content

            if (
                isinstance(content, str)
                and content.startswith("<system-reminder>")
                and serialized
                and serialized[-1]["role"] == "user"
            ):
                serialized[-1]["content"] = self._append_text_to_content(
                    serialized[-1]["content"], content
                )
                continue
            serialized.append({"role": message.role, "content": content})
        return serialized

    def _serialize_openai(self) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for message in self.history:
            if message.content:
                serialized.append({"role": message.role, "content": message.content})
            for tool_use in message.tool_uses:
                serialized.append(
                    {
                        "type": "function_call",
                        "name": tool_use.name,
                        "call_id": tool_use.tool_id,
                        "arguments": json.dumps(tool_use.input, ensure_ascii=False),
                    }
                )
            for tool_result in message.tool_results:
                serialized.append(
                    {
                        "type": "function_call_output",
                        "call_id": tool_result.tool_use_id,
                        "output": tool_result.content,
                    }
                )
        return serialized


__all__ = [
    "ConversationManager",
    "Message",
    "ThinkingBlock",
    "ToolResultBlock",
    "ToolUseBlock",
]
