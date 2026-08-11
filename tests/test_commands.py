"""Slash-command framework and built-in command tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mewcode.commands import (
    Command,
    CommandContext,
    CommandRegistry,
    CommandType,
    complete,
    parse_command,
)
from mewcode.commands.handlers import ALL_COMMANDS, register_all_commands
from mewcode.commands.handlers.review import REVIEW_PROMPT
from mewcode.conversation import ConversationManager
from mewcode.permissions import PermissionMode, RuleEngine


class FakeUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.prompts: list[str] = []
        self.plan_modes: list[bool] = []
        self.refreshes = 0
        self.tokens = 1_234

    async def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    async def send_user_message(self, text: str) -> None:
        self.prompts.append(text)

    def set_plan_mode(self, enabled: bool) -> None:
        self.plan_modes.append(enabled)

    def get_token_count(self) -> int:
        return self.tokens

    def refresh_status(self) -> None:
        self.refreshes += 1


async def _noop(ctx: CommandContext) -> None:
    del ctx


def _command(name: str, *, aliases: tuple[str, ...] = (), hidden: bool = False) -> Command:
    return Command(name, "test", CommandType.LOCAL, _noop, aliases, hidden=hidden)


def _context(
    registry: CommandRegistry,
    *,
    args: str = "",
    ui: FakeUI | None = None,
    **config: Any,
) -> CommandContext:
    return CommandContext(
        args=args,
        agent=SimpleNamespace(
            permission_mode=PermissionMode.ACCEPT_EDITS,
            registry=SimpleNamespace(list_tools=lambda: []),
        ),
        conversation=ConversationManager(),
        session=SimpleNamespace(id="session_test"),
        session_manager=None,
        memory_manager=None,
        ui=ui or FakeUI(),
        config={"registry": registry, **config},
    )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/foo bar baz", ("foo", "bar baz", True)),
        ("  /HELP   status ", ("help", "status", True)),
        ("/", ("", "", True)),
        ("", ("", "", False)),
        ("   ", ("", "", False)),
        ("hello", ("", "", False)),
    ],
)
def test_parse_command_boundaries(text: str, expected: tuple[str, str, bool]) -> None:
    assert parse_command(text) == expected


def test_registry_find_list_alias_and_normalization() -> None:
    registry = CommandRegistry()
    registry.register_sync(_command("HELP", aliases=("H",)))
    registry.register_sync(_command("hidden", hidden=True))

    assert registry.find("/help") is registry.find("h")
    assert [command.name for command in registry.list_commands()] == ["help"]
    assert [command.name for command in registry.list_commands(include_hidden=True)] == [
        "help",
        "hidden",
    ]


@pytest.mark.parametrize(
    "second",
    [
        _command("help"),
        _command("other", aliases=("help",)),
        _command("h"),
    ],
)
def test_registry_rejects_name_alias_cross_conflicts(second: Command) -> None:
    registry = CommandRegistry()
    registry.register_sync(_command("help", aliases=("h",)))
    with pytest.raises(ValueError, match="conflicts with"):
        registry.register_sync(second)


def test_registry_rejects_duplicate_alias_inside_command() -> None:
    registry = CommandRegistry()
    with pytest.raises(ValueError, match="conflicts with"):
        registry.register_sync(_command("help", aliases=("h", "H")))


@pytest.mark.asyncio
async def test_registry_async_register() -> None:
    registry = CommandRegistry()
    await registry.register(_command("help", aliases=("h",)))
    assert registry.find("H") is not None


def test_complete_uses_names_aliases_and_excludes_hidden() -> None:
    registry = CommandRegistry()
    registry.register_sync(_command("help", aliases=("h", "?")))
    registry.register_sync(_command("hello"))
    registry.register_sync(_command("hidden", hidden=True))

    assert complete(registry, "/h") == ["/h", "/hello", "/help"]
    assert complete(registry, "  /?") == ["/?"]
    assert "/hidden" not in complete(registry, "/")
    assert complete(registry, "/missing") == []


def test_register_all_commands_registers_exact_requested_set() -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    expected = {
        "help",
        "compact",
        "clear",
        "plan",
        "do",
        "session",
        "memory",
        "permission",
        "status",
        "review",
        "tasks",
        "task",
        "trace",
    }
    assert len(ALL_COMMANDS) == 13
    assert {command.name for command in registry.list_commands()} == expected
    assert registry.find("h").name == "help"  # type: ignore[union-attr]
    assert registry.find("mode").name == "permission"  # type: ignore[union-attr]
    assert registry.find("r").name == "review"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_help_lists_commands_and_shows_detail() -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    ui = FakeUI()
    command = registry.find("help")
    assert command is not None

    await command.handler(_context(registry, ui=ui))
    assert "可用命令" in ui.messages[-1]
    assert "/review" in ui.messages[-1]

    await command.handler(_context(registry, args="review", ui=ui))
    assert "/review" in ui.messages[-1]
    assert "prompt" in ui.messages[-1]


@pytest.mark.asyncio
async def test_plan_and_do_change_ui_mode_before_sending_optional_prompt() -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    ui = FakeUI()

    plan = registry.find("plan")
    do = registry.find("do")
    assert plan is not None and do is not None
    await plan.handler(_context(registry, args="设计登录流程", ui=ui))
    await do.handler(_context(registry, args="实现登录流程", ui=ui))

    assert ui.plan_modes == [True, False]
    assert ui.prompts == ["设计登录流程", "实现登录流程"]
    assert "Plan on" in ui.messages[0]
    assert "Accept Edits on" in ui.messages[1]


@pytest.mark.asyncio
async def test_review_sends_structured_prompt_to_agent_flow() -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    ui = FakeUI()
    review = registry.find("review")
    assert review is not None

    await review.handler(_context(registry, args="并发安全", ui=ui))

    assert REVIEW_PROMPT in ui.prompts[0]
    assert "逻辑错误" in ui.prompts[0]
    assert "安全问题" in ui.prompts[0]
    assert "性能问题" in ui.prompts[0]
    assert "代码风格" in ui.prompts[0]
    assert "额外关注：并发安全" in ui.prompts[0]


@pytest.mark.asyncio
async def test_unknown_help_target_has_consistent_guidance() -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    ui = FakeUI()
    help_command = registry.find("help")
    assert help_command is not None
    await help_command.handler(_context(registry, args="missing", ui=ui))
    assert "未知命令" in ui.messages[-1]
    assert "/help" in ui.messages[-1]


@pytest.mark.asyncio
async def test_permission_command_manages_mode_and_local_rules(tmp_path: Path) -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    ui = FakeUI()
    engine = RuleEngine(local_rules_path=tmp_path / "permissions.local.yaml")
    agent = SimpleNamespace(
        permission_mode=PermissionMode.ACCEPT_EDITS,
        permission_checker=SimpleNamespace(rule_engine=engine),
    )
    command = registry.find("permission")
    assert command is not None

    def set_mode(mode: PermissionMode) -> None:
        agent.permission_mode = mode

    ctx = _context(
        registry,
        ui=ui,
        permission_checker=agent.permission_checker,
        set_permission_mode=set_mode,
    )
    ctx.agent = agent

    ctx.args = "mode yolo"
    await command.handler(ctx)
    assert agent.permission_mode is PermissionMode.BYPASS

    ctx.args = "add allow Bash(git *)"
    await command.handler(ctx)
    assert engine.rules[0].pattern == "git *"

    ctx.args = "rules"
    await command.handler(ctx)
    assert "allow Bash(git *)" in ui.messages[-1]

    ctx.args = "reset"
    await command.handler(ctx)
    assert engine.rules == []


@pytest.mark.asyncio
async def test_clear_command_uses_injected_ui_independent_reset() -> None:
    registry = CommandRegistry()
    register_all_commands(registry)
    ui = FakeUI()
    called = False

    async def clear_conversation() -> None:
        nonlocal called
        called = True

    clear = registry.find("clear")
    assert clear is not None
    await clear.handler(
        _context(registry, ui=ui, clear_conversation=clear_conversation)
    )
    assert called
    assert ui.messages[-1] == "当前对话已清空。"
