"""CH12 declarative Hook system and Agent-loop integration tests."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from mewcode.agent import (
    Agent,
    HookEvent,
    PermissionRequest,
    PermissionResponse,
    ToolResultEvent,
)
from mewcode.client import LLMClient
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.hooks import (
    Action,
    ActionResult,
    Condition,
    ConditionGroup,
    ConditionParseError,
    Hook,
    HookConfigError,
    HookContext,
    HookEngine,
    LifecycleEvent,
    ToolRejectedError,
    execute_action,
    execute_agent,
    execute_command,
    execute_http,
    execute_prompt,
    load_hooks,
    parse_condition,
)
from mewcode.permissions import PathSandbox, PermissionChecker, PermissionMode, RuleEngine
from mewcode.tools import ToolRegistry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, Tool, ToolCallComplete, ToolResult


def context(**updates: Any) -> HookContext:
    values: dict[str, Any] = {
        "event_name": "pre_tool_use",
        "tool_name": "Bash",
        "tool_args": {"command": "echo ok", "meta": {"kind": "safe"}},
        "file_path": "src/app.py",
        "message": "hello",
        "error": "boom",
    }
    values.update(updates)
    return HookContext(**values)


class TestLifecycleEvent:
    def test_contains_exactly_fifteen_string_events(self) -> None:
        assert len(LifecycleEvent) == 15
        assert LifecycleEvent.SESSION_START == "session_start"
        assert LifecycleEvent.COMMAND_EXECUTE == "command_execute"


class TestHookContext:
    def test_get_field_supports_event_tool_and_nested_args(self) -> None:
        item = context()
        assert item.get_field("event") == "pre_tool_use"
        assert item.get_field("tool") == "Bash"
        assert item.get_field("args.command") == "echo ok"
        assert item.get_field("args.meta.kind") == "safe"

    def test_expand_replaces_all_context_variables(self) -> None:
        expanded = context().expand(
            "$EVENT|$TOOL_NAME|$FILE_PATH|$MESSAGE|$ERROR|$TOOL_ARGS.command"
        )
        assert expanded == "pre_tool_use|Bash|src/app.py|hello|boom|echo ok"

    def test_undefined_variables_expand_to_empty_string(self) -> None:
        assert context().expand("<$TOOL_ARGS.missing><$UNKNOWN>") == "<><>"


class TestParseCondition:
    @pytest.mark.parametrize("operator", ["==", "!=", "=~", "~="])
    def test_parses_each_leaf_operator(self, operator: str) -> None:
        parsed = parse_condition(f'tool {operator} "Bash"')
        assert isinstance(parsed, Condition)
        assert parsed.operator == operator

    def test_parses_and_group(self) -> None:
        parsed = parse_condition('tool == "Bash" && args.command ~= "echo*"')
        assert isinstance(parsed, ConditionGroup)
        assert parsed.logic == "and"
        assert len(parsed.conditions) == 2

    def test_parses_or_group(self) -> None:
        parsed = parse_condition('tool == "Bash" || tool == "WriteFile"')
        assert isinstance(parsed, ConditionGroup)
        assert parsed.logic == "or"

    def test_rejects_mixed_boolean_operators(self) -> None:
        with pytest.raises(ConditionParseError, match="Cannot mix"):
            parse_condition("tool == Bash && event == pre_tool_use || args.x == 1")

    def test_empty_expression_is_unconditional(self) -> None:
        assert parse_condition(None) is None
        assert parse_condition("   ") is None

    def test_rejects_expression_without_operator(self) -> None:
        with pytest.raises(ConditionParseError, match="no supported operator"):
            parse_condition("tool is Bash")

    def test_strips_regex_slashes(self) -> None:
        parsed = parse_condition(r"args.command =~ /rm\s+-rf/")
        assert isinstance(parsed, Condition)
        assert parsed.value == r"rm\s+-rf"


class TestConditionEvaluate:
    def test_exact_and_not_equal(self) -> None:
        item = context()
        assert Condition("tool", "==", "Bash").evaluate(item)
        assert Condition("tool", "!=", "WriteFile").evaluate(item)

    def test_regex_and_invalid_regex(self) -> None:
        item = context(tool_args={"command": "rm -rf ./build"})
        assert Condition("args.command", "=~", r"rm\s+-rf").evaluate(item)
        assert not Condition("args.command", "=~", "[").evaluate(item)

    def test_glob(self) -> None:
        assert Condition("file_path", "~=", "src/*.py").evaluate(context())


class TestConditionGroupEvaluate:
    def test_and_requires_every_condition(self) -> None:
        group = ConditionGroup(
            [Condition("tool", "==", "Bash"), Condition("event", "==", "pre_tool_use")],
            "and",
        )
        assert group.evaluate(context())
        assert not group.evaluate(context(tool_name="ReadFile"))

    def test_or_requires_one_condition(self) -> None:
        group = ConditionGroup(
            [Condition("tool", "==", "ReadFile"), Condition("tool", "==", "Bash")],
            "or",
        )
        assert group.evaluate(context())
        assert not group.evaluate(context(tool_name="WriteFile"))

    def test_empty_and_group_is_true(self) -> None:
        assert ConditionGroup([], "and").evaluate(context())


class TestCommandExecutor:
    @pytest.mark.asyncio
    async def test_runs_shell_command_and_expands_context(self) -> None:
        command = f'"{sys.executable}" -c "print(\'value=$MESSAGE\')"'
        result = await execute_command(Action("command", command=command), context())
        assert result.success
        assert "value=hello" in result.output

    @pytest.mark.asyncio
    async def test_timeout_kills_and_waits_for_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeProcess:
            returncode = None

            def __init__(self) -> None:
                self.killed = False
                self.waited = False

            async def communicate(self) -> tuple[bytes, None]:
                await asyncio.sleep(60)
                return b"", None

            def kill(self) -> None:
                self.killed = True

            async def wait(self) -> int:
                self.waited = True
                return -1

        process = FakeProcess()

        async def create(*args: Any, **kwargs: Any) -> FakeProcess:
            del args, kwargs
            return process

        async def timeout(*args: Any, **kwargs: Any) -> Any:
            awaitable = args[0]
            if hasattr(awaitable, "close"):
                awaitable.close()
            del kwargs
            raise TimeoutError

        monkeypatch.setattr(asyncio, "create_subprocess_shell", create)
        monkeypatch.setattr(asyncio, "wait_for", timeout)
        result = await execute_command(Action("command", command="sleep", timeout=1), context())
        assert not result.success
        assert "timed out after 1s" in result.output
        assert process.killed and process.waited


class TestPromptExecutor:
    @pytest.mark.asyncio
    async def test_returns_expanded_message(self) -> None:
        result = await execute_prompt(Action("prompt", message="Read $FILE_PATH"), context())
        assert result == ActionResult("Read src/app.py", True)


class TestHttpExecutor:
    @pytest.mark.asyncio
    async def test_mock_request_expands_json_and_limits_response(self) -> None:
        captured: dict[str, Any] = {}

        class Response:
            status = 200

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: Any) -> None:
                del args

            def getcode(self) -> int:
                return self.status

            def read(self, amount: int) -> bytes:
                assert amount == 500
                return b"ok"

        def fake_urlopen(request: Any, timeout: int) -> Response:
            captured["request"] = request
            captured["timeout"] = timeout
            return Response()

        action = Action(
            "http",
            url="https://example.test/$EVENT",
            body={"tool": "$TOOL_NAME"},
        )
        with patch("mewcode.hooks.executors.urlopen", fake_urlopen):
            result = await execute_http(action, context())
        assert result == ActionResult("HTTP 200: ok", True)
        assert captured["timeout"] == 30
        assert captured["request"].data == b'{"tool": "Bash"}'
        assert captured["request"].headers["Content-type"] == "application/json"


class TestAgentExecutor:
    @pytest.mark.asyncio
    async def test_returns_ch12_placeholder(self) -> None:
        result = await execute_agent(Action("agent", prompt="check"), context())
        assert result.success
        assert result.output == "agent executor not yet implemented"


class TestExecuteAction:
    @pytest.mark.asyncio
    async def test_dispatches_known_type(self) -> None:
        result = await execute_action(Action("prompt", message="$EVENT"), context())
        assert result.output == "pre_tool_use"

    @pytest.mark.asyncio
    async def test_unknown_type_is_structured_failure(self) -> None:
        result = await execute_action(Action("missing"), context())
        assert not result.success
        assert "Unknown action type" in result.output


class TestLoadHooks:
    def test_loads_complete_configuration_and_auto_id(self) -> None:
        hooks = load_hooks(
            [
                {
                    "event": "post_tool_use",
                    "condition": 'tool == "Bash"',
                    "once": True,
                    "async": True,
                    "action": {"type": "http", "url": "https://example.test", "timeout": 5},
                }
            ]
        )
        assert hooks[0].id == "post_tool_use_0"
        assert hooks[0].once and hooks[0].async_exec
        assert hooks[0].action.timeout == 5

    def test_none_and_empty_are_valid(self) -> None:
        assert load_hooks(None) == []
        assert load_hooks([]) == []

    @pytest.mark.parametrize(
        ("raw", "message"),
        [
            ([{"event": "unknown", "action": {"type": "prompt", "message": "x"}}], "event"),
            ([{"event": "startup", "action": {"type": "missing"}}], "action type"),
            ([{"event": "startup", "action": {"type": "command"}}], "requires 'command'"),
            (
                [
                    {
                        "event": "startup",
                        "reject": True,
                        "action": {"type": "prompt", "message": "x"},
                    }
                ],
                "reject",
            ),
            (
                [
                    {
                        "event": "pre_tool_use",
                        "async": True,
                        "action": {"type": "prompt", "message": "x"},
                    }
                ],
                "async",
            ),
            (
                [
                    {
                        "event": "startup",
                        "action": {"type": "prompt", "message": "x", "timeout": 0},
                    }
                ],
                "positive integer",
            ),
            (
                [
                    {
                        "id": "mixed",
                        "event": "startup",
                        "condition": "tool == Bash && event == startup || args.x == 1",
                        "action": {"type": "prompt", "message": "x"},
                    }
                ],
                "hook 'mixed'",
            ),
        ],
    )
    def test_invalid_configuration_is_located(
        self, raw: list[dict[str, Any]], message: str
    ) -> None:
        with pytest.raises(HookConfigError, match=message):
            load_hooks(raw)


class TestHookEngine:
    def test_find_matching_hooks_filters_event_condition_and_once(self) -> None:
        matching = Hook(
            "matching",
            "pre_tool_use",
            Action("prompt", message="x"),
            condition=Condition("tool", "==", "Bash"),
            once=True,
        )
        other = Hook("other", "startup", Action("prompt", message="y"))
        engine = HookEngine([matching, other])
        assert engine.find_matching_hooks("pre_tool_use", context()) == [matching]
        matching.mark_executed()
        assert engine.find_matching_hooks("pre_tool_use", context()) == []

    @pytest.mark.asyncio
    async def test_prompt_is_collected_once_and_notification_drains(self) -> None:
        hook = Hook("prompt", "session_start", Action("prompt", message="Read $FILE_PATH"))
        engine = HookEngine([hook])
        await engine.run_hooks("session_start", context(event_name="session_start"))
        assert engine.get_prompt_messages() == ["Read src/app.py"]
        assert engine.get_prompt_messages() == []
        assert engine.drain_notifications()[0].hook_id == "prompt"
        assert engine.drain_notifications() == []

    @pytest.mark.asyncio
    async def test_reject_returns_typed_error_and_stops_at_first_match(self) -> None:
        hooks = [
            Hook("deny", "pre_tool_use", Action("prompt", message="blocked"), reject=True),
            Hook("later", "pre_tool_use", Action("prompt", message="later"), reject=True),
        ]
        rejection = await HookEngine(hooks).run_pre_tool_hooks(context())
        assert isinstance(rejection, ToolRejectedError)
        assert rejection.reason == "blocked"
        assert rejection.hook_id == "deny"
        assert not hooks[1].executed

    @pytest.mark.asyncio
    async def test_non_reject_pre_tool_hook_allows_execution(self) -> None:
        engine = HookEngine(
            [Hook("audit", "pre_tool_use", Action("prompt", message="audit"), reject=False)]
        )
        assert await engine.run_pre_tool_hooks(context()) is None

    @pytest.mark.asyncio
    async def test_action_exception_is_isolated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def broken(action: Action, hook_context: HookContext) -> ActionResult:
            del action, hook_context
            raise RuntimeError("broken hook")

        monkeypatch.setattr("mewcode.hooks.engine.execute_action", broken)
        engine = HookEngine([Hook("broken", "startup", Action("prompt", message="x"))])
        await engine.run_hooks("startup", context(event_name="startup"))
        notification = engine.drain_notifications()[0]
        assert not notification.success
        assert "broken hook" in notification.output

    @pytest.mark.asyncio
    async def test_async_hook_returns_without_waiting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def delayed(action: Action, hook_context: HookContext) -> ActionResult:
            del action, hook_context
            started.set()
            await release.wait()
            return ActionResult("done")

        monkeypatch.setattr("mewcode.hooks.engine.execute_action", delayed)
        engine = HookEngine(
            [Hook("background", "turn_end", Action("command", command="x"), async_exec=True)]
        )
        await engine.run_hooks("turn_end", context(event_name="turn_end"))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert engine.drain_notifications() == []
        release.set()
        await engine.wait_background()
        assert engine.drain_notifications()[0].output == "done"


class ScriptedClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        config = ProviderConfig.model_validate(
            {
                "protocol": "anthropic",
                "model": "claude-sonnet-4-6",
                "base_url": "https://api.anthropic.com",
                "api_key": "test",
            }
        )
        super().__init__(config)
        self.responses = responses
        self.system_prompts: list[str] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del conversation, tools
        self.system_prompts.append(system or "")
        for item in self.responses.pop(0):
            yield item


class BashParams(BaseModel):
    command: str


class RecordingBash(Tool):
    name = "Bash"
    description = "Test command"
    params_model = BashParams
    category = "command"

    def __init__(self) -> None:
        self.executed = False

    async def execute(self, params: BashParams) -> ToolResult:
        self.executed = True
        return ToolResult(params.command)


class WriteParams(BaseModel):
    file_path: str
    content: str


class RecordingWrite(Tool):
    name = "WriteFile"
    description = "Test write"
    params_model = WriteParams
    category = "write"

    async def execute(self, params: WriteParams) -> ToolResult:
        return ToolResult(f"wrote {params.file_path}")


class TestAgentHookIntegration:
    @pytest.mark.asyncio
    async def test_pre_tool_reject_skips_tool_and_reaches_model(self, tmp_path: Path) -> None:
        hooks = load_hooks(
            [
                {
                    "id": "dangerous-delete",
                    "event": "pre_tool_use",
                    "condition": r'tool == "Bash" && args.command =~ /rm\s+-rf/',
                    "reject": True,
                    "action": {"type": "prompt", "message": "destructive delete blocked"},
                }
            ]
        )
        tool = RecordingBash()
        registry = ToolRegistry()
        registry.register(tool)
        client = ScriptedClient(
            [
                [
                    ToolCallComplete("danger", "Bash", {"command": "rm -rf /"}),
                    StreamEnd("tool_use"),
                ],
                [TextDelta("used a safer strategy"), StreamEnd("end_turn")],
            ]
        )
        agent = Agent(
            client,
            registry=registry,
            hook_engine=HookEngine(hooks),
            work_dir=tmp_path,
        )
        events = [event async for event in agent.run("explain cleanup policy")]
        results = [event for event in events if isinstance(event, ToolResultEvent)]
        assert not tool.executed
        assert results[0].is_error
        assert results[0].detail == "Hook rejected: destructive delete blocked"
        assert "destructive delete blocked" in client.system_prompts[1]

    @pytest.mark.asyncio
    async def test_session_prompt_is_injected_before_first_provider_call(
        self, tmp_path: Path
    ) -> None:
        hook = Hook(
            "architecture",
            "session_start",
            Action("prompt", message="Read ARCHITECTURE.md before editing."),
        )
        client = ScriptedClient([[TextDelta("ok"), StreamEnd("end_turn")]])
        agent = Agent(client, hook_engine=HookEngine([hook]), work_dir=tmp_path)
        _ = [event async for event in agent.run("start")]
        assert "Read ARCHITECTURE.md before editing." in client.system_prompts[0]

    @pytest.mark.asyncio
    async def test_permission_and_file_change_events_wrap_write(self, tmp_path: Path) -> None:
        hooks = [
            Hook(
                "permission-audit",
                "permission_request",
                Action("prompt", message="approval for $TOOL_NAME"),
            ),
            Hook(
                "format-file",
                "file_change",
                Action("prompt", message="format $FILE_PATH"),
            ),
        ]
        registry = ToolRegistry()
        registry.register(RecordingWrite())
        checker = PermissionChecker(
            sandbox=PathSandbox(tmp_path),
            rule_engine=RuleEngine(),
            mode=PermissionMode.DEFAULT,
        )
        client = ScriptedClient([[TextDelta("unused"), StreamEnd("end_turn")]])
        agent = Agent(
            client,
            registry=registry,
            hook_engine=HookEngine(hooks),
            permission_checker=checker,
            work_dir=tmp_path,
        )
        target = tmp_path / "module.py"
        iterator = agent._execute_tool(
            ToolCallComplete(
                "write",
                "WriteFile",
                {"file_path": str(target), "content": "pass"},
            )
        )
        permission_hook = await anext(iterator)
        request = await anext(iterator)
        assert isinstance(permission_hook, HookEvent)
        assert permission_hook.hook_name == "permission-audit"
        assert isinstance(request, PermissionRequest)
        request.future.set_result(PermissionResponse.ALLOW)
        remaining = [item async for item in iterator]
        hook_names = {item.hook_name for item in remaining if isinstance(item, HookEvent)}
        assert "format-file" in hook_names
        assert "format " + str(target) in agent.hook_prompts
