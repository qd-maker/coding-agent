"""CH16 reliability: pairing, cancel, headless, overflow retry, safe concurrency."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from simplecode.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    RetryEvent,
    StreamText,
    partition_tool_calls,
)
from simplecode.client import LLMClient
from simplecode.conversation import ConversationManager, ToolResultBlock, ToolUseBlock
from simplecode.permissions import PermissionChecker, PermissionMode
from simplecode.permissions.sandbox import PathSandbox
from simplecode.tools import ToolRegistry, create_default_registry
from simplecode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from simplecode.tools.write_file import WriteFile
from tests.test_agent import EchoTool, MockLLMClient, provider


class PromptTooLongThenOkClient(LLMClient):
    def __init__(self) -> None:
        super().__init__(provider())
        self.calls = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del conversation, system, tools
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("prompt is too long")
        if self.calls == 2:
            yield TextDelta("<summary>recovered context</summary>")
            yield StreamEnd("end_turn", 10, 5)
            return
        yield TextDelta("finished after compact")
        yield StreamEnd("end_turn", 3, 2)


def test_pairing_fills_missing_and_drops_orphans() -> None:
    conversation = ConversationManager()
    conversation.add_assistant_message("work", [ToolUseBlock("t1", "echo", {"text": "1"})])
    conversation.add_tool_results_message([ToolResultBlock("orphan", "stale")])
    repaired = conversation.ensure_tool_result_pairing()
    assert repaired >= 2
    results = [block for message in conversation.history for block in message.tool_results]
    assert [block.tool_use_id for block in results] == ["t1"]
    assert results[0].is_error


@pytest.mark.asyncio
async def test_cancel_after_tool_use_seals_result(tmp_path: Path) -> None:
    started = asyncio.Event()

    class HangAfterToolClient(MockLLMClient):
        async def stream(
            self,
            conversation: ConversationManager,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AsyncIterator[StreamEvent]:
            del conversation, system, tools
            yield ToolCallComplete("hang-1", "echo", {"text": "x"})
            yield StreamEnd("tool_use")
            started.set()
            await asyncio.Event().wait()

    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(HangAfterToolClient([]), registry=registry, work_dir=tmp_path)

    async def collect() -> None:
        async for _event in agent.run("go"):
            pass

    task = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    results = [block for message in agent.conversation.history for block in message.tool_results]
    uses = [block for message in agent.conversation.history for block in message.tool_uses]
    assert {block.tool_id for block in uses} == {"hang-1"}
    assert {block.tool_use_id for block in results} == {"hang-1"}
    assert results[0].is_error


@pytest.mark.asyncio
async def test_run_to_completion_denies_ask_without_hanging(tmp_path: Path) -> None:
    target = tmp_path / "secret.txt"
    client = MockLLMClient(
        [
            [
                ToolCallComplete(
                    "w1",
                    "WriteFile",
                    {"file_path": str(target), "content": "nope"},
                ),
                StreamEnd("tool_use"),
            ],
            [TextDelta("I could not write without approval."), StreamEnd("end_turn", 2, 2)],
        ]
    )
    checker = PermissionChecker(sandbox=PathSandbox(tmp_path), mode=PermissionMode.DEFAULT)
    agent = Agent(
        client,
        registry=create_default_registry(),
        work_dir=tmp_path,
        permission_checker=checker,
        completion_gate_enabled=False,
    )
    text = await asyncio.wait_for(agent.run_to_completion("write the file"), 2)
    assert "could not write" in text.casefold()
    assert not target.exists()


@pytest.mark.asyncio
async def test_prompt_too_long_compacts_and_retries(tmp_path: Path) -> None:
    client = PromptTooLongThenOkClient()
    agent = Agent(client, registry=ToolRegistry(), work_dir=tmp_path)
    events = [event async for event in agent.run("continue the long task")]
    assert any(isinstance(event, CompactNotification) for event in events)
    assert any(
        isinstance(event, RetryEvent) and "prompt_too_long" in event.reason for event in events
    )
    assert any(
        isinstance(event, StreamText) and "finished after compact" in event.text for event in events
    )
    assert not any(isinstance(event, ErrorEvent) for event in events)


def test_safe_bash_batches_with_read_tools() -> None:
    registry = create_default_registry()
    batches = partition_tool_calls(
        [
            ToolCallComplete("1", "Bash", {"command": "ls"}),
            ToolCallComplete("2", "ReadFile", {"file_path": "README.md"}),
        ],
        registry,
    )
    assert len(batches) == 1
    assert batches[0].concurrent is True

    unsafe = partition_tool_calls(
        [
            ToolCallComplete("1", "Bash", {"command": "rm file"}),
            ToolCallComplete("2", "ReadFile", {"file_path": "README.md"}),
        ],
        registry,
    )
    assert [batch.concurrent for batch in unsafe] == [False, True]


def test_write_file_is_destructive() -> None:
    assert WriteFile().is_destructive is True
