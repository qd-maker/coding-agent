"""Textual terminal UI and provider-to-Agent assembly."""

from __future__ import annotations

import asyncio
import json
import math
import os
from contextlib import suppress
from pathlib import Path
from time import perf_counter
from typing import Any, cast

import emoji
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Input, Markdown, Static
from textual.worker import Worker

from mewcode import __version__
from mewcode.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from mewcode.cache import FileCache
from mewcode.client import LLMError, create_client
from mewcode.commands.handlers import (
    handle_do,
    handle_permission_mode,
    handle_plan,
    permission_mode_label,
)
from mewcode.config import AppConfig
from mewcode.conversation import ConversationManager
from mewcode.hooks import HookEngine
from mewcode.permission_dialog import InlinePermissionPrompt, InlineQuestionPrompt
from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.teams import TeamManager
from mewcode.tools import ToolRegistry, create_default_registry
from mewcode.tools.ask_user import AskUserEvent, AskUserTool
from mewcode.tools.impl import ToolSearchTool

_SPINNER_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
_TERMINAL_SAFE_GLYPHS = str.maketrans(
    {
        "✅": "✓",
        "❌": "✗",
        "☑": "✓",
    }
)


def _emoji_fallback(chars: str, data: dict[str, Any]) -> str:
    if data.get("status") == 4 and "\ufe0f" not in chars:
        return chars
    text_variant = chars.replace("\ufe0f", "")
    if text_variant and all(char in "0123456789#*\u20e3" for char in text_variant):
        return text_variant.replace("\u20e3", "")
    if data.get("variant") is True and "\ufe0f" in chars:
        return text_variant
    name = data.get("en", "")
    if "face" in name or "smil" in name:
        return ":)"
    return "◇"


def terminal_safe_text(text: str) -> str:
    """Replace emoji whose fallback glyph width is inconsistent in terminals."""
    safe = text.translate(_TERMINAL_SAFE_GLYPHS).replace("⚠️", "⚠")
    safe = emoji.replace_emoji(safe, replace=_emoji_fallback)
    return safe.replace("\ufe0f", "").replace("\u200d", "")


class ActivityLine(Static):
    """Single-cell animated status line that remains safe in monospace terminals."""

    def __init__(
        self,
        label: str,
        *,
        classes: str,
        indent: str = "",
    ) -> None:
        self.label = label
        self.indent = indent
        self.running = True
        self._frame_index = 0
        self._started_at = perf_counter()
        self._animation_timer: Timer | None = None
        super().__init__("", classes=classes, markup=False)

    def on_mount(self) -> None:
        self._render_frame()
        self._animation_timer = self.set_interval(0.09, self._advance_frame)

    def on_unmount(self) -> None:
        if self._animation_timer is not None:
            self._animation_timer.stop()

    def _advance_frame(self) -> None:
        if not self.running:
            return
        self._frame_index = (self._frame_index + 1) % len(_SPINNER_FRAMES)
        self._render_frame()

    def _render_frame(self) -> None:
        elapsed = max(0.0, perf_counter() - self._started_at)
        self.update(
            f"{self.indent}{_SPINNER_FRAMES[self._frame_index]} {self.label} · {elapsed:.1f}s"
        )

    def set_activity(self, label: str) -> None:
        if not self.running or label != self.label:
            self._started_at = perf_counter()
        self.label = label
        self.running = True
        self.display = True
        self.add_class("is-running")
        if self._animation_timer is not None:
            self._animation_timer.resume()
        self._render_frame()

    def finish(self, text: str, *, hide: bool = False) -> None:
        self.running = False
        self.remove_class("is-running")
        if self._animation_timer is not None:
            self._animation_timer.pause()
        self.update(text)
        if hide:
            self.display = False


class MewCodeApp(App[None]):
    """Claude Code-inspired multi-turn terminal chat."""

    TITLE = "MewCode"
    CSS = """
    Screen {
        layout: vertical;
        background: #17171c;
        color: #e3e1e8;
    }

    #conversation {
        width: 100%;
        height: 1fr;
        padding: 1 2 1 2;
        scrollbar-size: 1 1;
        scrollbar-color: #55515f;
        scrollbar-background: #17171c;
    }

    #welcome-panel {
        width: 100%;
        height: 13;
        border: round #8f7ad1;
        background: #1b1a21;
    }

    #welcome-content {
        width: 100%;
        height: 100%;
        layout: horizontal;
        padding: 0 1;
    }

    #welcome-left {
        width: 42%;
        height: 100%;
        padding: 1 2;
        align-horizontal: center;
    }

    #welcome-message {
        width: 100%;
        height: 1;
        text-align: center;
        text-style: bold;
        color: #f0eef5;
    }

    #welcome-cat {
        width: 100%;
        height: 4;
        margin-top: 1;
        text-align: center;
        color: #a694e8;
        text-style: bold;
    }

    #product-line, #model-line, #directory-line {
        width: 100%;
        height: 1;
        text-align: center;
        color: #96919f;
    }

    #product-line {
        display: none;
    }

    #welcome-right {
        width: 1fr;
        height: 100%;
        padding: 1 2;
        border-left: solid #433c56;
    }

    .feed-title {
        width: 100%;
        height: 1;
        color: #b5a5ec;
        text-style: bold;
    }

    .feed-line {
        width: 100%;
        height: 1;
        color: #cbc8d2;
    }

    .feed-muted {
        color: #817d89;
    }

    .feed-divider {
        width: 100%;
        height: 1;
        border-bottom: solid #433c56;
    }

    Screen.narrow #welcome-panel {
        height: 12;
    }

    Screen.narrow #welcome-left {
        width: 100%;
    }

    Screen.narrow #welcome-right {
        display: none;
    }

    Screen.narrow #footer-hint {
        display: none;
    }

    #connection {
        width: 100%;
        height: 2;
        padding: 1 2 0 2;
        color: #85818d;
    }

    #connection.connected {
        color: #68cfc5;
    }

    #connection.failed {
        color: #f19a9f;
    }

    .user-row {
        width: 100%;
        height: auto;
        min-height: 2;
        margin-top: 1;
        padding: 0 1;
        background: #26252d;
    }

    .user-mark {
        width: 3;
        height: auto;
        color: #efedf3;
        text-style: bold;
    }

    .user-text {
        width: 1fr;
        height: auto;
        color: #efedf3;
    }

    .assistant-row {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    .assistant-mark {
        width: 3;
        height: auto;
        color: #a694e8;
    }

    .assistant-body {
        width: 1fr;
        height: auto;
    }

    .assistant, .assistant-markdown {
        width: 100%;
        height: auto;
        margin: 0;
        padding: 0;
        background: #17171c;
        color: #e3e1e8;
    }

    .assistant-markdown MarkdownBlock {
        background: #17171c;
    }

    .assistant-markdown MarkdownHeader {
        margin: 1 0;
        background: #17171c;
        color: #efedf3;
        text-style: bold;
    }

    .assistant-markdown MarkdownH1 {
        content-align: left middle;
    }

    .assistant-markdown MarkdownBullet {
        color: #a694e8;
    }

    .assistant-markdown MarkdownFence {
        margin: 1 0;
        background: #131318;
        color: #e3e1e8;
    }

    .assistant-markdown MarkdownBlockQuote {
        background: #211f29;
        border-left: outer #716887;
    }

    .thinking-label {
        width: 100%;
        height: 1;
        color: #8c8795;
        text-style: italic;
    }

    .thinking-label.is-running {
        color: #b5a5ec;
        text-style: bold;
    }

    .thinking-details {
        width: 100%;
        height: auto;
        padding-left: 2;
        margin-bottom: 1;
        color: #7f7a89;
        text-style: italic;
    }

    .error {
        width: 100%;
        height: auto;
        margin-top: 1;
        padding: 0 1;
        color: #f19a9f;
        border: solid #74464f;
    }

    .tool {
        width: 100%;
        height: auto;
        padding-left: 3;
        color: #96919f;
    }

    .tool.is-running {
        color: #b5a5ec;
        text-style: bold;
    }

    .tool.tool-error {
        color: #f19a9f;
    }

    .tool.tool-success {
        color: #72d39a;
    }

    #composer {
        width: 100%;
        height: 3;
        padding: 0 1;
        border-top: solid #625d6d;
        border-bottom: solid #625d6d;
        background: #1b1a20;
        align-vertical: middle;
    }

    #prompt-mark {
        width: 2;
        height: 1;
        color: #a694e8;
        text-style: bold;
    }

    #prompt {
        width: 1fr;
        height: 1;
        padding: 0;
        border: none;
        background: transparent;
        color: #efedf3;
    }

    #prompt:focus {
        border: none;
        background: transparent;
    }

    #prompt > .input--placeholder {
        color: #7f7a89;
    }

    #statusbar {
        width: 100%;
        height: 2;
        padding: 0 2;
        background: #17171c;
        color: #817d89;
    }

    #footer-hint {
        width: 24;
        height: 1;
    }

    #mode-status {
        width: 42;
        height: 1;
        overflow: hidden;
    }

    #model-status {
        width: 1fr;
        height: 1;
        text-align: right;
        overflow: hidden;
    }

    Input.-disabled {
        opacity: 70%;
    }
    """
    CTRL_C_EXIT_WINDOW_SECONDS = 1.5

    BINDINGS = [
        ("ctrl+c", "cancel_or_quit", "Cancel / Quit"),
        ("ctrl+l", "clear_chat", "Clear display"),
        ("ctrl+o", "toggle_thinking", "Toggle thinking"),
        Binding("shift+tab", "cycle_mode", "Permission mode", priority=True),
    ]

    def __init__(
        self,
        config: AppConfig,
        *,
        coordinator_mode: bool = False,
        team_name: str | None = None,
        team_manager: TeamManager | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.client = create_client(config.provider)
        self.work_dir = Path.cwd().resolve()
        self.conversation = ConversationManager()
        self.file_cache = FileCache()
        self.registry: ToolRegistry = create_default_registry(self.file_cache)
        self.registry.register(ToolSearchTool(self.registry, protocol=config.provider.protocol))
        self.ask_user_tool = AskUserTool()
        self.registry.register(self.ask_user_tool)
        self.permission_checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(self.work_dir),
            rule_engine=RuleEngine(
                user_rules_path=Path.home() / ".mewcode" / "permissions.yaml",
                project_rules_path=self.work_dir / ".mewcode" / "permissions.yaml",
                local_rules_path=self.work_dir / ".mewcode" / "permissions.local.yaml",
            ),
            mode=PermissionMode.ACCEPT_EDITS,
        )
        self.hook_engine = HookEngine()
        self.agent = Agent(
            client=self.client,
            system=config.system_prompt,
            registry=self.registry,
            protocol=config.provider.protocol,
            work_dir=self.work_dir,
            conversation=self.conversation,
            permission_checker=self.permission_checker,
            context_window=200_000,
            instructions_content="",
            memory_manager=None,
            hook_engine=self.hook_engine,
            coordinator_mode=coordinator_mode,
            team_name=team_name,
            team_manager=team_manager,
        )
        self._active_worker: Worker[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._turn_started_at: float | None = None
        self._live_prompt_text = ""
        self._live_generated_text = ""
        self._thinking_expanded = False
        self._thinking_widgets: list[tuple[Static, Static]] = []
        self._last_input_tokens = 0
        self._last_output_tokens = 0
        self._last_elapsed = 0.0
        self._pending_ask_event: AskUserEvent | None = None
        self._pending_permission_request: PermissionRequest | None = None
        self._inline_question_prompt: InlineQuestionPrompt | None = None
        self._inline_permission_prompt: InlinePermissionPrompt | None = None
        self._last_idle_ctrl_c_at: float | None = None
        self._narrow_mode = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="conversation"):
            with Vertical(id="welcome-panel") as welcome_panel:
                welcome_panel.border_title = f" MewCode v{__version__} "
                cast(Any, welcome_panel).border_title_align = "left"
                with Horizontal(id="welcome-content"):
                    with Vertical(id="welcome-left"):
                        yield Static("Welcome back!", id="welcome-message", markup=False)
                        yield Static(" /\\_/\\\n( o.o )\n > ^ <", id="welcome-cat", markup=False)
                        yield Static(f"MewCode v{__version__}", id="product-line", markup=False)
                        yield Static(self._welcome_model_line(), id="model-line", markup=False)
                        yield Static(self._compact_cwd(), id="directory-line", markup=False)
                    with Vertical(id="welcome-right"):
                        yield Static("Tips for getting started", classes="feed-title", markup=False)
                        yield Static(
                            "Ask about this project or paste an error",
                            classes="feed-line",
                        )
                        yield Static("Ctrl+C cancels the current response", classes="feed-line")
                        yield Static("", classes="feed-divider")
                        yield Static("Session", classes="feed-title", markup=False)
                        yield Static(
                            "No messages yet",
                            classes="feed-line feed-muted",
                            markup=False,
                        )
            yield Static(self._ready_text(), id="connection", markup=False)
        with Horizontal(id="composer"):
            yield Static("❯", id="prompt-mark", markup=False)
            yield Input(placeholder='Try "explain this project"', id="prompt")
        with Horizontal(id="statusbar"):
            yield Static("Ctrl+C ×2 to exit", id="footer-hint", markup=False)
            yield Static(self._mode_status_text(), id="mode-status", markup=False)
            yield Static(self._idle_status_text(), id="model-status", markup=False)

    def _provider_name(self) -> str:
        return "Anthropic" if self.config.provider.protocol == "anthropic" else "OpenAI"

    def _welcome_model_line(self) -> str:
        return f"{self.config.provider.model} · {self._provider_name()}"

    def _compact_cwd(self, limit: int = 52) -> str:
        value = str(self.work_dir)
        if len(value) <= limit:
            return value
        path = Path(value)
        tail = str(Path(path.parent.name) / path.name)
        return f"…{Path(value).anchor}{tail}"[-limit:]

    def _ready_text(self) -> str:
        return f"◇ Ready · {self._provider_name()} API · streaming"

    def _connected_text(self) -> str:
        return f"● Connected · {self._provider_name()} API · streaming"

    def _idle_status_text(self) -> str:
        return f"{self.config.provider.model} · 0 in / 0 out · 0.0s"

    def _mode_status_text(self) -> Text:
        mode = self.permission_mode
        colors = {
            PermissionMode.ACCEPT_EDITS: "bold #72d39a",
            PermissionMode.PLAN: "bold #b5a5ec",
            PermissionMode.BYPASS: "bold #e36a75",
        }
        return Text.assemble(
            (f"{permission_mode_label(mode)} on", colors.get(mode, "bold #72d39a")),
            (" (shift+tab to cycle)", "#817d89"),
        )

    def _refresh_mode_status(self) -> None:
        status = self.query("#mode-status").first(Static)
        if status is not None:
            status.update(self._mode_status_text())

    def on_mount(self) -> None:
        self._apply_width_mode(self.size.width)
        self.query_one("#prompt", Input).focus()
        self.set_interval(0.1, self._poll_ask_user)
        self.call_after_refresh(self._repaint_stable_frame)

    def on_resize(self, event: events.Resize) -> None:
        self._apply_width_mode(event.size.width)

    def _apply_width_mode(self, width: int) -> None:
        narrow = width < 92
        if narrow == self._narrow_mode:
            return
        self._narrow_mode = narrow
        if narrow:
            self.screen.add_class("narrow")
        else:
            self.screen.remove_class("narrow")
        self.screen.refresh(layout=True, repaint=True)

    def _repaint_stable_frame(self) -> None:
        """Repaint once after initial focus/layout changes to avoid stale terminal frames."""
        driver = self._driver
        if os.name == "nt" and not self.is_headless and driver is not None:
            driver.write("\x1b[2J\x1b[H")
        self.screen.refresh(layout=True, repaint=True)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        command, _, argument = prompt.partition(" ")
        command = command.casefold()
        if command in {"/plan", "/do", "/mode"}:
            event.input.value = ""
            await self._handle_mode_command(command, argument.strip() or None)
            return
        if self._active_worker is not None:
            return
        event.input.value = ""
        event.input.disabled = True
        event.input.placeholder = "Working…"
        self._last_idle_ctrl_c_at = None
        self.query_one("#footer-hint", Static).update("Ctrl+C to cancel")
        session_line = self.query("#welcome-right .feed-muted").first(Static)
        if session_line is not None:
            session_line.update("Conversation active")

        chat = self.query_one("#conversation", VerticalScroll)
        user_row = Horizontal(classes="user-row")
        await chat.mount(user_row)
        await user_row.mount(
            Static("❯", classes="user-mark", markup=False),
            Static(prompt, classes="user-text", markup=False),
        )
        chat.scroll_end(animate=False)
        self._active_worker = self.run_worker(
            self._stream_reply(prompt),
            name="llm-stream",
            group="generation",
            exclusive=True,
        )

    async def _poll_ask_user(self) -> None:
        if self._pending_ask_event is not None:
            return
        pending = self.ask_user_tool.pending_event
        if pending is None:
            return
        self._pending_ask_event = pending
        prompt = InlineQuestionPrompt(
            pending.questions,
            self._complete_ask_user,
            self._handle_inline_mode_command,
        )
        self._inline_question_prompt = prompt
        chat = self.query_one("#conversation", VerticalScroll)
        await chat.mount(prompt)
        chat.scroll_end(animate=False)

    def _complete_ask_user(self, answers: dict[str, str] | None) -> None:
        pending = self._pending_ask_event
        self._pending_ask_event = None
        self._inline_question_prompt = None
        if pending is None or pending.future.done():
            return
        if answers is None:
            answers = {question.name: "User cancelled" for question in pending.questions}
        pending.future.set_result(answers)

    def set_plan_mode(self, enabled: bool) -> None:
        mode = PermissionMode.PLAN if enabled else PermissionMode.ACCEPT_EDITS
        self.set_permission_mode(mode)

    @property
    def permission_mode(self) -> PermissionMode:
        return self.agent.permission_mode

    def set_permission_mode(self, mode: PermissionMode) -> None:
        self.agent.set_permission_mode(mode)
        self._refresh_mode_status()
        if mode is PermissionMode.BYPASS:
            self._allow_pending_permission_for_yolo()

    def _allow_pending_permission_for_yolo(self) -> None:
        request = self._pending_permission_request
        prompt = self._inline_permission_prompt
        self._pending_permission_request = None
        self._inline_permission_prompt = None
        if prompt is not None and not prompt.completed:
            prompt.completed = True
            prompt.display = False
        if request is not None and not request.future.done():
            request.future.set_result(PermissionResponse.ALLOW)

    def _apply_mode_command(self, command: str, argument: str | None = None) -> str:
        if command == "/plan":
            return handle_plan(self)
        if command == "/do":
            return handle_do(self)
        try:
            return handle_permission_mode(self, argument)
        except ValueError as exc:
            return str(exc)

    async def _show_mode_message(self, message: str) -> None:
        chat = self.query_one("#conversation", VerticalScroll)
        await chat.mount(Static(f"  ◇ {message}", classes="tool", markup=False))
        chat.scroll_end(animate=False)

    async def _handle_mode_command(self, command: str, argument: str | None = None) -> None:
        await self._show_mode_message(self._apply_mode_command(command, argument))

    def _handle_inline_mode_command(self, command: str) -> None:
        message = self._apply_mode_command(command)
        self.call_later(self._show_mode_message, message)

    async def _handle_permission_request(self, request: PermissionRequest) -> None:
        self._pending_permission_request = request
        prompt = InlinePermissionPrompt(request, self._complete_permission_request)
        self._inline_permission_prompt = prompt
        chat = self.query_one("#conversation", VerticalScroll)
        await chat.mount(prompt)
        chat.scroll_end(animate=False)

    def _complete_permission_request(self, response: PermissionResponse) -> None:
        request = self._pending_permission_request
        self._pending_permission_request = None
        self._inline_permission_prompt = None
        if request is not None and not request.future.done():
            request.future.set_result(response)

    async def _stream_reply(self, prompt: str) -> None:
        chat = self.query_one("#conversation", VerticalScroll)
        (
            assistant_row,
            assistant_body,
            thinking_label,
            thinking_details,
            answer_widget,
        ) = await self._mount_assistant_response(chat)

        answer_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_widgets: dict[str, ActivityLine] = {}
        needs_response_segment = False
        connected = False
        completed = False
        self._turn_started_at = perf_counter()
        self._live_prompt_text = prompt
        self._live_generated_text = ""
        self._status_task = asyncio.create_task(self._status_loop())

        try:
            self.conversation.add_user_message(prompt)
            async for event in self.agent.run(self.conversation):
                if not connected:
                    connection = self.query_one("#connection", Static)
                    connection.remove_class("failed")
                    connection.add_class("connected")
                    connection.update(self._connected_text())
                    connected = True
                if isinstance(event, (StreamText, ThinkingText)) and needs_response_segment:
                    (
                        assistant_row,
                        assistant_body,
                        thinking_label,
                        thinking_details,
                        answer_widget,
                    ) = await self._mount_assistant_response(chat)
                    answer_parts = []
                    thinking_parts = []
                    needs_response_segment = False
                if isinstance(event, StreamText):
                    if thinking_label.running and not thinking_parts:
                        thinking_label.finish("", hide=True)
                    answer_parts.append(event.text)
                    self._live_generated_text += event.text
                    answer_widget.update(terminal_safe_text("".join(answer_parts)))
                elif isinstance(event, ThinkingText):
                    thinking_label.display = True
                    if event.complete:
                        thinking_label.finish("∴ Thought · Ctrl+O to expand")
                        thinking_details.display = self._thinking_expanded
                    else:
                        thinking_label.set_activity("Thinking…")
                        thinking_parts.append(event.text)
                        self._live_generated_text += event.text
                        thinking_details.display = True
                        thinking_details.update(terminal_safe_text("".join(thinking_parts)))
                elif isinstance(event, ToolUseEvent):
                    if (
                        not answer_parts
                        and not thinking_parts
                        and assistant_row.is_mounted
                        and not needs_response_segment
                    ):
                        await assistant_row.remove()
                    tool_widget = tool_widgets.get(event.tool_id)
                    if tool_widget is None:
                        tool_widget = ActivityLine(
                            "Preparing tool…",
                            classes="tool is-running",
                            indent="  ",
                        )
                        tool_widgets[event.tool_id] = tool_widget
                        await chat.mount(tool_widget)
                    label = self._tool_event_label(event)
                    if event.status == "start":
                        tool_widget.set_activity(f"Preparing {label}")
                    elif event.status == "complete":
                        tool_widget.set_activity(f"Running {label}")
                    else:
                        elapsed = event.elapsed_seconds or 0.0
                        if event.is_error:
                            error = event.detail.splitlines()[0] if event.detail else "failed"
                            tool_widget.finish(f"  ✗ {label} ({elapsed:.1f}s)\n    {error}")
                            tool_widget.add_class("tool-error")
                        else:
                            tool_widget.finish(f"  ✓ {label} ({elapsed:.1f}s)")
                            tool_widget.add_class("tool-success")
                        if not needs_response_segment:
                            await self._render_markdown(
                                assistant_body,
                                answer_widget,
                                "".join(answer_parts),
                            )
                            needs_response_segment = True
                elif isinstance(event, UsageEvent):
                    self._last_input_tokens = event.total_input_tokens
                    self._last_output_tokens = event.total_output_tokens
                elif isinstance(event, TurnComplete):
                    # A tool round completed; the Agent will continue with another model turn.
                    pass
                elif isinstance(event, PermissionRequest):
                    await self._handle_permission_request(event)
                elif isinstance(event, RetryEvent):
                    await chat.mount(
                        Static(f"  ↻ Retrying: {event.reason}", classes="tool", markup=False)
                    )
                elif isinstance(event, CompactNotification):
                    await chat.mount(
                        Static(
                            f"  ◇ Compacted {event.removed_messages} messages",
                            classes="tool",
                            markup=False,
                        )
                    )
                elif isinstance(event, HookEvent):
                    await chat.mount(
                        Static(
                            f"  ◇ Hook {event.hook_name}: {event.message}",
                            classes="tool",
                            markup=False,
                        )
                    )
                elif isinstance(event, ErrorEvent):
                    completed = True
                    await self._show_error(event.message)
                elif isinstance(event, LoopComplete):
                    completed = True
                    if thinking_label.running:
                        thinking_label.finish("", hide=True)
                    self._last_input_tokens = event.input_tokens
                    self._last_output_tokens = event.output_tokens
                    self._last_elapsed = self._elapsed()
                    await self._render_markdown(
                        assistant_body,
                        answer_widget,
                        "".join(answer_parts),
                    )
                    self._update_final_status()
                chat.scroll_end(animate=False)
        except asyncio.CancelledError:
            if answer_widget.is_mounted:
                answer_widget.update(
                    terminal_safe_text("".join(answer_parts) + "\n\nGeneration cancelled.")
                )
            connection = self.query_one("#connection", Static)
            connection.remove_class("connected")
            connection.update("◇ Cancelled · ready")
            raise
        except LLMError as exc:
            connection = self.query_one("#connection", Static)
            connection.remove_class("connected")
            connection.add_class("failed")
            connection.update("! Request failed · check details below")
            await self._show_error(str(exc))
        except Exception as exc:
            connection = self.query_one("#connection", Static)
            connection.remove_class("connected")
            connection.add_class("failed")
            connection.update("! Agent failed · check details below")
            await self._show_error(f"{type(exc).__name__}: {exc}")
        finally:
            if thinking_label.running:
                if thinking_parts:
                    thinking_label.finish("∴ Thought interrupted")
                else:
                    thinking_label.finish("", hide=True)
            for tool_widget in tool_widgets.values():
                if tool_widget.running:
                    label = tool_widget.label.removeprefix("Preparing ").removeprefix("Running ")
                    tool_widget.finish(f"  - {label} · stopped")
            if self._status_task is not None:
                self._status_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._status_task
                self._status_task = None
            if not completed:
                self._last_elapsed = self._elapsed()
            self._turn_started_at = None
            self._active_worker = None
            question_prompt = self._inline_question_prompt
            if question_prompt is not None and not question_prompt.completed:
                question_prompt.action_cancel()
            self._inline_question_prompt = None
            self._pending_ask_event = None
            permission_prompt = self._inline_permission_prompt
            if permission_prompt is not None and not permission_prompt.completed:
                permission_prompt.action_deny()
            self._inline_permission_prompt = None
            pending_permission = self._pending_permission_request
            self._pending_permission_request = None
            if pending_permission is not None and not pending_permission.future.done():
                pending_permission.future.set_result(PermissionResponse.DENY)
            input_widget = self.query_one("#prompt", Input)
            input_widget.disabled = False
            input_widget.placeholder = 'Try "explain this project"'
            self.query_one("#footer-hint", Static).update("Ctrl+C ×2 exit · Ctrl+O think")
            input_widget.focus()

    async def _mount_assistant_response(
        self,
        chat: VerticalScroll,
    ) -> tuple[Horizontal, Vertical, ActivityLine, Static, Static]:
        thinking_label = ActivityLine(
            "Thinking…",
            classes="thinking-label is-running",
        )
        thinking_details = Static("", classes="thinking-details", markup=False)
        answer_widget = Static("", classes="assistant", markup=False)
        thinking_details.display = False
        assistant_row = Horizontal(classes="assistant-row")
        assistant_body = Vertical(classes="assistant-body")
        await chat.mount(assistant_row)
        await assistant_row.mount(
            Static("●", classes="assistant-mark", markup=False),
            assistant_body,
        )
        await assistant_body.mount(thinking_label, thinking_details, answer_widget)
        self._thinking_widgets.append((thinking_label, thinking_details))
        return (
            assistant_row,
            assistant_body,
            thinking_label,
            thinking_details,
            answer_widget,
        )

    def _tool_event_label(self, event: ToolUseEvent) -> str:
        display_names = {
            "ReadFile": "Read",
            "WriteFile": "Write",
            "EditFile": "Edit",
        }
        name = display_names.get(event.tool_name, event.tool_name)
        arguments = event.arguments or {}
        preferred_keys = {
            "ReadFile": ("file_path",),
            "WriteFile": ("file_path",),
            "EditFile": ("file_path",),
            "Glob": ("pattern", "path"),
            "Grep": ("pattern", "path", "include"),
            "Bash": ("command",),
            "ToolSearch": ("query",),
        }.get(event.tool_name, tuple(arguments)[:2])
        values: list[str] = []
        for key in preferred_keys:
            value = arguments.get(key)
            if value in (None, "", "."):
                continue
            rendered = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            if key.endswith("path") or key == "file_path":
                path = Path(rendered)
                if path.is_absolute():
                    with suppress(ValueError):
                        rendered = path.relative_to(self.work_dir).as_posix()
            values.append(rendered.replace("\n", " ")[:100])
        return f"{name}: {' · '.join(values)}" if values else name

    async def _render_markdown(
        self,
        body: Vertical,
        plain_widget: Static,
        text: str,
    ) -> None:
        if not text:
            return
        await plain_widget.remove()
        await body.mount(Markdown(terminal_safe_text(text), classes="assistant-markdown"))

    async def _status_loop(self) -> None:
        while True:
            self._update_live_status()
            await asyncio.sleep(0.1)

    def _elapsed(self) -> float:
        if self._turn_started_at is None:
            return self._last_elapsed
        return max(0.0, perf_counter() - self._turn_started_at)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        ascii_count = sum(1 for char in text if ord(char) < 128)
        non_ascii_count = len(text) - ascii_count
        return math.ceil(ascii_count / 4) + non_ascii_count

    def _update_live_status(self) -> None:
        input_estimate = self._estimate_tokens(self._live_prompt_text)
        output_estimate = self._estimate_tokens(self._live_generated_text)
        self.query_one("#model-status", Static).update(
            f"{self.config.provider.model} · "
            f"~{input_estimate} in / ~{output_estimate} out · "
            f"{self._elapsed():.1f}s"
        )

    def _update_final_status(self) -> None:
        self.query_one("#model-status", Static).update(
            f"{self.config.provider.model} · "
            f"{self._last_input_tokens} in / {self._last_output_tokens} out · "
            f"{self._last_elapsed:.1f}s"
        )

    async def _show_error(self, message: str) -> None:
        chat = self.query_one("#conversation", VerticalScroll)
        await chat.mount(Static(message, classes="error", markup=False))
        chat.scroll_end(animate=False)

    def action_toggle_thinking(self) -> None:
        self._thinking_expanded = not self._thinking_expanded
        action = "collapse" if self._thinking_expanded else "expand"
        for label, details in self._thinking_widgets:
            if str(details.render()):
                details.display = self._thinking_expanded
                label.update(f"∴ Thought · Ctrl+O to {action}")

    def action_cycle_mode(self) -> None:
        message = handle_permission_mode(self)
        self.call_later(self._show_mode_message, message)

    def action_cancel_or_quit(self) -> None:
        if self._active_worker is not None:
            self._last_idle_ctrl_c_at = None
            worker = self._active_worker
            self._active_worker = None
            worker.cancel()
            return

        now = perf_counter()
        last_press = self._last_idle_ctrl_c_at
        if last_press is not None and now - last_press <= self.CTRL_C_EXIT_WINDOW_SECONDS:
            self._last_idle_ctrl_c_at = None
            self.exit()
            return

        self._last_idle_ctrl_c_at = now
        self.query_one("#footer-hint", Static).update("Copy · Ctrl+C again to exit")
        self.set_timer(
            self.CTRL_C_EXIT_WINDOW_SECONDS,
            lambda: self._expire_idle_ctrl_c(now),
        )

    def _expire_idle_ctrl_c(self, armed_at: float) -> None:
        if self._last_idle_ctrl_c_at != armed_at:
            return
        self._last_idle_ctrl_c_at = None
        self.query_one("#footer-hint", Static).update("Ctrl+C ×2 exit · Ctrl+O think")

    async def action_clear_chat(self) -> None:
        chat = self.query_one("#conversation", VerticalScroll)
        for child in list(chat.children):
            if child.id not in {"welcome-panel", "connection"}:
                await child.remove()


__all__ = [
    "ActivityLine",
    "InlinePermissionPrompt",
    "InlineQuestionPrompt",
    "MewCodeApp",
    "terminal_safe_text",
]
