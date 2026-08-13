"""Agent and conversation integration tests required by the ch02 checklist."""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from simplecode.agent import (
    MAX_TOKENS_CEILING,
    Agent,
    ErrorEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamCollector,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    UsageEvent,
    partition_tool_calls,
)
from simplecode.client import (
    AnthropicClient,
    LLMClient,
    OpenAIClient,
    _supports_adaptive_thinking,
    create_client,
)
from simplecode.config import ProviderConfig
from simplecode.conversation import (
    ConversationManager,
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from simplecode.hooks import HookEngine, HookResult
from simplecode.permissions import PermissionChecker, PermissionMode, Rule, RuleEngine
from simplecode.prompts import _REMINDER_INTERVAL, build_plan_mode_reminder
from simplecode.tools import ToolRegistry, create_default_registry
from simplecode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    Tool,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)


def provider(protocol: str = "anthropic", **updates: Any) -> ProviderConfig:
    data: dict[str, Any] = {
        "protocol": protocol,
        "model": "claude-sonnet-4-6" if protocol == "anthropic" else "gpt-5.5",
        "base_url": (
            "https://api.anthropic.com" if protocol == "anthropic" else "https://api.openai.com/v1"
        ),
        "api_key": "test-key",
    }
    data.update(updates)
    return ProviderConfig.model_validate(data)


class MockLLMClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        super().__init__(provider())
        self.responses = list(responses)
        self.snapshots: list[list[Message]] = []
        self.tool_schemas: list[list[dict[str, Any]] | None] = []
        self.system_prompts: list[str | None] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.snapshots.append(conversation.get_messages())
        self.tool_schemas.append(tools)
        self.system_prompts.append(system)
        if not self.responses:
            raise AssertionError("Mock response script exhausted")
        for event in self.responses.pop(0):
            yield event


class EchoParams(BaseModel):
    text: str


class EchoTool(Tool):
    name = "echo"
    description = "Echo text"
    params_model = EchoParams
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: EchoParams) -> ToolResult:
        return ToolResult(params.text)


class WriteEchoTool(EchoTool):
    name = "write_echo"
    category = "write"
    is_concurrency_safe = False


class ParallelEchoTool(EchoTool):
    name = "parallel_echo"

    def __init__(self) -> None:
        self.running = 0
        self.max_running = 0

    async def execute(self, params: EchoParams) -> ToolResult:
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        await asyncio.sleep(0.03)
        self.running -= 1
        return ToolResult(params.text)


def test_stream_event_dataclasses() -> None:
    assert TextDelta("a").text == "a"
    assert ToolCallComplete("id", "name", {"x": 1}).arguments == {"x": 1}
    assert StreamEnd("end_turn", 3, 4).output_tokens == 4


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("claude-sonnet-4-6", True),
        ("claude-opus-4-9-20270101", True),
        ("claude-sonnet-4-5", False),
        ("claude-haiku-4-6", False),
        ("claude-sonnet-3-7", False),
    ],
)
def test_adaptive_thinking_model_detection(model: str, expected: bool) -> None:
    assert _supports_adaptive_thinking(model) is expected


def test_provider_token_defaults_and_override() -> None:
    assert provider(thinking=False).get_max_output_tokens() == 8_192
    assert provider(thinking=True).get_max_output_tokens() == 64_000
    assert provider(thinking=True, max_output_tokens=12_345).get_max_output_tokens() == 12_345


def test_create_client_factory() -> None:
    assert isinstance(create_client(provider("anthropic")), AnthropicClient)
    assert isinstance(create_client(provider("openai")), OpenAIClient)
    invalid = ProviderConfig.model_construct(
        protocol="custom",
        model="x",
        base_url="https://example.com",
        api_key="key",
        name="custom",
        thinking=False,
        max_output_tokens=None,
    )
    with pytest.raises(ValueError, match="Unknown protocol: custom"):
        create_client(invalid)


def test_system_reminder_merges_with_previous_user_message() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("question")
    conversation.add_system_reminder("remember this")
    serialized = conversation.serialize("anthropic")
    assert len(serialized) == 1
    assert serialized[0]["content"] == (
        "question\n\n<system-reminder>\nremember this\n</system-reminder>"
    )


def test_anthropic_serialization_preserves_blocks() -> None:
    conversation = ConversationManager()
    conversation.add_assistant_message(
        "working",
        [ToolUseBlock("call-1", "echo", {"text": "喵"})],
        [ThinkingBlock("plan", "sig-1")],
    )
    conversation.add_tool_results_message([ToolResultBlock("call-1", "failed", True)])
    serialized = conversation.serialize("anthropic")
    assistant_blocks = serialized[0]["content"]
    assert assistant_blocks[0] == {
        "type": "thinking",
        "thinking": "plan",
        "signature": "sig-1",
    }
    assert assistant_blocks[2]["input"] == {"text": "喵"}
    assert serialized[1]["content"][0]["is_error"] is True


def test_openai_serialization_flattens_tool_items() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("hello")
    conversation.add_assistant_message(
        "",
        [ToolUseBlock("call-1", "echo", {"text": "hello"})],
    )
    conversation.add_tool_results_message([ToolResultBlock("call-1", "hello")])
    serialized = conversation.serialize("openai")
    assert serialized[0] == {"role": "user", "content": "hello"}
    assert serialized[1]["type"] == "function_call"
    assert serialized[1]["arguments"] == '{"text": "hello"}'
    assert serialized[2] == {
        "type": "function_call_output",
        "call_id": "call-1",
        "output": "hello",
    }


def test_context_injections_are_idempotent() -> None:
    conversation = ConversationManager()
    assert conversation.inject_environment("env") is True
    assert conversation.inject_environment("duplicate") is False
    assert conversation.inject_long_term_memory("rules", ["memory"]) is True
    assert conversation.inject_long_term_memory("duplicate", []) is False
    assert [message.content for message in conversation.history] == [
        "env",
        "## 项目指令\nrules",
        "## 自动记忆\nmemory",
        "好的，我已了解项目背景和记忆。",
    ]


def test_history_copy_and_replace() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("old")
    copied = conversation.get_messages()
    copied.clear()
    assert len(conversation.history) == 1
    replacement = [Message("user", "new")]
    conversation.replace_history(replacement)
    assert conversation.get_messages() == replacement
    assert conversation.get_messages() is not replacement


@pytest.mark.asyncio
async def test_plain_streaming_reply_is_written_to_history() -> None:
    client = MockLLMClient([[TextDelta("hel"), TextDelta("lo"), StreamEnd("end_turn", 2, 1)]])
    agent = Agent(client)
    events = [event async for event in agent.run("hi")]
    assert "".join(event.text for event in events if isinstance(event, StreamText)) == "hello"
    history = agent.conversation.get_messages()
    assert history[0].content.startswith("<environment>")
    assert [(message.role, message.content) for message in history[-2:]] == [
        ("user", "hi"),
        ("assistant", "hello"),
    ]


@pytest.mark.asyncio
async def test_single_step_tool_call() -> None:
    client = MockLLMClient(
        [
            [
                ToolCallStart("call-1", "echo"),
                ToolCallDelta("call-1", '{"text":"hi"}'),
                ToolCallComplete("call-1", "echo", {"text": "hi"}),
                StreamEnd("tool_use", 5, 3),
            ],
            [TextDelta("done"), StreamEnd("end_turn", 7, 2)],
        ]
    )
    tools = ToolRegistry()
    tools.register(EchoTool())
    agent = Agent(client, tools=tools)
    events = [event async for event in agent.run("echo hi")]
    assert any(
        isinstance(event, ToolUseEvent) and event.status == "result" and event.detail == "hi"
        for event in events
    )
    assert agent.conversation.history[-1].content == "done"


@pytest.mark.asyncio
async def test_message_splicing() -> None:
    conversation = ConversationManager()
    conversation.inject_environment("env_context")
    conversation.add_user_message("use tools")
    conversation.add_assistant_message(
        "working",
        [
            ToolUseBlock("call-1", "one", {"n": 1}),
            ToolUseBlock("call-2", "two", {"n": 2}),
        ],
        [ThinkingBlock("think", "signature")],
    )
    conversation.add_tool_results_message(
        [ToolResultBlock("call-1", "1"), ToolResultBlock("call-2", "2")]
    )
    conversation.add_assistant_message("final")
    msgs = conversation.serialize("anthropic")
    assert len(msgs) == 5
    assert msgs[2]["content"][0]["signature"] == "signature"
    assert len([block for block in msgs[2]["content"] if block["type"] == "tool_use"]) == 2
    assert len(msgs[3]["content"]) == 2


@pytest.mark.asyncio
async def test_token_usage_accumulates() -> None:
    client = MockLLMClient(
        [
            [
                ToolCallComplete("call-1", "echo", {"text": "hi"}),
                StreamEnd("tool_use", 10, 3),
            ],
            [TextDelta("done"), StreamEnd("end_turn", 20, 4)],
        ]
    )
    tools = ToolRegistry()
    tools.register(EchoTool())
    agent = Agent(client, tools=tools)
    _ = [event async for event in agent.run("go")]
    assert agent.total_input_tokens == 30
    assert agent.total_output_tokens == 7


@pytest.mark.asyncio
async def test_default_read_file_tool_runs_end_to_end(tmp_path: Path) -> None:
    target = tmp_path / "README.md"
    target.write_text("# Simple Code\nTools work", encoding="utf-8")
    client = MockLLMClient(
        [
            [
                ToolCallComplete("read-1", "ReadFile", {"file_path": str(target)}),
                StreamEnd("tool_use", 6, 2),
            ],
            [TextDelta("Read complete"), StreamEnd("end_turn", 8, 2)],
        ]
    )
    agent = Agent(client, tools=create_default_registry())
    events = [event async for event in agent.run("read the readme")]

    assert any(
        isinstance(event, ToolUseEvent)
        and event.status == "result"
        and "1\t# Simple Code" in event.detail
        and event.arguments == {"file_path": str(target)}
        and event.elapsed_seconds is not None
        for event in events
    )
    assert client.tool_schemas[0] is not None
    assert {schema["name"] for schema in client.tool_schemas[0] or []} >= {
        "ReadFile",
        "WriteFile",
        "EditFile",
        "Bash",
        "Glob",
        "Grep",
    }
    assert agent.conversation.history[-1].content == "Read complete"
    runtime_hint = client.system_prompts[0] or ""
    assert ("cmd.exe" in runtime_hint) if os.name == "nt" else ("/bin/sh" in runtime_hint)


@pytest.mark.asyncio
async def test_unknown_tool_failure_is_returned_to_model() -> None:
    client = MockLLMClient(
        [
            [ToolCallComplete("bad-1", "Missing", {}), StreamEnd("tool_use", 2, 1)],
            [TextDelta("I will adjust."), StreamEnd("end_turn", 3, 2)],
        ]
    )
    agent = Agent(client, tools=create_default_registry())
    _ = [event async for event in agent.run("do it")]
    tool_message = agent.conversation.history[-2]
    assert tool_message.tool_results[0].is_error is True
    assert "unknown" in tool_message.tool_results[0].content


@pytest.mark.asyncio
async def test_multi_step_autonomous() -> None:
    client = MockLLMClient(
        [
            [
                ToolCallComplete("call-1", "echo", {"text": "first"}),
                StreamEnd("tool_use", 2, 1),
            ],
            [
                ToolCallComplete("call-2", "echo", {"text": "second"}),
                StreamEnd("tool_use", 3, 1),
            ],
            [TextDelta("must not run"), StreamEnd("end_turn", 4, 1)],
        ]
    )
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(client, tools=registry)
    events = [event async for event in agent.run("go")]

    result_events = [
        event for event in events if isinstance(event, ToolUseEvent) and event.status == "result"
    ]
    assert result_events[0].detail == "first"
    assert result_events[1].detail == "second"
    assert len(client.responses) == 0
    assert agent.conversation.history[-1].content == "must not run"


@pytest.mark.asyncio
async def test_react_finds_nested_named_file_then_deletes_and_verifies(tmp_path: Path) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    target = plan_dir / "quiet-delta-0715-2356.md"
    target.write_text("# disposable plan", encoding="utf-8")
    delete_command = f'"{sys.executable}" -c "import os,sys;os.remove(sys.argv[1])" "{target}"'
    client = MockLLMClient(
        [
            [
                ToolCallComplete(
                    "find-plan",
                    "Glob",
                    {"pattern": target.name, "path": str(tmp_path)},
                ),
                StreamEnd("tool_use"),
            ],
            [
                ToolCallComplete(
                    "delete-plan",
                    "Bash",
                    {"command": delete_command, "timeout": 5},
                ),
                StreamEnd("tool_use"),
            ],
            [TextDelta("Deleted and verified."), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(client, registry=create_default_registry(), work_dir=tmp_path)

    events = [event async for event in agent.run(f"delete {target.name}")]

    glob_result = next(
        event
        for event in events
        if isinstance(event, ToolResultEvent) and event.tool_name == "Glob"
    )
    assert glob_result.detail == f"plan/{target.name}"
    assert not target.exists()
    assert len(client.snapshots) == 3
    assert any(
        isinstance(event, StreamText) and event.text == "Deleted and verified." for event in events
    )


@pytest.mark.asyncio
async def test_failed_tool_round_may_retry_once() -> None:
    client = MockLLMClient(
        [
            [ToolCallComplete("bad-1", "Missing", {}), StreamEnd("tool_use", 2, 1)],
            [
                ToolCallComplete("call-2", "echo", {"text": "recovered"}),
                StreamEnd("tool_use", 3, 1),
            ],
            [TextDelta("done"), StreamEnd("end_turn", 4, 1)],
        ]
    )
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(client, tools=registry)
    events = [event async for event in agent.run("go")]

    result_events = [
        event for event in events if isinstance(event, ToolUseEvent) and event.status == "result"
    ]
    assert result_events[0].is_error is True
    assert result_events[1].detail == "recovered"
    assert result_events[1].is_error is False
    assert any(isinstance(event, StreamText) and event.text == "done" for event in events)


@pytest.mark.asyncio
async def test_stream_collector_consumes_all_seven_event_types() -> None:
    async def stream() -> AsyncIterator[StreamEvent]:
        events: list[StreamEvent] = [
            TextDelta("text"),
            ThinkingDelta("thought"),
            ThinkingComplete("thought", "sig"),
            ToolCallStart("call", "tool"),
            ToolCallDelta("call", "{}"),
            ToolCallComplete("call", "tool", {}),
            StreamEnd("end_turn", 1, 2),
        ]
        for event in events:
            yield event

    collector = StreamCollector()
    display = [event async for event in collector.consume(stream())]
    response = collector.response()
    assert any(isinstance(event, ThinkingText) for event in display)
    assert response.text == "text"
    assert response.thinking_blocks == [ThinkingBlock("thought", "sig")]
    assert response.tool_calls == [ToolCallComplete("call", "tool", {})]
    assert (response.input_tokens, response.output_tokens) == (1, 2)


@pytest.mark.asyncio
async def test_stop_end_turn(tmp_path: Path) -> None:
    client = MockLLMClient([[TextDelta("done"), StreamEnd("end_turn", 3, 2)]])
    agent = Agent(client, work_dir=tmp_path)
    events = [event async for event in agent.run("hello")]
    assert isinstance(events[-1], LoopComplete)
    assert events[-1] == LoopComplete("end_turn", 3, 2)
    assert any(isinstance(event, UsageEvent) for event in events)


@pytest.mark.asyncio
async def test_stop_max_iterations(tmp_path: Path) -> None:
    client = MockLLMClient(
        [
            [ToolCallComplete(f"call-{index}", "echo", {"text": "x"}), StreamEnd("tool_use")]
            for index in range(2)
        ]
    )
    registry = ToolRegistry()
    registry.register(EchoTool())
    agent = Agent(client, registry=registry, work_dir=tmp_path, max_iterations=2)
    events = [event async for event in agent.run("loop")]
    assert isinstance(events[-1], ErrorEvent)
    assert "max iterations: 2" in events[-1].message


@pytest.mark.asyncio
async def test_stop_cancel(tmp_path: Path) -> None:
    started = asyncio.Event()

    class BlockingClient(MockLLMClient):
        async def stream(
            self,
            conversation: ConversationManager,
            system: str | None = None,
            tools: list[dict[str, Any]] | None = None,
        ) -> AsyncIterator[StreamEvent]:
            del conversation, system, tools
            started.set()
            await asyncio.Event().wait()
            yield StreamEnd("end_turn")

    agent = Agent(BlockingClient([]), work_dir=tmp_path)

    async def collect() -> list[Any]:
        return [event async for event in agent.run("wait")]

    task = asyncio.create_task(collect())
    await asyncio.wait_for(started.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stop_consecutive_unknown_tools(tmp_path: Path) -> None:
    client = MockLLMClient(
        [
            [ToolCallComplete(f"bad-{index}", "missing", {}), StreamEnd("tool_use")]
            for index in range(3)
        ]
    )
    agent = Agent(client, work_dir=tmp_path)
    events = [event async for event in agent.run("loop")]
    assert isinstance(events[-1], ErrorEvent)
    assert "3 consecutive unknown-tool rounds" in events[-1].message


@pytest.mark.asyncio
async def test_concurrent_batch_execution(tmp_path: Path) -> None:
    tool = ParallelEchoTool()
    registry = ToolRegistry()
    registry.register(tool)
    client = MockLLMClient(
        [
            [
                ToolCallComplete("one", tool.name, {"text": "one"}),
                ToolCallComplete("two", tool.name, {"text": "two"}),
                StreamEnd("tool_use"),
            ],
            [TextDelta("done"), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(client, registry=registry, work_dir=tmp_path)
    events = [event async for event in agent.run("parallel")]
    assert tool.max_running == 2
    assert len([event for event in events if isinstance(event, ToolResultEvent)]) == 2


def test_partition_tool_calls() -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(WriteEchoTool())
    calls = [
        ToolCallComplete("1", "echo", {"text": "1"}),
        ToolCallComplete("2", "echo", {"text": "2"}),
        ToolCallComplete("3", "write_echo", {"text": "3"}),
        ToolCallComplete("4", "echo", {"text": "4"}),
        ToolCallComplete("5", "echo", {"text": "5"}),
    ]
    batches = partition_tool_calls(calls, registry)
    assert [len(batch.calls) for batch in batches] == [2, 1, 2]
    assert [batch.concurrent for batch in batches] == [True, False, True]


@pytest.mark.asyncio
async def test_plan_mode_denied_tool_returns_error(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(WriteEchoTool())
    client = MockLLMClient(
        [
            [
                ToolCallComplete("write", "write_echo", {"text": "blocked"}),
                StreamEnd("tool_use"),
            ],
            [TextDelta("kept read-only"), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(client, registry=registry, work_dir=tmp_path)
    agent.set_permission_mode(PermissionMode.PLAN)
    events = [event async for event in agent.run("plan")]
    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert result.is_error is True
    assert "Permission denied" in result.detail


@pytest.mark.asyncio
async def test_plan_mode_only_exposes_read_tools_and_write_plan(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(WriteEchoTool())
    client = MockLLMClient([[TextDelta("plan"), StreamEnd("end_turn")]])
    agent = Agent(client, registry=registry, work_dir=tmp_path)
    agent.set_permission_mode(PermissionMode.PLAN)

    _ = [event async for event in agent.run("make a plan")]

    schemas = client.tool_schemas[0] or []
    names = {schema["name"] for schema in schemas}
    assert names == {"echo", "WritePlan"}
    assert registry.get("write_echo") is not None


@pytest.mark.asyncio
async def test_plan_mode_write_plan_saves_only_current_plan(tmp_path: Path) -> None:
    content = "# Implementation Plan\n\n1. Inspect\n2. Implement\n3. Verify"
    client = MockLLMClient(
        [
            [
                ToolCallComplete("plan-1", "WritePlan", {"content": content}),
                StreamEnd("tool_use"),
            ],
            [TextDelta("Plan is ready."), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(client, work_dir=tmp_path)
    agent.set_permission_mode(PermissionMode.PLAN)

    events = [event async for event in agent.run("make a plan")]

    plan_path = agent._get_plan_path()
    assert plan_path.read_text(encoding="utf-8") == content
    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert result.is_error is False
    assert str(plan_path) in result.detail


@pytest.mark.asyncio
async def test_write_plan_rejects_any_caller_supplied_path(tmp_path: Path) -> None:
    agent = Agent(MockLLMClient([]), work_dir=tmp_path)
    agent.set_permission_mode(PermissionMode.PLAN)
    redirected = tmp_path / "redirected.md"

    result = await agent.registry.execute(
        "WritePlan",
        {"content": "# Plan", "path": str(redirected)},
    )

    assert result.is_error is True
    assert "extra_forbidden" in result.output
    assert not agent._get_plan_path().exists()
    assert not redirected.exists()


@pytest.mark.asyncio
async def test_plan_mode_cannot_be_bypassed_by_allow_always_rule(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(WriteEchoTool())
    rules = RuleEngine()
    rules.append_local_rule(Rule("write_echo", "*"))
    checker = PermissionChecker(rule_engine=rules)
    client = MockLLMClient(
        [
            [
                ToolCallComplete("write", "write_echo", {"text": "blocked"}),
                StreamEnd("tool_use"),
            ],
            [TextDelta("stayed read-only"), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(
        client,
        registry=registry,
        work_dir=tmp_path,
        permission_checker=checker,
    )
    agent.set_permission_mode(PermissionMode.PLAN)

    events = [event async for event in agent.run("plan")]

    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert result.is_error is True
    assert "Permission denied" in result.detail


def test_do_mode_restores_full_tool_schemas_and_hides_write_plan(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(WriteEchoTool())
    agent = Agent(MockLLMClient([]), registry=registry, work_dir=tmp_path)

    agent.set_permission_mode(PermissionMode.PLAN)
    plan_names = {schema["name"] for schema in registry.get_all_schemas()}
    agent.set_permission_mode(PermissionMode.DEFAULT)
    normal_names = {schema["name"] for schema in registry.get_all_schemas()}

    assert plan_names == {"echo", "WritePlan"}
    assert normal_names == {"echo", "write_echo"}
    assert registry.get("WritePlan") is None


@pytest.mark.asyncio
async def test_do_mode_supersedes_persisted_plan_reminders(tmp_path: Path) -> None:
    client = MockLLMClient([[TextDelta("Executing now."), StreamEnd("end_turn")]])
    registry = ToolRegistry()
    registry.register(EchoTool())
    registry.register(WriteEchoTool())
    agent = Agent(client, registry=registry, work_dir=tmp_path)
    conversation = ConversationManager()
    conversation.add_system_reminder("Plan Mode remains active. Stay read-only.")
    conversation.add_user_message("continue")

    agent.set_permission_mode(PermissionMode.PLAN)
    agent.set_permission_mode(PermissionMode.DEFAULT)
    _ = [event async for event in agent.run(conversation)]

    snapshot_text = "\n".join(message.content for message in client.snapshots[0])
    assert "Plan Mode is no longer active" in snapshot_text
    assert "Ignore earlier Plan Mode reminders" in snapshot_text
    names = {schema["name"] for schema in client.tool_schemas[0] or []}
    assert names == {"echo", "write_echo"}


def test_plan_mode_filters_deferred_write_tool_search() -> None:
    class DeferredWriteTool(WriteEchoTool):
        name = "deferred_write"
        should_defer = True

    registry = ToolRegistry()
    registry.register(DeferredWriteTool())
    registry.set_plan_mode(True)

    assert registry.get_deferred_tool_names() == []
    assert registry.search_deferred("write") == []
    assert registry.find_deferred_by_names(["deferred_write"]) == []


@pytest.mark.asyncio
async def test_permission_request_allow_always(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(WriteEchoTool())
    checker = PermissionChecker(
        rule_engine=RuleEngine(tmp_path / "rules.json"),
        ask_for_writes=True,
    )
    client = MockLLMClient(
        [
            [
                ToolCallComplete("write", "write_echo", {"text": "approved"}),
                StreamEnd("tool_use"),
            ],
            [TextDelta("done"), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(
        client,
        registry=registry,
        work_dir=tmp_path,
        permission_checker=checker,
    )
    events: list[Any] = []
    async for event in agent.run("write"):
        events.append(event)
        if isinstance(event, PermissionRequest):
            event.future.set_result(PermissionResponse.ALLOW_ALWAYS)
    assert any(isinstance(event, PermissionRequest) for event in events)
    assert checker.rule_engine.rules[0].tool == "write_echo"
    assert (tmp_path / "rules.json").is_file()


@pytest.mark.asyncio
async def test_max_tokens_escalation(tmp_path: Path) -> None:
    client = MockLLMClient(
        [
            [TextDelta("partial"), StreamEnd("max_tokens", 5, 8)],
            [TextDelta("complete"), StreamEnd("end_turn", 6, 2)],
        ]
    )
    agent = Agent(client, work_dir=tmp_path)
    events = [event async for event in agent.run("continue")]
    assert client.max_output_tokens == MAX_TOKENS_CEILING
    retry = next(event for event in events if isinstance(event, RetryEvent))
    assert retry.reason == "max_tokens escalation"
    assert isinstance(events[-1], LoopComplete)


def test_system_prompt_normal(tmp_path: Path) -> None:
    agent = Agent(MockLLMClient([]), work_dir=tmp_path, system="base prompt")
    prompt = agent._system_prompt()
    assert "base prompt" in prompt
    assert "coordinator" not in prompt
    assert "search subdirectories recursively" in prompt
    assert "After a mutation, verify the result" in prompt
    assert "do not emit colored emoji" in prompt


def test_system_prompt_coordinator(tmp_path: Path) -> None:
    agent = Agent(MockLLMClient([]), work_dir=tmp_path, coordinator_mode=True)
    assert "You are the coordinator" in agent._system_prompt()


@pytest.mark.asyncio
async def test_system_prompt_plan(tmp_path: Path) -> None:
    client = MockLLMClient([[TextDelta("plan"), StreamEnd("end_turn")]])
    agent = Agent(client, work_dir=tmp_path)
    agent.set_permission_mode(PermissionMode.PLAN)
    _ = [event async for event in agent.run("make a plan")]
    snapshot_text = "\n".join(message.content for message in client.snapshots[0])
    assert "Plan Mode is active" in snapshot_text
    assert str(tmp_path / "plan") in snapshot_text


def test_plan_mode_sparse_reminder(tmp_path: Path) -> None:
    path = tmp_path / "plan.md"
    first = build_plan_mode_reminder(path, False, 1)
    sparse = build_plan_mode_reminder(path, False, 2)
    periodic = build_plan_mode_reminder(path, False, _REMINDER_INTERVAL)
    assert "Plan Mode is active" in first
    assert "remains active" in sparse
    assert "Plan Mode is active" in periodic


def test_plan_mode(tmp_path: Path) -> None:
    agent = Agent(MockLLMClient([]), work_dir=tmp_path)
    agent.set_permission_mode(PermissionMode.PLAN)
    first = agent._get_plan_path()
    second = agent._get_plan_path()
    assert agent.permission_mode is PermissionMode.PLAN
    assert first == second
    assert first.parent == tmp_path / "plan"
    assert first.parent.is_dir()
    assert first.suffix == ".md"


@pytest.mark.asyncio
async def test_environment_context(tmp_path: Path) -> None:
    client = MockLLMClient([[TextDelta("ok"), StreamEnd("end_turn")]])
    agent = Agent(client, work_dir=tmp_path)
    _ = [event async for event in agent.run("hello")]
    environment = client.snapshots[0][0].content
    assert environment.startswith("<environment>")
    assert str(tmp_path) in environment


@pytest.mark.asyncio
async def test_hook_lifecycle_events(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    hook_engine = HookEngine()
    hook_names = (
        "session_start",
        "turn_start",
        "pre_send",
        "post_receive",
        "pre_tool_use",
        "post_tool_use",
        "turn_end",
        "session_end",
    )
    for name in hook_names:
        hook_engine.register(
            name,
            lambda context, name=name: HookResult(notification=f"{name}:{context.event}"),
        )
    client = MockLLMClient(
        [
            [ToolCallComplete("echo", "echo", {"text": "ok"}), StreamEnd("tool_use")],
            [TextDelta("done"), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(
        client,
        registry=registry,
        work_dir=tmp_path,
        hook_engine=hook_engine,
    )
    events = [event async for event in agent.run("hooks")]
    emitted = {event.hook_name for event in events if hasattr(event, "hook_name")}
    assert emitted == set(hook_names)


@pytest.mark.asyncio
async def test_large_tool_result_is_persisted(tmp_path: Path) -> None:
    class LargeTool(EchoTool):
        name = "large"

        async def execute(self, params: EchoParams) -> ToolResult:
            del params
            return ToolResult("x" * 9_000)

    registry = ToolRegistry()
    registry.register(LargeTool())
    client = MockLLMClient(
        [
            [ToolCallComplete("large-1", "large", {"text": "go"}), StreamEnd("tool_use")],
            [TextDelta("done"), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(client, registry=registry, work_dir=tmp_path)
    events = [event async for event in agent.run("large")]
    result = next(event for event in events if isinstance(event, ToolResultEvent))
    # The event can render the full result once, while persistent history keeps only preview/path.
    assert len(result.detail) == 9_000
    saved = agent.session_dir / "large-1.txt"
    assert saved.is_file()
    assert len(saved.read_text(encoding="utf-8")) == 9_000
    assert "large-1" in agent.replacement_state.replacements
    assert agent.replacement_state.replacements["large-1"].startswith("<persisted-output>")
    stored = next(
        tool_result
        for message in agent.conversation.history
        for tool_result in message.tool_results
        if tool_result.tool_use_id == "large-1"
    )
    assert stored.content.startswith("<persisted-output>")
    assert str(saved) in stored.content
    assert len(stored.content) < 9_000
