"""CH13 definition, fork, filtering, task, trace, command and AgentTool tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from mewcode.agent import Agent
from mewcode.agents.fork import FORK_BOILERPLATE_TAG, ForkError, build_forked_messages
from mewcode.agents.loader import AgentLoader
from mewcode.agents.notification import (
    MAX_NOTIFICATION_RESULT_LENGTH,
    format_task_notification,
    inject_task_notifications,
)
from mewcode.agents.parser import AgentDefinition, AgentParseError, parse_agent_file
from mewcode.agents.task_manager import TaskManager
from mewcode.agents.tool_filter import resolve_agent_tools
from mewcode.agents.trace import TraceRegistry
from mewcode.client import LLMClient
from mewcode.commands.handlers.tasks import handle_task, handle_tasks, handle_trace
from mewcode.commands.registry import CommandContext
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager, ThinkingBlock, ToolUseBlock
from mewcode.permissions import PermissionMode
from mewcode.tools import ToolRegistry, create_default_registry
from mewcode.tools.agent_tool import AgentTool, AgentToolParams
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, Tool, ToolResult


def _provider() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "protocol": "anthropic",
            "model": "claude-sonnet-4-6",
            "base_url": "https://api.anthropic.com",
            "api_key": "test-key",
        }
    )


class ScriptedClient(LLMClient):
    def __init__(self, scripts: list[list[StreamEvent]], *, delay: float = 0.0) -> None:
        super().__init__(_provider())
        self.scripts = list(scripts)
        self.delay = delay
        self.snapshots: list[ConversationManager] = []
        self.systems: list[str | None] = []
        self.schemas: list[list[dict[str, Any]] | None] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.snapshots.append(conversation)
        self.systems.append(system)
        self.schemas.append(tools)
        if self.delay:
            await asyncio.sleep(self.delay)
        for event in self.scripts.pop(0):
            yield event


class EmptyParams(BaseModel):
    pass


class NamedTool(Tool):
    params_model = EmptyParams
    category = "read"

    def __init__(self, name: str, *, deferred: bool = False) -> None:
        self.name = name  # type: ignore[misc]
        self.description = name  # type: ignore[misc]
        self.should_defer = deferred  # type: ignore[misc]

    async def execute(self, params: EmptyParams) -> ToolResult:
        return ToolResult(self.name)


def _write_agent(path: Path, *, name: str = "custom", extra: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "description: Test worker\n"
        f"{extra}"
        "---\n"
        "Follow the test task.\n",
        encoding="utf-8",
    )
    return path


class TestAgentParser:
    def test_parse_valid_agent_and_preserve_extra(self, tmp_path: Path) -> None:
        path = _write_agent(
            tmp_path / "a.md",
            name="worker-1",
            extra=(
                "tools: [ReadFile, Grep]\n"
                "disallowedTools: [Bash]\n"
                "model: sonnet\n"
                "maxTurns: 7\n"
                "permissionMode: dontAsk\n"
                "background: true\n"
                "skills: [review]\n"
            ),
        )
        definition = parse_agent_file(path, source="plugin")
        assert definition.agent_type == "worker-1"
        assert definition.tools == ("ReadFile", "Grep")
        assert definition.disallowed_tools == ("Bash",)
        assert definition.max_turns == 7 and definition.background is True
        assert definition.source == "plugin"
        assert definition.metadata["skills"] == ["review"]

    @pytest.mark.parametrize(
        ("header", "message"),
        [
            ("description: x", "name"),
            ("name: x", "description"),
            ("name: x\ndescription: x\nmodel: giant", "model"),
            ("name: x\ndescription: x\npermissionMode: yolo", "permissionMode"),
            ("name: x\ndescription: x\nisolation: remote", "isolation"),
            ("name: x\ndescription: x\nmaxTurns: 0", "maxTurns"),
        ],
    )
    def test_invalid_metadata(self, tmp_path: Path, header: str, message: str) -> None:
        path = tmp_path / "bad.md"
        path.write_text(f"---\n{header}\n---\nbody", encoding="utf-8")
        with pytest.raises(AgentParseError, match=message):
            parse_agent_file(path)

    def test_requires_frontmatter_and_body(self, tmp_path: Path) -> None:
        plain = tmp_path / "plain.md"
        plain.write_text("no header", encoding="utf-8")
        with pytest.raises(AgentParseError, match="frontmatter"):
            parse_agent_file(plain)
        empty = tmp_path / "empty.md"
        empty.write_text("---\nname: x\ndescription: x\n---\n", encoding="utf-8")
        with pytest.raises(AgentParseError, match="body"):
            parse_agent_file(empty)


class TestAgentLoader:
    def test_loads_three_default_builtins(self, tmp_path: Path, monkeypatch: Any) -> None:
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
        loader = AgentLoader(tmp_path)
        names = {item.agent_type for item in loader.load_all().values()}
        assert {"Explore", "Plan", "general-purpose"} <= names
        assert "Verification" not in names

    def test_verification_flag(self, tmp_path: Path) -> None:
        loader = AgentLoader(tmp_path, enable_verification=True)
        loader.load_all()
        assert loader.get("verification") is not None

    def test_project_overrides_user_builtin_and_plugin(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        home = tmp_path / "home"
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        _write_agent(home / ".mewcode/agents/plan.md", name="Plan")
        plugin = tmp_path / "plugin"
        _write_agent(plugin / "plan.md", name="Plan")
        project = _write_agent(tmp_path / ".mewcode/agents/plan.md", name="Plan")
        loader = AgentLoader(tmp_path, plugin_dirs=[plugin])
        loader.load_all()
        assert loader.get("plan").file_path == project.resolve()  # type: ignore[union-attr]
        assert loader.get("plan").source == "project"  # type: ignore[union-attr]

    def test_plugin_unique_definition_loads_last(self, tmp_path: Path) -> None:
        plugin = tmp_path / "plugin"
        _write_agent(plugin / "plug.md", name="plug")
        loader = AgentLoader(tmp_path, plugin_dirs=[plugin])
        loader.load_all()
        assert loader.get("plug").source == "plugin"  # type: ignore[union-attr]

    def test_hot_reload_and_invalid_fallback(self, tmp_path: Path) -> None:
        path = _write_agent(tmp_path / ".mewcode/agents/hot.md", name="hot")
        loader = AgentLoader(tmp_path)
        loader.load_all()
        path.write_text(
            "---\nname: hot\ndescription: changed\n---\nnew body", encoding="utf-8"
        )
        assert loader.get("hot").when_to_use == "changed"  # type: ignore[union-attr]
        path.write_text("broken", encoding="utf-8")
        assert loader.get("hot").when_to_use == "changed"  # type: ignore[union-attr]


class TestToolFilter:
    def _registry(self) -> ToolRegistry:
        registry = create_default_registry()
        registry.register(NamedTool("Agent"))
        registry.register(NamedTool("AskUserQuestion"))
        registry.register(NamedTool("TaskCreate"))
        registry.register(NamedTool("mcp_context_search", deferred=True))
        registry.mark_discovered("mcp_context_search")
        return registry

    def test_global_disallowed_and_mcp_passthrough(self) -> None:
        filtered = resolve_agent_tools(self._registry())
        names = {tool.name for tool in filtered.list_tools()}
        assert "Agent" not in names and "AskUserQuestion" not in names
        assert "mcp_context_search" in names
        assert filtered.is_discovered("mcp_context_search")

    def test_definition_white_and_blacklists(self) -> None:
        definition = AgentDefinition(
            "x", "x", "x", tools=("ReadFile", "Bash"), disallowed_tools=("Bash",)
        )
        filtered = resolve_agent_tools(self._registry(), definition)
        names = {tool.name for tool in filtered.list_tools()}
        assert "ReadFile" in names and "Bash" not in names
        assert "mcp_context_search" in names

    def test_background_and_custom_layers(self) -> None:
        definition = AgentDefinition("x", "x", "x", source="project")
        names = {
            tool.name
            for tool in resolve_agent_tools(
                self._registry(), definition, is_background=True
            ).list_tools()
        }
        assert "ReadFile" in names
        assert "TaskCreate" not in names

    def test_file_tools_are_isolated_instances(self) -> None:
        registry = self._registry()
        filtered = resolve_agent_tools(registry)
        assert filtered.get("ReadFile") is not registry.get("ReadFile")


class TestForkMode:
    def test_preserves_all_history_and_is_deep_copy(self) -> None:
        conversation = ConversationManager()
        conversation.add_user_message("hello")
        conversation.add_assistant_message(
            "answer",
            thinking_blocks=[ThinkingBlock("thought", "sig")],
        )
        forked = build_forked_messages(conversation, "investigate")
        assert forked.history[:2] == conversation.history
        assert forked.history[0] is not conversation.history[0]
        assert FORK_BOILERPLATE_TAG in forked.history[-1].content

    def test_wraps_pending_tool_use(self) -> None:
        conversation = ConversationManager()
        conversation.add_assistant_message("", [ToolUseBlock("t1", "ReadFile", {})])
        forked = build_forked_messages(conversation, "continue")
        assert forked.history[-2].tool_results[0].content == "interrupted"
        assert forked.history[-2].tool_results[0].is_error is True

    def test_rejects_nested_fork(self) -> None:
        conversation = ConversationManager()
        conversation.add_user_message(FORK_BOILERPLATE_TAG)
        with pytest.raises(ForkError, match="Cannot fork"):
            build_forked_messages(conversation, "again")


class TestTraceRegistry:
    def test_create_update_complete_and_totals(self) -> None:
        registry = TraceRegistry()
        first = registry.create("Explore", "parent")
        second = registry.create("Plan", first.agent_id, first.trace_id)
        registry.update(first.agent_id, input_tokens=4, output_tokens=2)
        registry.update(second.agent_id, input_tokens=5, output_tokens=3)
        registry.complete(first.agent_id)
        assert registry.get_tree(first.trace_id) == [first, second]
        assert registry.get_total_tokens(first.trace_id) == (9, 5)
        assert first.status == "completed" and first.end_time is not None

    def test_missing_id_is_noop(self) -> None:
        registry = TraceRegistry()
        registry.update("missing", status="failed")
        registry.complete("missing")
        assert registry.get("missing") is None


class StubAgent:
    def __init__(self, result: str = "done", *, delay: float = 0, fail: bool = False) -> None:
        self.agent_id = "stub"
        self.total_input_tokens = 7
        self.total_output_tokens = 3
        self.result = result
        self.delay = delay
        self.fail = fail

    async def run_to_completion(self, prompt: str, conversation: Any = None) -> str:
        del prompt, conversation
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError("boom")
        return self.result


class TestTaskManager:
    @pytest.mark.asyncio
    async def test_launch_complete_poll_and_trace(self) -> None:
        traces = TraceRegistry()
        manager = TaskManager(traces)
        node = traces.create("Explore", "parent")
        task = await manager.launch(
            StubAgent(),  # type: ignore[arg-type]
            "work",
            agent_type="Explore",
            description="scan",
            trace_node=node,
        )
        await asyncio.sleep(0.01)
        assert task.status == "completed" and task.result == "done"
        assert manager.poll_completed() == [task]
        assert manager.poll_completed() == []
        assert traces.get_total_tokens(node.trace_id) == (7, 3)

    @pytest.mark.asyncio
    async def test_failure_timeout_cancel_and_list(self) -> None:
        manager = TaskManager(default_timeout=0.01)
        failed = await manager.launch(
            StubAgent(fail=True),  # type: ignore[arg-type]
            "x",
            agent_type="x",
            description="failed",
            trace_node=manager.trace_registry.create("x"),
        )
        timed = await manager.launch(
            StubAgent(delay=1),  # type: ignore[arg-type]
            "x",
            agent_type="x",
            description="timed",
            trace_node=manager.trace_registry.create("x"),
        )
        cancelled = await manager.launch(
            StubAgent(delay=1),  # type: ignore[arg-type]
            "x",
            agent_type="x",
            description="cancelled",
            trace_node=manager.trace_registry.create("x"),
            timeout=1,
        )
        assert await manager.cancel(cancelled.task_id) is True
        await asyncio.sleep(0.03)
        assert failed.status == "failed" and "boom" in failed.error
        assert timed.status == "failed" and "timed out" in timed.error
        assert cancelled.status == "cancelled"
        assert len(manager.list_tasks()) == 3


class TestNotification:
    @pytest.mark.asyncio
    async def test_format_truncate_and_inject(self) -> None:
        manager = TaskManager()
        node = manager.trace_registry.create("Explore")
        task = StubAgent("x")
        record = await manager.launch(  # type: ignore[arg-type]
            task,
            "x",
            agent_type="Explore",
            description="scan",
            trace_node=node,
        )
        await asyncio.sleep(0)
        record.status = "completed"
        record.result = "x" * (MAX_NOTIFICATION_RESULT_LENGTH + 20)
        rendered = format_task_notification(record)
        assert "<task-notification>" in rendered and "(truncated)" in rendered
        conversation = ConversationManager()
        assert inject_task_notifications(conversation, [record]) == [rendered]
        assert conversation.history[-1].content == rendered


class FakeUI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def add_system_message(self, text: str) -> None:
        self.messages.append(text)


def _command_context(
    manager: TaskManager,
    traces: TraceRegistry,
    ui: FakeUI,
    args: str = "",
) -> Any:
    return CommandContext(
        args=args,
        agent=None,
        conversation=ConversationManager(),
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=ui,  # type: ignore[arg-type]
        config={"task_manager": manager, "trace_registry": traces},
    )


class TestTaskCommands:
    @pytest.mark.asyncio
    async def test_list_info_cancel_and_trace(self) -> None:
        traces = TraceRegistry()
        manager = TaskManager(traces)
        node = traces.create("Explore", "parent")
        record = await manager.launch(
            StubAgent(delay=1),  # type: ignore[arg-type]
            "x",
            agent_type="Explore",
            description="scan",
            trace_node=node,
            timeout=1,
        )
        ui = FakeUI()
        await handle_tasks(_command_context(manager, traces, ui))
        assert record.task_id in ui.messages[-1]
        await handle_task(_command_context(manager, traces, ui, f"info {record.task_id}"))
        assert "Agent：Explore" in ui.messages[-1]
        await handle_trace(_command_context(manager, traces, ui, node.trace_id))
        assert f"Trace {node.trace_id}" in ui.messages[-1]
        await handle_task(_command_context(manager, traces, ui, f"cancel {record.task_id}"))
        assert "已取消" in ui.messages[-1]


class TestAgentTool:
    def _parent(self, client: ScriptedClient, tmp_path: Path) -> Agent:
        return Agent(
            client,
            registry=create_default_registry(),
            work_dir=tmp_path,
            conversation=ConversationManager(),
        )

    def test_params_and_permission_mode(self) -> None:
        params = AgentToolParams(prompt="x", description="d", run_in_background=True)
        assert params.subagent_type is None and params.run_in_background is True
        with pytest.raises(ValueError):
            AgentToolParams(prompt="x", description="")

    @pytest.mark.asyncio
    async def test_defined_agent_sync(self, tmp_path: Path) -> None:
        client = ScriptedClient([[TextDelta("done"), StreamEnd("end_turn", 5, 2)]])
        parent = self._parent(client, tmp_path)
        loader = AgentLoader(tmp_path)
        loader.load_all()
        tool = AgentTool(parent_agent=parent, agent_loader=loader, foreground_timeout=1)
        result = await tool.execute(
            AgentToolParams(
                prompt="implement",
                description="implementation",
                subagent_type="general-purpose",
            )
        )
        assert result == ToolResult("done")
        assert "General-purpose worker" in (client.systems[0] or "")
        schema_names = {item["name"] for item in client.schemas[0] or []}
        assert "Agent" not in schema_names and "AskUserQuestion" not in schema_names
        assert parent.permission_mode is PermissionMode.BYPASS

    @pytest.mark.asyncio
    async def test_fork_is_background_and_notifies(self, tmp_path: Path) -> None:
        client = ScriptedClient([[TextDelta("forked"), StreamEnd("end_turn", 4, 1)]])
        parent = self._parent(client, tmp_path)
        parent.conversation.add_user_message("parent context")
        traces = TraceRegistry()
        manager = TaskManager(traces)
        tool = AgentTool(
            parent_agent=parent,
            agent_loader=AgentLoader(tmp_path),
            trace_manager=traces,
            task_manager=manager,
        )
        result = await tool.execute(
            AgentToolParams(prompt="investigate", description="fork task")
        )
        assert "Task ID:" in result.output
        await asyncio.sleep(0.02)
        completed = manager.poll_completed()
        assert completed[0].result == "forked"
        assert any(
            FORK_BOILERPLATE_TAG in message.content
            for message in client.snapshots[0].history
        )
        assert client.snapshots[0].history[1].content == "parent context"

    @pytest.mark.asyncio
    async def test_auto_timeout_and_manual_detach_preserve_instance(self, tmp_path: Path) -> None:
        loader = AgentLoader(tmp_path)
        loader.load_all()
        client = ScriptedClient(
            [
                [TextDelta("auto"), StreamEnd("end_turn")],
                [TextDelta("manual"), StreamEnd("end_turn")],
            ],
            delay=0.04,
        )
        parent = self._parent(client, tmp_path)
        manager = TaskManager(default_timeout=1)
        tool = AgentTool(
            parent_agent=parent,
            agent_loader=loader,
            task_manager=manager,
            trace_manager=manager.trace_registry,
            foreground_timeout=0.005,
            background_timeout=1,
        )
        auto = await tool.execute(
            AgentToolParams(prompt="one", description="auto", subagent_type="general-purpose")
        )
        assert "auto-detached" in auto.output
        await asyncio.sleep(0.15)
        assert any(item.result == "auto" for item in manager.poll_completed())

        tool.foreground_timeout = 1
        outer = asyncio.create_task(
            tool.execute(
                AgentToolParams(
                    prompt="two",
                    description="manual",
                    subagent_type="general-purpose",
                )
            )
        )
        while not tool.foreground_running:
            await asyncio.sleep(0)
        record = await tool.detach_foreground()
        assert record is not None
        outer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await outer
        await asyncio.sleep(0.15)
        assert record.status == "completed" and record.result == "manual"

    @pytest.mark.asyncio
    async def test_unknown_type_and_nested_fork_errors(self, tmp_path: Path) -> None:
        client = ScriptedClient([])
        parent = self._parent(client, tmp_path)
        loader = AgentLoader(tmp_path)
        loader.load_all()
        tool = AgentTool(parent_agent=parent, agent_loader=loader)
        missing = await tool.execute(
            AgentToolParams(prompt="x", description="x", subagent_type="missing")
        )
        assert missing.is_error and "Available" in missing.output
        parent.conversation.add_user_message(FORK_BOILERPLATE_TAG)
        nested = await tool.execute(AgentToolParams(prompt="x", description="x"))
        assert nested.is_error and "Cannot fork" in nested.output
