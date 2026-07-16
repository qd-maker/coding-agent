"""Textual shell and streaming interaction tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from textual.widgets import Input, Markdown, Static

import mewcode.app as app_module
from mewcode.app import ActivityLine, MewCodeApp, terminal_safe_text
from mewcode.client import LLMClient
from mewcode.config import AppConfig
from mewcode.conversation import ConversationManager
from mewcode.permission_dialog import InlinePermissionPrompt, InlineQuestionPrompt
from mewcode.permissions import PermissionMode
from mewcode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    Tool,
    ToolCallComplete,
    ToolCallStart,
    ToolResult,
)


def app_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "provider": {
                "name": "default",
                "protocol": "anthropic",
                "model": "anthropic/claude-sonnet-4.6",
                "base_url": "https://openrouter.ai/api",
                "api_key": "test-key",
                "thinking": True,
            }
        }
    )


class StreamingClient(LLMClient):
    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.snapshots: list[list[Any]] = []
        self.tool_schemas: list[list[dict[str, Any]] | None] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del system
        self.snapshots.append(conversation.get_messages())
        self.tool_schemas.append(tools)
        for event in [
            TextDelta("## Hello\n\n"),
            TextDelta("- from MewCode"),
            StreamEnd("end_turn", 3, 4),
        ]:
            await asyncio.sleep(0)
            yield event


class BlockingClient(LLMClient):
    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del conversation, system, tools
        yield TextDelta("Hello")
        self.started.set()
        await self.release.wait()
        yield StreamEnd("end_turn", 2, 2)


class DelayedEmojiClient(LLMClient):
    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del conversation, system, tools
        self.started.set()
        await self.release.wait()
        yield TextDelta("✅ 已成功删除")
        yield StreamEnd("end_turn", 2, 3)


class EmptyToolParams(BaseModel):
    pass


class ControlledReadTool(Tool):
    name = "ControlledRead"
    description = "Wait until the TUI animation is observed"
    params_model = EmptyToolParams
    category = "read"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, params: EmptyToolParams) -> ToolResult:
        del params
        self.started.set()
        await self.release.wait()
        return ToolResult("read complete")


class ControlledToolClient(LLMClient):
    def __init__(self, config: Any) -> None:
        super().__init__(config)
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
            yield ToolCallStart("controlled-read", "ControlledRead")
            yield ToolCallComplete("controlled-read", "ControlledRead", {})
            yield StreamEnd("tool_use", 2, 1)
            return
        yield TextDelta("Done.")
        yield StreamEnd("end_turn", 3, 2)


class ToolClient(LLMClient):
    def __init__(self, config: Any, target: Path) -> None:
        super().__init__(config)
        self.target = target
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
            yield ToolCallComplete("read-1", "ReadFile", {"file_path": str(self.target)})
            yield StreamEnd("tool_use", 5, 2)
            return
        yield TextDelta("## Read complete")
        yield StreamEnd("end_turn", 6, 2)


class AskClient(LLMClient):
    def __init__(self, config: Any) -> None:
        super().__init__(config)
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
            yield ToolCallComplete(
                "ask-1",
                "AskUserQuestion",
                {
                    "questions": [
                        {
                            "type": "select",
                            "name": "mode",
                            "message": "Choose mode",
                            "options": ["safe", "fast"],
                        }
                    ]
                },
            )
            yield StreamEnd("tool_use", 4, 2)
            return
        yield TextDelta("Using safe mode.")
        yield StreamEnd("end_turn", 5, 2)


class TextAskClient(LLMClient):
    def __init__(self, config: Any) -> None:
        super().__init__(config)
        self.calls = 0
        self.snapshots: list[list[Any]] = []
        self.tool_schemas: list[list[dict[str, Any]] | None] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del system
        self.calls += 1
        self.snapshots.append(conversation.get_messages())
        self.tool_schemas.append(tools)
        if self.calls == 1:
            yield ToolCallComplete(
                "ask-text-1",
                "AskUserQuestion",
                {
                    "questions": [
                        {
                            "type": "text",
                            "name": "details",
                            "message": "Anything else?",
                        }
                    ]
                },
            )
            yield StreamEnd("tool_use", 4, 2)
            return
        yield TextDelta("Execution mode ready.")
        yield StreamEnd("end_turn", 5, 2)


class ChainApprovalClient(LLMClient):
    def __init__(self, config: Any, target: Path) -> None:
        super().__init__(config)
        self.target = target
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
            yield ToolCallComplete(
                "glob-1",
                "Glob",
                {"pattern": "*.md", "path": str(self.target.parent)},
            )
            yield StreamEnd("tool_use", 4, 2)
            return
        if self.calls == 2:
            yield ToolCallComplete(
                "read-1",
                "ReadFile",
                {"file_path": str(self.target)},
            )
            yield StreamEnd("tool_use", 5, 2)
            return
        yield TextDelta("## Finished")
        yield StreamEnd("end_turn", 6, 2)


class WriteParams(BaseModel):
    text: str


class TestWriteTool(Tool):
    name = "TestWrite"
    description = "A protected test write"
    params_model = WriteParams
    category = "command"

    async def execute(self, params: WriteParams) -> ToolResult:
        return ToolResult(params.text)


class PermissionClient(LLMClient):
    def __init__(self, config: Any) -> None:
        super().__init__(config)
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
            yield ToolCallComplete("write-1", "TestWrite", {"text": "approved"})
            yield StreamEnd("tool_use", 2, 1)
            return
        yield TextDelta("Write approved.")
        yield StreamEnd("end_turn", 3, 2)


@pytest.mark.asyncio
async def test_tui_shell_fits_80_by_24(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    monkeypatch.setattr(app_module, "create_client", lambda _: StreamingClient(config.provider))
    app = MewCodeApp(config)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert "MewCode" in str(app.query_one("#product-line", Static).render())
        assert "claude-sonnet-4.6" in str(app.query_one("#model-line", Static).render())
        assert "Ready" in str(app.query_one("#connection", Static).render())
        assert app.query_one("#prompt", Input).placeholder == 'Try "explain this project"'
        assert app.focused is app.query_one("#prompt", Input)
        for selector in (
            "#welcome-panel",
            "#welcome-cat",
            "#connection",
            "#composer",
            "#prompt",
            "#statusbar",
        ):
            assert len(app.query(selector)) == 1


@pytest.mark.asyncio
async def test_tui_streams_reply_and_restores_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    monkeypatch.setattr(app_module, "create_client", lambda _: StreamingClient(config.provider))
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("h", "e", "l", "l", "o", "enter")
        await pilot.pause(0.2)

        assert "hello" in str(app.query_one(".user-text", Static).render())
        markdown = app.query_one(".assistant-markdown", Markdown)
        assert markdown.source == "## Hello\n\n- from MewCode"
        assert len(app.query(".assistant-markdown")) == 1
        assert not app.query(".assistant")
        assert "Connected" in str(app.query_one("#connection", Static).render())
        assert "3 in / 4 out" in str(app.query_one("#model-status", Static).render())
        prompt = app.query_one("#prompt", Input)
        assert prompt.disabled is False
        assert prompt.placeholder == 'Try "explain this project"'


@pytest.mark.asyncio
async def test_statusbar_updates_during_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    client = BlockingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("h", "e", "l", "l", "o", "enter")
        await asyncio.wait_for(client.started.wait(), timeout=1)
        await pilot.pause(0.15)

        status = str(app.query_one("#model-status", Static).render())
        assert "anthropic/claude-sonnet-4.6" in status
        assert "~2 in / ~2 out" in status
        assert status.endswith("s")

        client.release.set()
        await pilot.pause(0.1)
        assert "2 in / 2 out" in str(app.query_one("#model-status", Static).render())


@pytest.mark.asyncio
async def test_thinking_animation_and_terminal_safe_status_emoji(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = DelayedEmojiClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("delete it"), "enter")
        await asyncio.wait_for(client.started.wait(), timeout=1)
        thinking = app.query_one(".thinking-label", ActivityLine)
        first_frame = str(thinking.render())
        await pilot.pause(0.12)
        second_frame = str(thinking.render())

        assert "Thinking" in first_frame
        assert first_frame != second_frame
        assert thinking.running is True

        client.release.set()
        await pilot.pause(0.15)
        markdown = app.query_one(".assistant-markdown", Markdown)
        assert markdown.source == "✓ 已成功删除"
        assert "✅" not in markdown.source
        assert thinking.display is False


def test_terminal_safe_text_replaces_colored_emoji_sequences() -> None:
    source = "🥰 👨‍💻 🇨🇳 1️⃣ ☀️ ©️ ❤️ ⏰ 👍🏽 ✅ ❌"
    assert terminal_safe_text(source) == ":) ◇ ◇ 1 ☀ © ❤ ◇ ◇ ✓ ✗"
    assert terminal_safe_text("⚠️ warning") == "⚠ warning"
    assert terminal_safe_text("© ™ ❤ ☀ ⚠") == "© ™ ❤ ☀ ⚠"


@pytest.mark.asyncio
async def test_tool_line_animates_until_execution_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = ControlledToolClient(config.provider)
    tool = ControlledReadTool()
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)
    app.registry.register(tool)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("read slowly"), "enter")
        await asyncio.wait_for(tool.started.wait(), timeout=1)
        activity = app.query_one(".tool.is-running", ActivityLine)
        first_frame = str(activity.render())
        await pilot.pause(0.12)
        second_frame = str(activity.render())

        assert "Running ControlledRead" in first_frame
        assert first_frame != second_frame
        assert activity.running is True

        tool.release.set()
        await pilot.pause(0.2)
        assert activity.running is False
        assert "✓ ControlledRead" in str(activity.render())


@pytest.mark.asyncio
async def test_ctrl_c_cancels_an_active_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = BlockingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)
    exit_calls: list[bool] = []
    monkeypatch.setattr(app, "exit", lambda *args, **kwargs: exit_calls.append(True))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("cancel me"), "enter")
        await asyncio.wait_for(client.started.wait(), timeout=1)
        await pilot.press("ctrl+c")
        await pilot.pause(0.1)

        assert exit_calls == []
        assert "Cancelled" in str(app.query_one("#connection", Static).render())
        assert app.query_one("#prompt", Input).disabled is False


@pytest.mark.asyncio
async def test_ctrl_c_only_exits_on_second_idle_press_within_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    monkeypatch.setattr(app_module, "create_client", lambda _: StreamingClient(config.provider))
    clock = [100.0]
    monkeypatch.setattr(app_module, "perf_counter", lambda: clock[0])
    app = MewCodeApp(config)
    exit_calls: list[bool] = []
    monkeypatch.setattr(app, "exit", lambda *args, **kwargs: exit_calls.append(True))

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exit_calls == []
        assert "Ctrl+C again to exit" in str(app.query_one("#footer-hint", Static).render())

        clock[0] = 102.0
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exit_calls == []

        clock[0] = 102.5
        await pilot.press("ctrl+c")
        await pilot.pause()
        assert exit_calls == [True]


@pytest.mark.asyncio
async def test_tui_executes_read_file_and_returns_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "README.md"
    target.write_text("# MewCode", encoding="utf-8")
    config = app_config()
    client = ToolClient(config.provider, target)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("read readme"), "enter")
        await pilot.pause(0.25)

        tool_lines = [str(widget.render()) for widget in app.query(".tool").results(Static)]
        assert any("✓ Read:" in line and "README.md" in line for line in tool_lines)
        assert all("# MewCode" not in line for line in tool_lines)
        assert app.query_one(".assistant-markdown", Markdown).source == "## Read complete"


@pytest.mark.asyncio
async def test_tui_collects_ask_user_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    client = AskClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("/plan"), "enter")
        await pilot.pause()
        await pilot.press(*list("ask me"), "enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.query(".inline-question-prompt"):
                break
        assert len(app.screen_stack) == 1
        question = app.query_one(InlineQuestionPrompt)
        assert "Choose mode" in str(question.query_one(".inline-prompt-message", Static).render())
        await pilot.press("down", "enter")
        await pilot.pause(0.2)
        assert "mode: fast" in str(question.query_one(".inline-prompt-message", Static).render())
        assert app.query_one(".assistant-markdown", Markdown).source == "Using safe mode."


@pytest.mark.asyncio
async def test_do_command_exits_plan_mode_from_inline_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = TextAskClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("/plan"), "enter")
        await pilot.pause()
        await pilot.press(*list("clarify first"), "enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.query(".inline-question-prompt"):
                break

        question = app.query_one(InlineQuestionPrompt)
        await pilot.press(*list("/do"), "enter")
        await pilot.pause(0.25)

        assert app.agent.permission_mode is PermissionMode.ACCEPT_EDITS
        assert "Question cancelled" in str(
            question.query_one(".inline-prompt-title", Static).render()
        )
        assert app.query_one(".assistant-markdown", Markdown).source == "Execution mode ready."
        second_history = "\n".join(message.content for message in client.snapshots[1])
        assert "Plan Mode is no longer active" in second_history
        second_tools = {schema["name"] for schema in client.tool_schemas[1] or []}
        assert "WritePlan" not in second_tools
        assert "WriteFile" in second_tools


@pytest.mark.asyncio
async def test_tui_runs_multiple_tool_rounds_without_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    target = tmp_path / "README.md"
    target.write_text("# MewCode", encoding="utf-8")
    config = app_config()
    client = ChainApprovalClient(config.provider, target)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("inspect readme"), "enter")
        await pilot.pause(0.35)
        assert not app.query(".inline-question-prompt")
        tool_lines = [str(widget.render()) for widget in app.query(".tool").results(Static)]
        assert any("✓ Glob:" in line and "*.md" in line for line in tool_lines)
        assert any("✓ Read:" in line and "README.md" in line for line in tool_lines)
        assert app.query_one(".assistant-markdown", Markdown).source == "## Finished"


@pytest.mark.asyncio
async def test_tui_plan_and_do_commands_switch_permission_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("/plan"), "enter")
        await pilot.pause()
        assert app.agent.permission_mode is PermissionMode.PLAN
        assert "Plan on" in str(app.query(".tool").last(Static).render())
        assert "Plan on" in str(app.query_one("#mode-status", Static).render())

        await pilot.press(*list("make a plan"), "enter")
        await pilot.pause(0.2)
        assert len(client.snapshots) == 1

        await pilot.press(*list("/do"), "enter")
        await pilot.pause()
        assert app.agent.permission_mode is PermissionMode.ACCEPT_EDITS
        assert "Accept Edits on" in str(app.query(".tool").last(Static).render())
        assert "Accept Edits on" in str(app.query_one("#mode-status", Static).render())

        await pilot.press(*list("implement it"), "enter")
        await pilot.pause(0.2)
        assert len(client.snapshots) == 2
        latest_history = "\n".join(message.content for message in client.snapshots[-1])
        assert "Plan Mode is no longer active" in latest_history
        latest_tools = {schema["name"] for schema in client.tool_schemas[-1] or []}
        assert "WritePlan" not in latest_tools
        assert "WriteFile" in latest_tools


@pytest.mark.asyncio
async def test_tui_resolves_permission_request(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    client = PermissionClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)
    app.registry.register(TestWriteTool())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("write"), "enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.query(".inline-permission-prompt"):
                break
        assert len(app.screen_stack) == 1
        permission = app.query_one(InlinePermissionPrompt)
        assert "TestWrite" in str(permission.query_one(".inline-prompt-message", Static).render())
        await pilot.press("down", "up", "enter")
        await pilot.pause(0.2)
        assert "Permission: Allowed once" in str(
            permission.query_one(".inline-prompt-title", Static).render()
        )
        assert app.query_one(".assistant-markdown", Markdown).source == "Write approved."


@pytest.mark.asyncio
async def test_tui_mode_command_and_shift_tab_cycle_all_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        assert app.permission_mode is PermissionMode.ACCEPT_EDITS
        assert "Accept Edits on" in str(app.query_one("#mode-status", Static).render())

        await pilot.press(*list("/mode yolo"), "enter")
        await pilot.pause()
        assert app.permission_mode is PermissionMode.BYPASS
        assert "YOLO on" in str(app.query(".tool").last(Static).render())
        assert "YOLO on" in str(app.query_one("#mode-status", Static).render())

        await pilot.press(*list("/mode"), "enter")
        await pilot.pause()
        assert app.permission_mode is PermissionMode.ACCEPT_EDITS

        await pilot.press("shift+tab")
        await pilot.pause()
        assert app.permission_mode is PermissionMode.PLAN
        assert "Plan on" in str(app.query_one("#mode-status", Static).render())


@pytest.mark.asyncio
async def test_tui_yolo_executes_command_without_permission_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = PermissionClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)
    app.registry.register(TestWriteTool())

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("/mode yolo"), "enter")
        await pilot.press(*list("write"), "enter")
        for _ in range(20):
            await pilot.pause(0.05)
            if app.query(".assistant-markdown"):
                break

        assert app.permission_mode is PermissionMode.BYPASS
        assert not app.query(".inline-permission-prompt")
        assert app.query_one(".assistant-markdown", Markdown).source == "Write approved."
