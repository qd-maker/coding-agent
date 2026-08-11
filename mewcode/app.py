"""Textual terminal UI and provider-to-Agent assembly."""

from __future__ import annotations

import asyncio
import json
import math
import os
import subprocess
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
from textual.message import Message as TextualMessage
from textual.timer import Timer
from textual.widgets import Markdown, Static, TextArea
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
    ToolBatchEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
    VerificationEvent,
)
from mewcode.agents import AgentLoader, TaskManager, TraceRegistry, inject_task_notifications
from mewcode.agents.tool_filter import build_teammate_tools
from mewcode.cache import FileCache
from mewcode.client import LLMError, create_client
from mewcode.commands import CommandContext, CommandRegistry, complete, parse_command
from mewcode.commands.completion import CompletionPopup
from mewcode.commands.handlers import (
    TEAM_COMMAND,
    WORKTREE_COMMAND,
    handle_permission_mode,
    permission_mode_label,
    register_all_commands,
)
from mewcode.commands.handlers.skill import SKILL_COMMAND
from mewcode.commands.handlers.skill_register import register_skill_commands
from mewcode.config import AppConfig
from mewcode.context import ensure_session_dir, estimate_conversation_tokens
from mewcode.conversation import ConversationManager, Message
from mewcode.evidence import EvidenceBundle
from mewcode.hooks import HookContext, HookEngine, load_hooks
from mewcode.mcp import MCPManager
from mewcode.memory import MemoryManager, SessionManager, load_instructions
from mewcode.permission_dialog import InlinePermissionPrompt, InlineQuestionPrompt
from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.skills import SkillExecutor, SkillLoader, validate_skill_dependencies
from mewcode.teams import TeamManager
from mewcode.teams.transcript import load_transcript
from mewcode.tools import ToolRegistry, create_default_registry, register_task_tools
from mewcode.tools.agent_tool import AgentTool
from mewcode.tools.ask_user import AskUserEvent, AskUserTool
from mewcode.tools.impl import ToolSearchTool
from mewcode.tools.load_skill import LoadSkill
from mewcode.tools.send_message import SendMessageTool
from mewcode.tools.synthetic_output import SyntheticOutputTool
from mewcode.tools.team_create import TEAM_RUNTIME_TOOLS, TeamCreateTool
from mewcode.tools.team_delete import TeamDeleteTool
from mewcode.tools.team_merge import TeamMergeTool
from mewcode.tools.team_stop import TeamStopTool
from mewcode.worktree import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    WorktreeManager,
    start_stale_cleanup_task,
)

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
    return str(safe.replace("\ufe0f", "").replace("\u200d", ""))


class PromptComposer(TextArea):
    """Multiline prompt editor with submit, history, and reverse-search behavior."""

    class Submitted(TextualMessage):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    BINDINGS = [
        Binding("enter", "submit", "Submit", show=False, priority=True),
        Binding("shift+enter,alt+enter", "newline", "New line", show=False, priority=True),
        Binding("up", "history_previous", "Previous prompt", show=False, priority=True),
        Binding("down", "history_next", "Next prompt", show=False, priority=True),
        Binding("ctrl+r", "history_search", "Search history", show=False, priority=True),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._history: list[str] = []
        self._history_index = 0
        self._history_draft = ""
        self._search_query = ""

    def on_mount(self) -> None:
        self._resize_to_content()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area is self:
            self._resize_to_content()

    def _resize_to_content(self) -> None:
        line_count = max(1, self.text.count("\n") + 1)
        self.styles.height = min(6, line_count)

    def record_history(self, value: str) -> None:
        value = value.strip()
        if value and (not self._history or self._history[-1] != value):
            self._history.append(value)
        self._history_index = len(self._history)
        self._history_draft = ""
        self._search_query = ""

    def _load_history(self, value: str) -> None:
        self.load_text(value)
        lines = value.splitlines() or [""]
        self.cursor_location = (len(lines) - 1, len(lines[-1]))

    def action_submit(self) -> None:
        value = self.text.strip()
        if not value:
            return
        self.record_history(value)
        self.clear()
        self.post_message(self.Submitted(value))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_history_previous(self) -> None:
        if self.cursor_location[0] > 0:
            self.action_cursor_up()
            return
        if not self._history:
            return
        if self._history_index == len(self._history):
            self._history_draft = self.text
        self._history_index = max(0, self._history_index - 1)
        self._load_history(self._history[self._history_index])

    def action_history_next(self) -> None:
        if self.cursor_location[0] < self.text.count("\n"):
            self.action_cursor_down()
            return
        if self._history_index >= len(self._history):
            return
        self._history_index += 1
        value = (
            self._history_draft
            if self._history_index == len(self._history)
            else self._history[self._history_index]
        )
        self._load_history(value)

    def action_history_search(self) -> None:
        if not self._history:
            return
        if not self._search_query:
            self._search_query = self.text.strip()
            self._history_index = len(self._history)
        for index in range(self._history_index - 1, -1, -1):
            candidate = self._history[index]
            if not self._search_query or self._search_query.casefold() in candidate.casefold():
                self._history_index = index
                self._load_history(candidate)
                return


class ToolCard(Vertical):
    """Collapsible, structured projection of one Tool execution."""

    can_focus = True

    def __init__(self, tool_id: str) -> None:
        super().__init__(classes="tool-card")
        self.tool_id = tool_id
        self.header = ActivityLine("Preparing tool…", classes="tool is-running", indent="  ")
        self.details = Static("", classes="tool-details", markup=False)
        self.details.display = False
        self.running = True
        self.label = "Preparing tool…"

    def compose(self) -> ComposeResult:
        yield self.header
        yield self.details

    def set_activity(self, label: str) -> None:
        self.label = label
        self.running = True
        self.header.set_activity(label)

    def finish(self, text: str, *, is_error: bool = False) -> None:
        self.running = False
        self.header.finish(text)
        self.header.add_class("tool-error" if is_error else "tool-success")

    def update_details(self, event: ToolUseEvent) -> None:
        sections: list[str] = []
        if event.arguments:
            rendered = json.dumps(event.arguments, ensure_ascii=False, indent=2)
            sections.append("Arguments:\n" + rendered)
        if event.exit_code is not None:
            sections.append(f"Exit code: {event.exit_code}")
        if event.preview:
            sections.append("Preview:\n" + event.preview)
        elif event.detail:
            sections.append("Result:\n" + event.detail[:2000])
        if event.artifact_path:
            sections.append(f"Artifact: {event.artifact_path}")
        if event.diagnostics:
            sections.append("Diagnostics:\n" + "\n".join(event.diagnostics))
        self.details.update("\n\n".join(sections))

    def toggle_details(self) -> None:
        self.details.display = not self.details.display

    def on_click(self) -> None:
        self.toggle_details()


class RunResultCard(Static):
    """Fixed completion summary backed by deterministic evidence."""

    def __init__(
        self,
        evidence: EvidenceBundle,
        elapsed_seconds: float,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> None:
        diff = evidence.diff_stat
        tests = evidence.tests
        passed = sum(item.exit_code == 0 for item in tests)
        failed = evidence.outcome == "verification_failed"
        icon = "✗" if failed else "◇" if evidence.outcome == "waiting_background" else "✓"
        lines = [
            f"{icon} {evidence.outcome.replace('_', ' ').title()} with evidence",
            f"Files changed  {len(evidence.changed_files)}",
            f"Diff           +{diff.added} / -{diff.removed}",
            f"Tests          {passed}/{len(tests)} passed" if tests else "Tests          not run",
            f"Diagnostics    {len(evidence.diagnostics)}",
            f"Unresolved     {len(evidence.unresolved)}",
            (
                f"Token          {input_tokens} in / {output_tokens} out"
                if input_tokens is not None and output_tokens is not None
                else "Token          pending"
            ),
            f"Elapsed        {elapsed_seconds:.1f}s",
        ]
        if evidence.changed_files:
            lines.append("Files:\n  " + "\n  ".join(evidence.changed_files))
        if evidence.unresolved:
            lines.append("Needs attention:\n  " + "\n  ".join(evidence.unresolved))
        classes = "result-card result-error" if failed else "result-card"
        super().__init__("\n".join(lines), classes=classes, markup=False)


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

    def render(self) -> str:
        """Derive a live frame from monotonic time even when a timer tick is delayed."""

        if not self.running:
            return str(super().render())
        elapsed = max(0.0, perf_counter() - self._started_at)
        frame_index = int(elapsed / 0.09) % len(_SPINNER_FRAMES)
        return f"{self.indent}{_SPINNER_FRAMES[frame_index]} {self.label} · {elapsed:.1f}s"

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

    #welcome-panel.collapsed {
        height: 3;
    }

    #welcome-collapsed {
        display: none;
        height: 1;
        padding: 0 1;
        color: #c9bee9;
    }

    #welcome-panel.collapsed #welcome-content {
        display: none;
    }

    #welcome-panel.collapsed #welcome-collapsed {
        display: block;
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

    Screen.narrow #welcome-panel.collapsed {
        height: 3;
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

    Screen.narrow #mode-status {
        width: 28;
    }

    Screen.narrow #git-status {
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

    .tool-card {
        width: 100%;
        height: auto;
    }

    .tool-batch {
        height: auto;
        margin: 0 1 1 2;
        padding: 0 1;
        border-left: solid #6b5a9d;
    }

    .tool-batch-title {
        height: 1;
        color: #9e8fd2;
    }

    .tool-card:focus {
        background: #1d1c23;
    }

    .tool-details {
        width: 100%;
        height: auto;
        margin-left: 5;
        margin-right: 2;
        padding: 0 1;
        border-left: solid #433c56;
        background: #131318;
        color: #96919f;
    }

    .result-card {
        width: 100%;
        height: auto;
        margin: 1 0;
        padding: 1 2;
        border: solid #4f8065;
        background: #171d1a;
        color: #cce8d5;
    }

    .result-card.result-error {
        border: solid #74464f;
        background: #21191b;
        color: #f1b2b6;
    }

    #new-events {
        width: 100%;
        height: 1;
        text-align: center;
        background: #26252d;
        color: #b5a5ec;
        display: none;
    }

    #composer {
        width: 100%;
        height: auto;
        min-height: 3;
        max-height: 8;
        padding: 0 1;
        border-top: solid #625d6d;
        border-bottom: solid #625d6d;
        background: #1b1a20;
        align-vertical: middle;
    }

    #prompt-mark {
        width: 2;
        height: 100%;
        content-align: center middle;
        color: #a694e8;
        text-style: bold;
    }

    #prompt {
        width: 1fr;
        height: 1;
        max-height: 6;
        padding: 0;
        border: none;
        background: transparent;
        color: #efedf3;
    }

    #prompt:focus {
        border: none;
        background: transparent;
    }

    #prompt > .text-area--placeholder {
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

    #git-status {
        width: 20;
        height: 1;
        overflow: hidden;
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

    TextArea.-disabled {
        opacity: 70%;
    }
    """
    CTRL_C_EXIT_WINDOW_SECONDS = 1.5

    BINDINGS = [
        ("ctrl+c", "cancel_or_quit", "Cancel / Quit"),
        ("ctrl+l", "clear_chat", "Clear display"),
        ("ctrl+o", "toggle_thinking", "Toggle thinking"),
        Binding("tab", "command_complete", "Complete command", show=False, priority=True),
        Binding("shift+tab", "cycle_mode", "Permission mode", priority=True),
        Binding("escape", "background_active_subagent", "Background Agent", show=False),
        Binding("ctrl+t", "toggle_last_tool", "Tool details", show=False),
        Binding("end,ctrl+end", "jump_to_latest", "Latest event", show=False, priority=True),
    ]

    def __init__(
        self,
        config: AppConfig,
        *,
        hook_engine: HookEngine | None = None,
        coordinator_mode: bool = False,
        team_name: str | None = None,
        team_manager: TeamManager | None = None,
        resume_worktree: bool = False,
        teammate_mode: str | None = None,
        enable_coordinator_mode: bool | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self._teammate_mode = config.teammate_mode if teammate_mode is None else teammate_mode
        self._enable_coordinator_mode = (
            config.enable_coordinator_mode
            if enable_coordinator_mode is None
            else enable_coordinator_mode
        )
        self.client = create_client(config.provider)
        self.work_dir = Path.cwd().resolve()
        self._mcp_server_configs = list(config.mcp_servers)
        self._mcp_manager = MCPManager()
        self._mcp_manager.load_configs(self._mcp_server_configs)
        self._mcp_init_task: asyncio.Task[None] | None = None
        configured_mcp = ", ".join(item.name for item in self._mcp_server_configs)
        self._mcp_instructions = (
            "Configured MCP servers are available lazily through ToolSearch: " + configured_mcp
            if configured_mcp
            else ""
        )
        self._mcp_instructions_injected = False
        self._mcp_connected_count = 0
        self._mcp_tool_count = 0
        self._mcp_errors: list[str] = []
        self.conversation = ConversationManager()
        self._instructions_content = load_instructions(self.work_dir)
        self.memory_manager = MemoryManager(self.work_dir)
        self._resume_candidates: list[str] = []
        self._session_saved_message_ids: set[int] = set()
        self.command_registry = CommandRegistry()
        register_all_commands(self.command_registry)
        self.file_cache = FileCache()
        self.registry: ToolRegistry = create_default_registry(
            self.file_cache,
            work_dir=self.work_dir,
        )
        self.registry.register(
            ToolSearchTool(
                self.registry,
                protocol=config.provider.protocol,
                external_initializer=self._ensure_mcp_tools,
            )
        )
        self.ask_user_tool = AskUserTool()
        self.registry.register(self.ask_user_tool)
        self._load_skill_tool = LoadSkill()
        self.registry.register(self._load_skill_tool)
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
        self.hook_engine = hook_engine or HookEngine(load_hooks(config.raw_hooks))
        self.agent = Agent(
            client=self.client,
            system=config.system_prompt,
            registry=self.registry,
            protocol=config.provider.protocol,
            work_dir=self.work_dir,
            conversation=self.conversation,
            permission_checker=self.permission_checker,
            context_window=200_000,
            instructions_content=self._instructions_content,
            memory_manager=self.memory_manager,
            hook_engine=self.hook_engine,
            coordinator_mode=coordinator_mode,
            team_name=team_name,
            team_manager=team_manager,
        )
        self.agent_loader = AgentLoader(self.work_dir)
        self.agent_loader.load_all()
        self.trace_registry = TraceRegistry()
        self.task_manager = TaskManager(self.trace_registry)
        self.worktree_manager = WorktreeManager(
            self.work_dir,
            file_cache=self.file_cache,
            symlink_directories=config.worktree.symlink_directories,
        )
        self.worktree_manager.add_work_dir_callback(self._set_active_work_dir)
        self.command_registry.register_sync(WORKTREE_COMMAND)
        self.command_registry.register_sync(TEAM_COMMAND)
        self.registry.register(EnterWorktreeTool(self.worktree_manager))
        self.registry.register(ExitWorktreeTool(self.worktree_manager))
        if resume_worktree:
            self.worktree_manager.restore_session()
        self.team_manager = team_manager or TeamManager(
            self.worktree_manager,
            self.trace_registry,
        )
        self.agent._team_manager = self.team_manager
        member = None
        if team_name:
            self.agent.team_name = team_name
            team = self.team_manager.get_team(team_name)
            teammate_name = os.getenv("MEWCODE_TEAMMATE_NAME", "")
            member = team.get_member(teammate_name) if team is not None and teammate_name else None
            if member is not None:
                self.agent.agent_id = member.agent_id
                transcript = load_transcript(
                    team_name,
                    member.agent_id,
                    self.team_manager.teams_root,
                )
                if transcript is not None:
                    self.conversation = transcript
                    self.agent.conversation = transcript

        # Main entry sees only TeamCreate. Collaboration tools are present but disabled
        # until a team exists; a teammate process receives them immediately.
        existing_names = {tool.name for tool in self.registry.list_tools()}
        if not {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate"} & existing_names:
            register_task_tools(self.registry, self.team_manager, team_name or "__active__")
        self.registry.register(SendMessageTool(self.team_manager, self.agent))
        self.registry.register(TeamDeleteTool(self.team_manager, self.agent))
        self.registry.register(TeamMergeTool(self.team_manager, self.agent))
        self.registry.register(TeamStopTool(self.team_manager, self.agent))
        self.registry.register(SyntheticOutputTool())
        self.registry.register(
            TeamCreateTool(
                self.team_manager,
                self.agent,
                self._teammate_mode,
                is_interactive=not bool(team_name),
                enable_coordinator_mode=self._enable_coordinator_mode,
            )
        )
        if not team_name:
            for tool_name in TEAM_RUNTIME_TOOLS:
                self.registry.disable(tool_name)
        self.agent_tool = AgentTool(
            agent_loader=self.agent_loader,
            task_manager=self.task_manager,
            trace_manager=self.trace_registry,
            parent_agent=self.agent,
            provider_config=config.provider,
            enable_fork=True,
            worktree_manager=self.worktree_manager,
            team_manager=self.team_manager,
        )
        self.registry.register(self.agent_tool)
        if team_name and member is not None:
            self.registry = build_teammate_tools(
                self.registry,
                member.backend_type,
                work_dir=self.work_dir,
            )
            self.agent.registry = self.registry
            self.agent.tools = self.registry
        self.agent.set_agent_catalog(self.agent_loader.build_catalog_prompt())
        self.skill_loader = SkillLoader(self.work_dir)
        loaded_skills = self.skill_loader.load_all()
        validate_skill_dependencies(loaded_skills.values(), self.registry)
        self._load_skill_tool.set_loader(self.skill_loader)
        self._load_skill_tool.set_agent(self.agent)
        self.skill_executor = SkillExecutor(
            self.agent,
            client=self.client,
            protocol=config.provider.protocol,
        )
        self.agent.set_skill_catalog(self.skill_loader.build_catalog_prompt())
        self.command_registry.register_sync(SKILL_COMMAND)
        register_skill_commands(
            self.command_registry,
            self.skill_loader,
            self.skill_executor,
        )
        self._skill_background_tasks: set[asyncio.Task[None]] = set()
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
        self._queued_prompts: list[str] = []
        self._new_event_count = 0
        self._last_tool_card: ToolCard | None = None
        self._phase = "Ready"
        self._git_status = "git --"
        self._stale_cleanup_task: asyncio.Task[None] | None = None
        self._headless_started = False
        self._shutdown_complete = False
        # Create persistent state last so a fail-fast Skill/Agent/Worktree validation
        # cannot leave an empty session artifact behind.
        self.session_manager = SessionManager(self.work_dir)
        self.session_manager.cleanup()
        self.session = self.session_manager.create()

    def _set_active_work_dir(self, work_dir: Path) -> None:
        """Retarget checkout-local state after enter/exit or crash recovery."""

        active = work_dir.resolve()
        self.file_cache.clear()
        self.registry.set_work_dir(active)
        self.agent.work_dir = active
        self.agent.session_dir = ensure_session_dir(active)
        self.agent._plan_path_cache = None
        self._instructions_content = load_instructions(active)
        self.agent.instructions_content = self._instructions_content
        self.memory_manager = MemoryManager(active)
        self.agent.memory_manager = self.memory_manager
        self.permission_checker.sandbox = PathSandbox(active)
        self.permission_checker.rule_engine = RuleEngine(
            user_rules_path=Path.home() / ".mewcode" / "permissions.yaml",
            project_rules_path=active / ".mewcode" / "permissions.yaml",
            local_rules_path=active / ".mewcode" / "permissions.local.yaml",
        )

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="conversation"):
            with Vertical(id="welcome-panel") as welcome_panel:
                welcome_panel.border_title = f" MewCode v{__version__} "
                cast(Any, welcome_panel).border_title_align = "left"
                yield Static(
                    f"{self.agent.work_dir.name} · {self.config.provider.model}",
                    id="welcome-collapsed",
                    markup=False,
                )
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
        yield Static("", id="new-events", markup=False)
        with Horizontal(id="composer"):
            yield Static("❯", id="prompt-mark", markup=False)
            yield PromptComposer(
                placeholder='Try "explain this project" · Shift+Enter newline',
                id="prompt",
                soft_wrap=True,
                show_line_numbers=False,
                compact=True,
            )
        with Horizontal(id="statusbar"):
            yield Static(self._idle_footer_text(), id="footer-hint", markup=False)
            yield Static(self._mode_status_text(), id="mode-status", markup=False)
            yield Static(self._git_status, id="git-status", markup=False)
            yield Static(self._idle_status_text(), id="model-status", markup=False)
        yield CompletionPopup(id="completion-popup")

    def _provider_name(self) -> str:
        return "Anthropic" if self.config.provider.protocol == "anthropic" else "OpenAI"

    def _welcome_model_line(self) -> str:
        return f"{self.config.provider.model} · {self._provider_name()}"

    def _compact_cwd(self, limit: int = 52) -> str:
        value = str(self.agent.work_dir if hasattr(self, "agent") else self.work_dir)
        if len(value) <= limit:
            return value
        path = Path(value)
        tail = str(Path(path.parent.name) / path.name)
        return f"…{Path(value).anchor}{tail}"[-limit:]

    def _mcp_status_suffix(self) -> str:
        if not self._mcp_server_configs:
            return ""
        states = getattr(self._mcp_manager, "server_states", {})
        if any(getattr(state, "value", str(state)) == "connecting" for state in states.values()):
            return " · MCP connecting"
        total = len(self._mcp_server_configs)
        if not self._mcp_connected_count and not self._mcp_errors:
            return f" · MCP idle ({total})"
        return f" · MCP {self._mcp_connected_count}/{total} servers · {self._mcp_tool_count} tools"

    def _mcp_status_report(self) -> list[str]:
        """Return command-friendly per-server states without forcing a connection."""

        states = getattr(self._mcp_manager, "server_states", {})
        lines = []
        for config in self._mcp_server_configs:
            state = states.get(config.name, "idle")
            lines.append(f"{config.name}={getattr(state, 'value', state)}")
        lines.extend(f"error: {message}" for message in self._mcp_errors)
        return lines

    def _ready_text(self) -> str:
        return f"◇ Ready · {self._provider_name()} API · streaming{self._mcp_status_suffix()}"

    def _connected_text(self) -> str:
        return f"● Connected · {self._provider_name()} API · streaming{self._mcp_status_suffix()}"

    def _idle_status_text(self) -> str:
        return f"{self.config.provider.model} · 0 in / 0 out · 0.0s"

    @staticmethod
    def _idle_footer_text() -> str:
        return "/help · /status · Tab complete · Ctrl+O think"

    def _mode_status_text(self) -> Text:
        mode = self.permission_mode
        colors = {
            PermissionMode.ACCEPT_EDITS: "bold #72d39a",
            PermissionMode.PLAN: "bold #b5a5ec",
            PermissionMode.BYPASS: "bold #e36a75",
        }
        if mode is PermissionMode.BYPASS:
            return Text.assemble(
                ("YOLO on", colors[mode]),
                (" · auto-approve · sandbox enforced", "#b46a72"),
            )
        return Text.assemble(
            (f"{permission_mode_label(mode)} on", colors.get(mode, "bold #72d39a")),
            (" (shift+tab to cycle)", "#817d89"),
        )

    def _refresh_mode_status(self) -> None:
        status = self.query("#mode-status").first(Static)
        if status is not None:
            status.update(self._mode_status_text())

    async def on_mount(self) -> None:
        self._apply_width_mode(self.size.width)
        self.query_one("#prompt", PromptComposer).focus()
        self.set_interval(0.1, self._poll_ask_user)
        self.set_interval(0.25, self._poll_background_tasks)
        self.call_after_refresh(self._repaint_stable_frame)
        asyncio.create_task(self._refresh_git_status())
        if self.config.worktree.enabled:
            self._stale_cleanup_task = asyncio.create_task(
                start_stale_cleanup_task(
                    self.worktree_manager,
                    self.config.worktree.stale_cleanup_interval,
                    self.config.worktree.stale_cutoff_hours,
                )
            )
        await self._run_app_hook("startup", message=str(self.work_dir))

    async def _run_app_hook(
        self,
        event_name: str,
        *,
        tool_name: str = "",
        tool_args: dict[str, Any] | None = None,
        message: str = "",
        error: str = "",
        show_notifications: bool = True,
    ) -> None:
        await self.hook_engine.run_hooks(
            event_name,
            HookContext(
                event_name=event_name,
                tool_name=tool_name,
                tool_args=dict(tool_args or {}),
                message=message,
                error=error,
                agent_id=self.agent.agent_id,
            ),
        )
        self.agent.hook_prompts.extend(self.hook_engine.get_prompt_messages())
        notifications = self.hook_engine.drain_notifications()
        if show_notifications:
            for notification in notifications:
                await self.add_system_message(f"Hook {notification.hook_id}: {notification.output}")

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

    async def on_prompt_composer_submitted(self, event: PromptComposer.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt:
            return
        self.query_one(CompletionPopup).hide()
        if await self._dispatch_command(prompt):
            return
        await self.send_user_message(prompt)

    async def send_user_message(self, prompt: str) -> None:
        """Submit a regular prompt to the Agent loop through the active UI."""

        if self._active_worker is not None:
            if len(self._queued_prompts) >= 5:
                await self.add_system_message("消息队列已满（最多 5 条），请等待或取消当前任务。")
                return
            self._queued_prompts.append(prompt)
            await self.add_system_message(
                f"Queued #{len(self._queued_prompts)}: {prompt.replace(chr(10), ' ')[:120]}"
            )
            self._refresh_runtime_status()
            return
        input_widget = self.query_one("#prompt", PromptComposer)
        input_widget.clear()
        input_widget.placeholder = "Working… type the next request to queue"
        self._phase = "Preparing"
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
        self._set_welcome_collapsed(True)
        self._active_worker = self.run_worker(
            self._stream_reply(prompt),
            name="llm-stream",
            group="generation",
            exclusive=True,
        )

    async def add_system_message(self, text: str) -> None:
        """UIController implementation used by local command handlers."""

        await self._show_mode_message(text)

    def _set_welcome_collapsed(self, collapsed: bool) -> None:
        welcome = self.query_one("#welcome-panel", Vertical)
        welcome.display = True
        welcome.set_class(collapsed, "collapsed")

    def refresh_status(self) -> None:
        """Refresh command-visible status widgets without depending on handlers."""

        self._refresh_mode_status()

    def _build_command_context(self, args: str, *, invoked_name: str = "") -> CommandContext:
        return CommandContext(
            args=args,
            agent=self.agent,
            conversation=self.conversation,
            session=self.session,
            session_manager=self.session_manager,
            memory_manager=self.memory_manager,
            ui=self,
            config={
                "registry": self.command_registry,
                "set_session": self._set_command_session,
                "set_conversation": self._set_command_conversation,
                "clear_conversation": self._clear_command_conversation,
                "render_restored": self.render_restored,
                "persist": self._persist_unsaved_messages,
                "set_permission_mode": self.set_permission_mode,
                "permission_checker": self.permission_checker,
                "resume_candidates": self._resume_candidates,
                "is_busy": lambda: self._active_worker is not None,
                "work_dir": self.agent.work_dir,
                "version": __version__,
                "invoked_name": invoked_name,
                "skill_loader": self.skill_loader,
                "skill_executor": self.skill_executor,
                "tool_registry": self.registry,
                "background_tasks": self._skill_background_tasks,
                "task_manager": self.task_manager,
                "trace_registry": self.trace_registry,
                "agent_loader": self.agent_loader,
                "worktree_manager": self.worktree_manager,
                "team_manager": self.team_manager,
                "mcp_status": self._mcp_status_report,
            },
        )

    async def _poll_background_tasks(self) -> None:
        """Inject completed child results only at a safe parent-conversation boundary."""

        if self._active_worker is not None:
            return
        completed = self.task_manager.poll_completed()
        if not completed:
            return
        notifications = inject_task_notifications(self.conversation, completed)
        self._persist_unsaved_messages()
        for notification in notifications:
            await self.add_system_message(notification)

    async def _dispatch_command(self, text: str) -> bool:
        name, args, is_command = parse_command(text)
        if not is_command:
            return False
        await self._run_app_hook(
            "command_execute",
            tool_name=name,
            tool_args={"name": name, "arguments": args},
            message=text,
        )
        command = self.command_registry.find(name or "help")
        if command is None:
            await self.add_system_message(f"未知命令：/{name}。输入 /help 查看可用命令。")
            return True
        try:
            await command.handler(self._build_command_context(args, invoked_name=name))
        except Exception as exc:  # noqa: BLE001 - one command must not crash the TUI
            await self._run_app_hook(
                "error",
                message=f"/{command.name}: {exc}",
                error=str(exc),
            )
            await self.add_system_message(f"命令 /{command.name} 执行失败：{exc}")
        return True

    def _set_command_session(self, session: Any) -> None:
        self.session = session
        self._session_saved_message_ids.clear()

    def _set_command_conversation(self, conversation: ConversationManager) -> None:
        self.conversation = conversation
        self.agent.conversation = conversation
        self._session_saved_message_ids = {id(item) for item in conversation.history}
        self._mcp_instructions_injected = False

    async def _clear_command_conversation(self) -> None:
        previous = self.session
        previous_id = previous.id
        was_empty = previous.meta.message_count == 0
        previous.close()
        if was_empty:
            self.session_manager.delete(previous_id)
        self._set_command_session(self.session_manager.create())
        self._set_command_conversation(ConversationManager())
        self.agent._loop_count = 0
        self.memory_manager.reset_cursor()
        await self.render_restored()

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
        if self._driver is not None:
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
            self.set_plan_mode(True)
            return "Plan on (shift+tab to cycle)"
        if command == "/do":
            self.set_plan_mode(False)
            return "Accept Edits on (shift+tab to cycle)"
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

    def get_token_count(self) -> int:
        return max(
            int(self.conversation.last_input_tokens or 0),
            estimate_conversation_tokens(self.conversation),
        )

    async def _handle_permission_request(self, request: PermissionRequest) -> None:
        self._pending_permission_request = request
        self._phase = "Awaiting permission"
        self.query_one("#prompt", PromptComposer).disabled = True
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
        composer = self.query_one("#prompt", PromptComposer)
        composer.disabled = False
        composer.focus()
        self._phase = "Running"

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
        live_parts: list[str] = []
        tool_widgets: dict[str, ToolCard] = {}
        active_batch_ids: set[str] = set()
        active_batch_total = 0
        active_batch_done = 0
        last_answer_render = 0.0
        last_thinking_render = 0.0
        needs_response_segment = False
        connected = False
        completed = False
        self._turn_started_at = perf_counter()
        self._live_prompt_text = prompt
        self._live_generated_text = ""
        self._status_task = asyncio.create_task(self._status_loop())

        try:
            if self._mcp_instructions and not self._mcp_instructions_injected:
                self.conversation.add_system_reminder(self._mcp_instructions)
                self._mcp_instructions_injected = True
            user_message = self.conversation.add_user_message(prompt)
            self._persist_message(user_message)
            async for event in self.agent.run(self.conversation):
                follow_before = chat.is_vertical_scroll_end or (
                    float(chat.max_scroll_y) - float(chat.scroll_y) <= 2
                )
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
                    self._phase = "Streaming"
                    if thinking_label.running and not thinking_parts:
                        thinking_label.finish("", hide=True)
                    answer_parts.append(event.text)
                    live_parts.append(event.text)
                    now = perf_counter()
                    if now - last_answer_render >= 0.04:
                        self._live_generated_text = "".join(live_parts)
                        answer_widget.update(terminal_safe_text("".join(answer_parts)))
                        last_answer_render = now
                elif isinstance(event, ThinkingText):
                    self._phase = "Thinking"
                    thinking_label.display = True
                    if event.complete:
                        if thinking_parts:
                            thinking_details.update(terminal_safe_text("".join(thinking_parts)))
                        thinking_label.finish("∴ Thought · Ctrl+O to expand")
                        thinking_details.display = self._thinking_expanded
                    else:
                        thinking_label.set_activity("Thinking…")
                        thinking_parts.append(event.text)
                        live_parts.append(event.text)
                        now = perf_counter()
                        if now - last_thinking_render >= 0.04:
                            self._live_generated_text = "".join(live_parts)
                            thinking_details.display = True
                            thinking_details.update(terminal_safe_text("".join(thinking_parts)))
                            last_thinking_render = now
                elif isinstance(event, ToolBatchEvent):
                    active_batch_ids = set(event.tool_ids)
                    active_batch_total = len(event.tool_ids)
                    active_batch_done = 0
                    self._phase = f"Tool 0/{active_batch_total}"
                    cards = []
                    for tool_id in event.tool_ids:
                        card = tool_widgets.get(tool_id)
                        if card is None:
                            card = ToolCard(tool_id)
                            tool_widgets[tool_id] = card
                        cards.append(card)
                    if event.concurrent and len(cards) > 1:
                        group = Vertical(classes="tool-batch")
                        await chat.mount(group)
                        await group.mount(
                            Static(
                                f"Parallel tool batch · {len(cards)} tools",
                                classes="tool-batch-title",
                                markup=False,
                            ),
                            *cards,
                        )
                    else:
                        for card in cards:
                            if not card.is_mounted:
                                await chat.mount(card)
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
                        tool_widget = ToolCard(event.tool_id)
                        tool_widgets[event.tool_id] = tool_widget
                        self._last_tool_card = tool_widget
                    if event.status == "result" and not tool_widget.is_mounted:
                        await chat.mount(tool_widget)
                    label = self._tool_event_label(event)
                    if event.status == "start":
                        tool_widget.set_activity(f"Preparing {label}")
                    elif event.status == "complete":
                        tool_widget.set_activity(f"Running {label}")
                    else:
                        if event.tool_id in active_batch_ids:
                            active_batch_done += 1
                            self._phase = f"Tool {active_batch_done}/{active_batch_total}"
                        elapsed = event.elapsed_seconds or 0.0
                        if event.is_error:
                            error = event.detail.splitlines()[0] if event.detail else "failed"
                            tool_widget.finish(
                                f"  ✗ {label} ({elapsed:.1f}s)\n    {error}",
                                is_error=True,
                            )
                        else:
                            tool_widget.finish(f"  ✓ {label} ({elapsed:.1f}s)")
                        tool_widget.update_details(event)
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
                    self._persist_unsaved_messages()
                elif isinstance(event, PermissionRequest):
                    await self._handle_permission_request(event)
                elif isinstance(event, RetryEvent):
                    self._phase = "Retrying"
                    self._persist_unsaved_messages()
                    await chat.mount(
                        Static(f"  ↻ Retrying: {event.reason}", classes="tool", markup=False)
                    )
                elif isinstance(event, CompactNotification):
                    self._phase = "Compacting"
                    await chat.mount(
                        Static(
                            "  ◇ Compacted context "
                            f"({event.before_tokens:,} → {event.after_tokens:,} tokens)",
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
                    self._phase = "Failed"
                    await self._show_error(event.message)
                elif isinstance(event, VerificationEvent):
                    self._phase = "Verifying"
                elif isinstance(event, LoopComplete):
                    completed = True
                    phases = {
                        "waiting_background": "Waiting background",
                        "verification_failed": "Verification failed",
                    }
                    self._phase = phases.get(event.outcome, "Completed")
                    self._persist_unsaved_messages()
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
                    if event.evidence is not None:
                        await chat.mount(
                            RunResultCard(
                                event.evidence,
                                self._last_elapsed,
                                input_tokens=event.input_tokens,
                                output_tokens=event.output_tokens,
                            )
                        )
                    self._update_final_status()
                    if answer_parts:
                        self.session.update_summary("".join(answer_parts))
                await self._follow_chat(chat, follow_before)
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
            await self._run_app_hook("error", message=str(exc), error=str(exc))
            await self._show_error(str(exc))
        except Exception as exc:
            connection = self.query_one("#connection", Static)
            connection.remove_class("connected")
            connection.add_class("failed")
            connection.update("! Agent failed · check details below")
            message = f"{type(exc).__name__}: {exc}"
            await self._run_app_hook("error", message=message, error=message)
            await self._show_error(message)
        finally:
            self._persist_unsaved_messages()
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
            input_widget = self.query_one("#prompt", PromptComposer)
            input_widget.disabled = False
            input_widget.placeholder = 'Try "explain this project" · Shift+Enter newline'
            self.query_one("#footer-hint", Static).update(self._idle_footer_text())
            input_widget.focus()
            self._phase = "Ready"
            asyncio.create_task(self._refresh_git_status())
            if self._queued_prompts:
                next_prompt = self._queued_prompts.pop(0)
                self.call_later(self.send_user_message, next_prompt)

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
        context_tokens = self.get_token_count()
        context_percent = min(100, round(context_tokens * 100 / self.agent.context_window))
        running_tasks = sum(task.status == "running" for task in self.task_manager.list_tasks())
        queue = f" · q{len(self._queued_prompts)}" if self._queued_prompts else ""
        tasks = f" · {running_tasks} task" if running_tasks else ""
        self.query_one("#model-status", Static).update(
            f"{self.config.provider.model} · {self._phase} · "
            f"ctx {context_percent}%{queue}{tasks} · "
            f"~{input_estimate} in / ~{output_estimate} out · "
            f"{self._elapsed():.1f}s"
        )

    def _update_final_status(self) -> None:
        context_tokens = self.get_token_count()
        context_percent = min(100, round(context_tokens * 100 / self.agent.context_window))
        self.query_one("#model-status", Static).update(
            f"{self.config.provider.model} · {self._phase} · ctx {context_percent}% · "
            f"{self._last_input_tokens} in / {self._last_output_tokens} out · "
            f"{self._last_elapsed:.1f}s"
        )

    def _refresh_runtime_status(self) -> None:
        if self._turn_started_at is None:
            self._update_final_status()
        else:
            self._update_live_status()

    async def _refresh_git_status(self) -> None:
        try:
            process = await asyncio.wait_for(
                asyncio.to_thread(
                    subprocess.run,
                    ["git", "status", "--short", "--branch"],
                    cwd=str(self.agent.work_dir),
                    capture_output=True,
                    check=False,
                ),
                timeout=2.0,
            )
        except (OSError, TimeoutError):
            return
        if process.returncode != 0:
            return
        lines = process.stdout.decode("utf-8", errors="replace").splitlines()
        branch = "detached"
        if lines and lines[0].startswith("##"):
            branch = lines[0][2:].strip().split("...", 1)[0]
        dirty = any(line and not line.startswith("##") for line in lines)
        self._git_status = f"{branch}{'*' if dirty else ''}"
        widget = next(iter(self.query("#git-status").results(Static)), None)
        if widget is not None:
            widget.update(self._git_status)

    async def _ensure_mcp_tools(self, retry_failed: bool = False) -> None:
        """ToolSearch callback: discover external tools without blocking App startup."""

        await self._init_mcp(
            update_ui=self._driver is not None,
            retry_failed=retry_failed,
        )

    async def _init_mcp(
        self,
        *,
        update_ui: bool = True,
        retry_failed: bool = False,
    ) -> None:
        """Lazily connect configured MCP servers and register their deferred tools."""
        manager = self._mcp_manager
        before_names = {tool.name for tool in self.registry.list_tools()}
        try:
            if retry_failed:
                self._mcp_errors = await manager.register_all_tools(
                    self.registry,
                    retry_failed=True,
                )
            else:
                self._mcp_errors = await manager.register_all_tools(self.registry)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._mcp_errors = [f"MCP initialization failed: {type(exc).__name__}: {exc}"]

        new_tool_names = sorted(
            tool.name for tool in self.registry.list_tools() if tool.name not in before_names
        )
        tool_names = sorted(
            tool.name for tool in self.registry.list_tools() if tool.name.startswith("mcp_")
        )
        connected_names = manager.connected_server_names
        self._mcp_connected_count = len(connected_names)
        self._mcp_tool_count = len(tool_names)

        if new_tool_names or tool_names:
            server_lines = []
            for server_name in connected_names:
                prefix = f"mcp_{server_name}_"
                names = [name for name in tool_names if name.startswith(prefix)]
                tool_summary = ", ".join(names) if names else "(no tools)"
                server_lines.append(f"- {server_name}: {tool_summary}")
            self._mcp_instructions = "\n".join(
                [
                    "MCP servers are connected and their tools are available as deferred tools.",
                    "Use ToolSearch to discover/select an MCP tool before calling it.",
                    *server_lines,
                ]
            )

        if not update_ui or self._driver is None:
            return

        connection = self.query_one("#connection", Static)
        connection.remove_class("connected", "failed")
        if self._mcp_connected_count:
            connection.add_class("connected")
            message = (
                f"● Connected to {self._mcp_connected_count} MCP server(s), "
                f"{self._mcp_tool_count} tools registered"
            )
            if self._mcp_errors:
                message += f" · {len(self._mcp_errors)} failed"
            connection.update(message)
        else:
            connection.add_class("failed")
            connection.update(f"! No MCP servers connected · {len(self._mcp_errors)} failed")

    async def _shutdown_mcp(self) -> None:
        """Cancel pending initialization and close all MCP sessions."""
        task = self._mcp_init_task
        self._mcp_init_task = None
        if task is not None and not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await self._mcp_manager.shutdown()

    async def start_headless(self) -> None:
        """Initialize services that Textual normally starts in ``on_mount``."""

        if self._headless_started:
            return
        self._headless_started = True
        await self._run_app_hook("startup", message=str(self.work_dir), show_notifications=False)
        if self._mcp_instructions and not self._mcp_instructions_injected:
            self.conversation.add_system_reminder(self._mcp_instructions)
            self._mcp_instructions_injected = True

    async def shutdown_headless(self, *, run_hooks: bool = True) -> None:
        """Persist and close a non-interactive App without issuing an extra memory request."""

        await self._shutdown_runtime(force_memory=False, run_hooks=run_hooks)

    async def _shutdown_runtime(self, *, force_memory: bool, run_hooks: bool = True) -> None:
        """Close shared TUI/headless resources exactly once."""

        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        if run_hooks:
            await self._run_app_hook("shutdown", show_notifications=False)
        await self.hook_engine.wait_background()
        stale_task = self._stale_cleanup_task
        self._stale_cleanup_task = None
        if stale_task is not None:
            stale_task.cancel()
            await asyncio.gather(stale_task, return_exceptions=True)
        for task in tuple(self._skill_background_tasks):
            task.cancel()
        if self._skill_background_tasks:
            await asyncio.gather(*tuple(self._skill_background_tasks), return_exceptions=True)
        self._skill_background_tasks.clear()
        await self.task_manager.shutdown()
        await self.team_manager.shutdown_runtime()
        self._persist_unsaved_messages()
        has_dialogue = any(
            message.role == "assistant" and not self._is_transient_message(message)
            for message in self.conversation.history
        )
        if has_dialogue:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    self.agent.flush_memories(self.conversation, force=force_memory),
                    timeout=2.0,
                )
        session_id = self.session.id
        is_empty = self.session.meta.message_count == 0
        self.session.close()
        if is_empty:
            self.session_manager.delete(session_id)
        await self._shutdown_mcp()

    async def on_unmount(self) -> None:
        await self._shutdown_runtime(force_memory=True)

    @staticmethod
    def _is_transient_message(message: Message) -> bool:
        content = message.content
        return (
            content.startswith(
                (
                    "<environment>",
                    "<system-reminder>",
                    "## 项目指令\n",
                    "## 自动记忆\n",
                    "[系统提示] 距离上次会话",
                )
            )
            or content == "好的，我已了解项目背景和记忆。"
        )

    def _persist_message(self, message: Message) -> None:
        marker = id(message)
        if marker in self._session_saved_message_ids:
            return
        self._session_saved_message_ids.add(marker)
        if not self._is_transient_message(message):
            self.session.append(message)

    def _persist_unsaved_messages(self) -> None:
        if self.session.closed:
            return
        for message in self.conversation.history:
            self._persist_message(message)

    async def render_restored(self) -> None:
        """Replace the visible feed with the current restored conversation."""
        await self.action_clear_chat()
        chat = self.query_one("#conversation", VerticalScroll)
        for message in self.conversation.history:
            if self._is_transient_message(message):
                continue
            if message.role == "user" and message.content:
                row = Horizontal(classes="user-row")
                await chat.mount(row)
                await row.mount(
                    Static("❯", classes="user-mark", markup=False),
                    Static(message.content, classes="user-text", markup=False),
                )
            elif message.role == "assistant" and message.content:
                row = Horizontal(classes="assistant-row")
                body = Vertical(classes="assistant-body")
                await chat.mount(row)
                await row.mount(
                    Static("●", classes="assistant-mark", markup=False),
                    body,
                )
                await body.mount(
                    Markdown(
                        terminal_safe_text(message.content),
                        classes="assistant-markdown",
                    )
                )
            elif message.tool_results:
                names = ", ".join(result.tool_use_id for result in message.tool_results)
                await chat.mount(Static(f"  ✓ Restored tool results: {names}", classes="tool"))
        self._session_saved_message_ids = {id(message) for message in self.conversation.history}
        self._mcp_instructions_injected = False
        self._set_welcome_collapsed(bool(self.conversation.history))
        chat.scroll_end(animate=False)

    async def _show_error(self, message: str) -> None:
        chat = self.query_one("#conversation", VerticalScroll)
        follow = chat.is_vertical_scroll_end or float(chat.max_scroll_y) - float(chat.scroll_y) <= 2
        await chat.mount(Static(message, classes="error", markup=False))
        await self._follow_chat(chat, follow)

    async def _follow_chat(self, chat: VerticalScroll, should_follow: bool) -> None:
        badge = self.query_one("#new-events", Static)
        if should_follow:
            chat.scroll_end(animate=False)
            self._new_event_count = 0
            badge.display = False
            return
        self._new_event_count += 1
        badge.update(f"↓ {self._new_event_count} new event(s) · End to follow")
        badge.display = True

    def action_command_complete(self) -> None:
        input_widget = self.query_one("#prompt", PromptComposer)
        prefix = input_widget.text
        popup = self.query_one(CompletionPopup)
        if not prefix.lstrip().startswith("/"):
            popup.hide()
            self.screen.focus_next()
            return
        matches = complete(self.command_registry, prefix)
        if len(matches) == 1:
            input_widget.load_text(f"{matches[0]} ")
            input_widget.cursor_location = (0, len(matches[0]) + 1)
            popup.hide()
            input_widget.focus()
        elif matches:
            popup.show(matches)
        else:
            popup.hide()
            input_widget.focus()

    def on_completion_popup_selected(self, event: CompletionPopup.Selected) -> None:
        input_widget = self.query_one("#prompt", PromptComposer)
        input_widget.load_text(f"{event.value} ")
        input_widget.cursor_location = (0, len(event.value) + 1)
        input_widget.focus()

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "prompt":
            return
        if not event.text_area.text.lstrip().startswith("/"):
            self.query_one(CompletionPopup).hide()

    def action_toggle_last_tool(self) -> None:
        if self._last_tool_card is not None and self._last_tool_card.is_mounted:
            self._last_tool_card.toggle_details()

    def action_jump_to_latest(self) -> None:
        chat = self.query_one("#conversation", VerticalScroll)
        chat.scroll_end(animate=False)
        self._new_event_count = 0
        self.query_one("#new-events", Static).display = False

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

    async def action_background_active_subagent(self) -> None:
        """Move the in-flight child to TaskManager without restarting it."""

        record = await self.agent_tool.detach_foreground()
        if record is None:
            return
        worker = self._active_worker
        self._active_worker = None
        if worker is not None:
            worker.cancel()
        await self._show_mode_message(
            f"Agent 已转入后台，Task ID: {record.task_id}。使用 /tasks 查看。"
        )

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
        self.query_one("#footer-hint", Static).update(self._idle_footer_text())

    async def action_clear_chat(self) -> None:
        chat = self.query_one("#conversation", VerticalScroll)
        for child in list(chat.children):
            if child.id not in {"welcome-panel", "connection"}:
                await child.remove()
        self._set_welcome_collapsed(False)
        self._new_event_count = 0
        self.query_one("#new-events", Static).display = False


__all__ = [
    "ActivityLine",
    "InlinePermissionPrompt",
    "InlineQuestionPrompt",
    "MewCodeApp",
    "PromptComposer",
    "RunResultCard",
    "ToolCard",
    "terminal_safe_text",
]
