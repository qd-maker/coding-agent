"""Textual shell and streaming interaction tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel
from textual.widgets import Markdown, Static

import mewcode.app as app_module
from mewcode.app import ActivityLine, MewCodeApp, PromptComposer, terminal_safe_text
from mewcode.client import LLMClient
from mewcode.commands.completion import CompletionPopup
from mewcode.config import AppConfig, MCPServerConfig
from mewcode.conversation import ConversationManager, Message
from mewcode.memory import SessionManager
from mewcode.permission_dialog import InlinePermissionPrompt, InlineQuestionPrompt
from mewcode.permissions import PermissionMode
from mewcode.skills import SkillDependencyError
from mewcode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    Tool,
    ToolCallComplete,
    ToolCallStart,
    ToolResult,
)
from mewcode.tools.impl import ToolSearchParams, ToolSearchTool


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


@pytest.fixture(autouse=True)
def isolate_tui_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep App-created sessions and plans out of the developer's working tree."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "mewcode.skills.loader.USER_SKILLS_DIR",
        tmp_path / "isolated-user-skills",
    )
    monkeypatch.setattr(
        "mewcode.agents.loader.USER_AGENTS_DIR",
        tmp_path / "isolated-user-agents",
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
        self.snapshots: list[list[Message]] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del system, tools
        self.snapshots.append(conversation.get_messages())
        yield TextDelta("Hello")
        self.started.set()
        await self.release.wait()
        yield StreamEnd("end_turn", 2, 2)


class BurstClient(LLMClient):
    def __init__(self, config: Any, count: int = 1000) -> None:
        super().__init__(config)
        self.count = count
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
        for _ in range(self.count):
            yield TextDelta("x")
        yield StreamEnd("end_turn", 10, self.count)


class ParallelToolClient(LLMClient):
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
            yield ToolCallComplete("glob-py", "Glob", {"pattern": "*.py", "path": "."})
            yield ToolCallComplete("glob-md", "Glob", {"pattern": "*.md", "path": "."})
            yield StreamEnd("tool_use", 4, 2)
            return
        yield TextDelta("Exploration complete.")
        yield StreamEnd("end_turn", 5, 2)


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


class FakeMCPTool(Tool):
    name = "mcp_context7_resolve_library_id"
    description = "Resolve a library ID through Context7"
    params_model = EmptyToolParams
    category = "command"
    should_defer = True

    async def execute(self, params: EmptyToolParams) -> ToolResult:
        del params
        return ToolResult("resolved")


class FakeMCPManager:
    instances: list[FakeMCPManager] = []

    def __init__(self) -> None:
        self.configs: list[MCPServerConfig] = []
        self.release = asyncio.Event()
        self.registered = False
        self.shutdown_called = False
        self.instances.append(self)

    def load_configs(self, configs: list[MCPServerConfig]) -> None:
        self.configs = list(configs)

    async def register_all_tools(self, registry: Any) -> list[str]:
        await self.release.wait()
        registry.register(FakeMCPTool())
        self.registered = True
        return []

    @property
    def connected_server_names(self) -> list[str]:
        return [config.name for config in self.configs] if self.registered else []

    async def shutdown(self) -> None:
        self.shutdown_called = True


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
        assert "Shift+Enter newline" in str(app.query_one("#prompt", PromptComposer).placeholder)
        assert app.focused is app.query_one("#prompt", PromptComposer)
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
async def test_tui_slash_commands_are_intercepted_before_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("/status"), "enter")
        await pilot.pause()
        assert client.snapshots == []
        assert "MewCode 状态" in str(app.query(".tool").last(Static).render())

        await pilot.press(*list("/missing"), "enter")
        await pilot.pause()
        assert client.snapshots == []
        assert "未知命令" in str(app.query(".tool").last(Static).render())


@pytest.mark.asyncio
async def test_tui_tab_completes_single_match_and_shows_multiple_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    monkeypatch.setattr(app_module, "create_client", lambda _: StreamingClient(config.provider))
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        prompt = app.query_one("#prompt", PromptComposer)
        await pilot.press(*list("/sta"), "tab")
        await pilot.pause()
        assert prompt.text == "/status "

        prompt.load_text("/")
        prompt.focus()
        await pilot.press("tab")
        await pilot.pause()
        popup = app.query_one(CompletionPopup)
        assert popup.is_visible


@pytest.mark.asyncio
async def test_tui_review_skill_runs_in_isolated_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("/review focus concurrency"), "enter")
        await pilot.pause(0.2)
        assert len(client.snapshots) == 1
        submitted = "\n".join(message.content for message in client.snapshots[0])
        assert "# Code Review SOP" in submitted
        assert "Extra focus from the user: focus concurrency" in submitted
        assert app.conversation.history == []
        assert any(
            "[review skill result]" in str(widget.render()) for widget in app.query(".tool")
        )


@pytest.mark.asyncio
async def test_ch11_tui_catalog_inline_skill_and_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(110, 36)) as pilot:
        await pilot.press(*list("/help"), "enter")
        await pilot.pause()
        help_text = str(app.query(".tool").last(Static).render())
        assert "/commit" in help_text and "[skill]" in help_text
        assert "/review" in help_text and "/test" in help_text and "/skill" in help_text

        await pilot.press(*list("/skill list"), "enter")
        await pilot.pause()
        assert "builtin" in str(app.query(".tool").last(Static).render())

        await pilot.press(*list("/skill info commit"), "enter")
        await pilot.pause()
        assert "AllowedTools: Bash, ReadFile, Grep" in str(
            app.query(".tool").last(Static).render()
        )

        await pilot.press(*list("/commit docs only"), "enter")
        await pilot.pause(0.2)
        assert "commit" in app.agent.active_skills
        submitted = "\n".join(message.content for message in client.snapshots[-1])
        assert "# Commit SOP" in submitted
        assert "User request: docs only" in submitted

        await pilot.press(*list("/clear"), "enter")
        await pilot.pause()
        assert app.agent.active_skills == {}
        assert app.agent._active_allowed_tool_names() is None


@pytest.mark.asyncio
async def test_ch11_tui_hot_reloads_project_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / ".mewcode" / "skills" / "custom.md"
    skill_path.parent.mkdir(parents=True)

    def write(body: str) -> None:
        skill_path.write_text(
            "---\nname: custom\ndescription: Custom hot skill\n"
            "allowedTools: []\nmode: inline\ncontext: full\n---\n" + body,
            encoding="utf-8",
        )

    write("version one $ARGUMENTS")
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        write("version two $ARGUMENTS")
        await pilot.press(*list("/custom refreshed"), "enter")
        await pilot.pause(0.2)
        assert app.agent.active_skills["custom"] == "version two refreshed"
        assert "version two refreshed" in "\n".join(
            message.content for message in client.snapshots[-1]
        )


def test_ch11_app_fails_fast_for_missing_skill_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    skill_path = tmp_path / ".mewcode" / "skills" / "broken-dependency.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(
        "---\nname: broken-dependency\ndescription: Invalid dependency\n"
        "allowedTools: [DefinitelyMissing]\nmode: inline\n---\nDo work",
        encoding="utf-8",
    )
    config = app_config()
    monkeypatch.setattr(app_module, "create_client", lambda _: StreamingClient(config.provider))
    with pytest.raises(SkillDependencyError, match="DefinitelyMissing"):
        MewCodeApp(config)


def test_headless_permission_mode_can_change_before_screen_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    monkeypatch.setattr(app_module, "create_client", lambda _: StreamingClient(config.provider))
    app = MewCodeApp(config)

    app.set_permission_mode(PermissionMode.BYPASS)

    assert app.permission_mode is PermissionMode.BYPASS


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
        prompt = app.query_one("#prompt", PromptComposer)
        assert prompt.disabled is False
        assert "Shift+Enter newline" in str(prompt.placeholder)


@pytest.mark.asyncio
async def test_multiline_composer_history_and_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        composer = app.query_one("#prompt", PromptComposer)
        await pilot.press("f", "i", "r", "s", "t", "shift+enter", "s", "e", "c", "o", "n", "d")
        assert composer.text == "first\nsecond"
        await pilot.press("enter")
        await pilot.pause(0.2)
        assert client.snapshots[-1][-1].content == "first\nsecond"

        await pilot.press("up")
        assert composer.text == "first\nsecond"


@pytest.mark.asyncio
async def test_busy_composer_queues_and_runs_next_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    client = BlockingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("first request"), "enter")
        await asyncio.wait_for(client.started.wait(), timeout=1)
        await pilot.press(*list("second request"), "enter")
        await pilot.pause(0.1)
        assert app._queued_prompts == ["second request"]
        assert app.query_one("#prompt", PromptComposer).disabled is False

        client.release.set()
        await pilot.pause(0.35)
        user_messages = [
            message.content
            for snapshot in client.snapshots
            for message in snapshot
            if message.role == "user" and message.content in {"first request", "second request"}
        ]
        assert "first request" in user_messages
        assert "second request" in user_messages
        assert app._queued_prompts == []


@pytest.mark.asyncio
async def test_streaming_burst_is_rendered_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    config = app_config()
    client = BurstClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list("burst"), "enter")
        await asyncio.wait_for(client.started.wait(), timeout=1)
        answer = app.query_one(".assistant", Static)
        original_update = answer.update
        updates = 0

        def counted_update(content: Any = "") -> None:
            nonlocal updates
            updates += 1
            original_update(content)

        monkeypatch.setattr(answer, "update", counted_update)
        client.release.set()
        await pilot.pause(0.3)

        assert updates < 50
        assert len(app.query_one(".assistant-markdown", Markdown).source) == 1000


@pytest.mark.asyncio
async def test_parallel_tools_are_grouped_and_welcome_collapses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    monkeypatch.setattr(app_module, "create_client", lambda _: ParallelToolClient(config.provider))
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        welcome = app.query_one("#welcome-panel")
        assert not welcome.has_class("collapsed")

        await pilot.press(*list("explore files"), "enter")
        await pilot.pause(0.3)

        assert welcome.has_class("collapsed")
        assert len(app.query(".tool-batch")) == 1
        assert len(app.query(".tool-batch .tool-card")) == 2
        assert "Parallel tool batch" in str(
            app.query_one(".tool-batch-title", Static).render()
        )


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
        await pilot.press(*list("show the status icon"), "enter")
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
        assert app.query_one("#prompt", PromptComposer).disabled is False


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

        await pilot.press(*list("continue"), "enter")
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
        summary = str(permission.query_one(".inline-prompt-message", Static).render())
        assert "TestWrite" in summary
        assert "Working directory" in summary
        assert "Approval fingerprint" in summary
        await pilot.press("y")
        await pilot.pause(0.2)
        assert "Permission: Allowed once" in str(
            permission.query_one(".inline-prompt-title", Static).render()
        )
        assert app.query_one(".assistant-markdown", Markdown).source == "Write approved."
        assert "Completed with evidence" in str(app.query_one(".result-card", Static).render())


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


@pytest.mark.asyncio
async def test_tui_initializes_mcp_on_tool_search_and_injects_reminder_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config().model_copy(
        update={
            "mcp_servers": [MCPServerConfig(name="context7", command="npx")],
        }
    )
    client = StreamingClient(config.provider)
    FakeMCPManager.instances.clear()
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    monkeypatch.setattr(app_module, "MCPManager", FakeMCPManager)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        manager = FakeMCPManager.instances[-1]
        assert "MCP idle" in str(app.query_one("#connection", Static).render())
        assert manager.registered is False

        search = app.registry.get("ToolSearch")
        assert isinstance(search, ToolSearchTool)
        search_task = asyncio.create_task(
            search.execute(ToolSearchParams(query="context7"))
        )
        await asyncio.sleep(0.05)
        manager.release.set()
        await search_task
        await pilot.pause(0.1)
        assert manager.registered is True
        assert "Connected to 1 MCP server(s), 1 tools registered" in str(
            app.query_one("#connection", Static).render()
        )

        await pilot.press(*list("first request"), "enter")
        await pilot.pause(0.2)
        await pilot.press(*list("second request"), "enter")
        await pilot.pause(0.2)

        assert len(client.snapshots) == 2
        history = "\n".join(message.content for message in client.snapshots[-1])
        assert history.count("MCP servers are connected") == 1
        assert "Use ToolSearch to discover/select an MCP tool" in history
        assert "mcp_context7_resolve_library_id" in history
        assert "MCP 1/1 servers · 1 tools" in str(app.query_one("#connection", Static).render())

    assert manager.shutdown_called is True


@pytest.mark.asyncio
async def test_first_message_does_not_wait_for_mcp_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config().model_copy(
        update={
            "mcp_servers": [MCPServerConfig(name="context7", command="npx")],
        }
    )
    client = StreamingClient(config.provider)
    FakeMCPManager.instances.clear()
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    monkeypatch.setattr(app_module, "MCPManager", FakeMCPManager)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        manager = FakeMCPManager.instances[-1]
        await pilot.press(*list("ordinary question"), "enter")
        await pilot.pause(0.25)
        assert len(client.snapshots) == 1
        assert manager.registered is False
        assert app.query_one("#prompt", PromptComposer).disabled is False


@pytest.mark.asyncio
async def test_ch9_tui_loads_instructions_persists_and_exposes_commands(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "MEWCODE.md").write_text("Always run tests.", encoding="utf-8")
    config = app_config()
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)

    async with app.run_test(size=(100, 30)) as pilot:
        session_id = app.session.id
        await pilot.press(*list("hello"), "enter")
        await pilot.pause(0.25)
        assert any(
            message.content == "## 项目指令\nAlways run tests." for message in client.snapshots[0]
        )

        await pilot.press(*list("/session list"), "enter")
        await pilot.pause()
        assert any("最近会话" in str(widget.render()) for widget in app.query(".tool"))

        await pilot.press(*list("/memory edit"), "enter")
        await pilot.pause()
        assert any("memories.md" in str(widget.render()) for widget in app.query(".tool"))

    jsonl = tmp_path / ".mewcode" / "sessions" / f"{session_id}.jsonl"
    payloads = [json.loads(line) for line in jsonl.read_text(encoding="utf-8").splitlines()]
    assert [payload["type"] for payload in payloads] == ["user", "assistant"]
    assert payloads[0]["content"] == "hello"


@pytest.mark.asyncio
async def test_headless_runtime_runs_hooks_and_persists_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    raw_config = app_config().model_dump(by_alias=True)
    raw_config["hooks"] = [
        {
            "id": "headless-startup",
            "event": "startup",
            "action": {"type": "prompt", "message": "headless startup observed"},
        },
        {
            "id": "headless-shutdown",
            "event": "shutdown",
            "action": {"type": "prompt", "message": "headless shutdown observed"},
        },
    ]
    config = AppConfig.model_validate(raw_config)
    client = StreamingClient(config.provider)
    monkeypatch.setattr(app_module, "create_client", lambda _: client)
    app = MewCodeApp(config)
    session_id = app.session.id

    await app.start_headless()
    result = await app.agent.run_to_completion("headless request")
    await app.shutdown_headless()

    assert result == "## Hello\n\n- from MewCode"
    assert app.session.closed is True
    assert "headless startup observed" in app.agent.hook_prompts
    assert "headless shutdown observed" in app.agent.hook_prompts
    restored = SessionManager(tmp_path).resume(session_id)
    assert restored is not None
    assert [message.content for message in restored.messages] == [
        "headless request",
        "## Hello\n\n- from MewCode",
    ]


@pytest.mark.asyncio
async def test_ch9_tui_resumes_and_renders_archived_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = SessionManager(tmp_path)
    archived = manager.create()
    archived.append(Message("user", "old request"))
    archived.append(Message("assistant", "old answer"))
    archived.meta.last_active = datetime.now(UTC) - timedelta(hours=25)
    archived.meta.save(archived.meta_path)
    archived_id = archived.id
    archived.close()

    config = app_config()
    monkeypatch.setattr(
        app_module,
        "create_client",
        lambda _: StreamingClient(config.provider),
    )
    app = MewCodeApp(config)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.press(*list(f"/session resume {archived_id}"), "enter")
        await pilot.pause(0.1)
        assert app.session.id == archived_id
        assert any(message.content == "old request" for message in app.conversation.history)
        assert any("25 小时" in message.content for message in app.conversation.history)
        assert "old request" in str(app.query_one(".user-text", Static).render())
        assert app.query_one(".assistant-markdown", Markdown).source == "old answer"


@pytest.mark.asyncio
async def test_ch12_tui_runs_startup_command_and_shutdown_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_config = app_config().model_dump(by_alias=True)
    raw_config["hooks"] = [
        {
            "id": "startup-context",
            "event": "startup",
            "action": {"type": "prompt", "message": "startup injected"},
        },
        {
            "id": "command-context",
            "event": "command_execute",
            "condition": 'args.name == "status"',
            "action": {"type": "prompt", "message": "status command observed"},
        },
        {
            "id": "shutdown-context",
            "event": "shutdown",
            "action": {"type": "prompt", "message": "shutdown observed"},
        },
    ]
    config = AppConfig.model_validate(raw_config)
    monkeypatch.setattr(
        app_module,
        "create_client",
        lambda _: StreamingClient(config.provider),
    )
    app = MewCodeApp(config)
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        assert "startup injected" in app.agent.hook_prompts
        await pilot.press(*list("/status"), "enter")
        await pilot.pause()
        assert "status command observed" in app.agent.hook_prompts

    assert "shutdown observed" in app.agent.hook_prompts


@pytest.mark.asyncio
async def test_ch13_tui_registers_agents_commands_and_injects_task_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = app_config()
    monkeypatch.setattr(
        app_module,
        "create_client",
        lambda _: StreamingClient(config.provider),
    )
    app = MewCodeApp(config)

    class QuickAgent:
        agent_id = "quick-agent"
        total_input_tokens = 9
        total_output_tokens = 4

        async def run_to_completion(
            self,
            prompt: str,
            conversation: ConversationManager | None = None,
        ) -> str:
            del prompt, conversation
            await asyncio.sleep(0)
            return "background complete"

    assert app.registry.get("Agent") is app.agent_tool
    assert app.command_registry.find("tasks") is not None
    assert app.command_registry.find("task") is not None
    assert app.command_registry.find("trace") is not None
    assert {item.agent_type for item in app.agent_loader.list_agents()} >= {
        "Explore",
        "Plan",
        "general-purpose",
    }

    async with app.run_test(size=(100, 30)) as pilot:
        node = app.trace_registry.create("Explore", app.agent.agent_id)
        await app.task_manager.launch(
            QuickAgent(),  # type: ignore[arg-type]
            "scan",
            agent_type="Explore",
            description="background scan",
            trace_node=node,
        )
        await pilot.pause(0.35)
        assert any(
            "<task-notification>" in message.content
            for message in app.conversation.history
        )
        assert await app._dispatch_command("/tasks") is True
        assert any("background scan" in str(widget.render()) for widget in app.query(".tool"))
