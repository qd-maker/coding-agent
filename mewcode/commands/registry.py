"""Core types and registry for slash commands."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class CommandType(StrEnum):
    """Execution category advertised by a slash command."""

    LOCAL = "local"
    LOCAL_UI = "local_ui"
    PROMPT = "prompt"


class UIController(Protocol):
    """Minimal UI surface available to command handlers."""

    async def add_system_message(self, text: str) -> None: ...

    async def send_user_message(self, text: str) -> None: ...

    def set_plan_mode(self, enabled: bool) -> None: ...

    def get_token_count(self) -> int: ...

    def refresh_status(self) -> None: ...


@dataclass(slots=True)
class CommandContext:
    """Runtime dependencies passed to every command handler."""

    args: str
    agent: Any
    conversation: Any
    session: Any
    session_manager: Any
    memory_manager: Any
    ui: UIController
    config: dict[str, Any] = field(default_factory=dict)


CommandHandler = Callable[[CommandContext], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class Command:
    """Declarative slash-command metadata."""

    name: str
    description: str
    type: CommandType
    handler: CommandHandler
    aliases: tuple[str, ...] = ()
    usage: str = ""
    arg_prompt: str = ""
    hidden: bool = False


def _normalize_name(value: str) -> str:
    return value.strip().removeprefix("/").casefold()


class CommandRegistry:
    """Centralized, alias-aware command registry."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._alias_map: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def register(self, command: Command) -> None:
        """Register a command while serializing concurrent writers."""

        async with self._lock:
            self.register_sync(command)

    def register_sync(self, command: Command) -> None:
        """Register a command and reject every name/alias collision."""

        name = _normalize_name(command.name)
        if not name:
            raise ValueError("Command name cannot be empty")

        aliases = tuple(_normalize_name(alias) for alias in command.aliases)
        if any(not alias for alias in aliases):
            raise ValueError("Command alias cannot be empty")

        candidates = (name, *aliases)
        if len(set(candidates)) != len(candidates):
            raise ValueError(f"Command {name!r} conflicts with one of its aliases")

        for candidate in candidates:
            owner = self._owner(candidate)
            if owner is not None:
                raise ValueError(f"Command name or alias {candidate!r} conflicts with {owner!r}")

        normalized = Command(
            name=name,
            description=command.description,
            type=command.type,
            handler=command.handler,
            aliases=aliases,
            usage=command.usage,
            arg_prompt=command.arg_prompt,
            hidden=command.hidden,
        )
        self._commands[name] = normalized
        for alias in aliases:
            self._alias_map[alias] = name

    def _owner(self, name: str) -> str | None:
        if name in self._commands:
            return name
        return self._alias_map.get(name)

    def find(self, name: str) -> Command | None:
        """Find a command by canonical name or alias, case-insensitively."""

        normalized = _normalize_name(name)
        canonical = self._alias_map.get(normalized, normalized)
        return self._commands.get(canonical)

    def unregister(self, name: str) -> Command | None:
        """Remove a canonical command (or its alias) and all alias mappings."""

        normalized = _normalize_name(name)
        canonical = self._alias_map.get(normalized, normalized)
        command = self._commands.pop(canonical, None)
        if command is None:
            return None
        for alias in command.aliases:
            self._alias_map.pop(alias, None)
        return command

    def list_commands(self, *, include_hidden: bool = False) -> list[Command]:
        """Return commands ordered by canonical name."""

        commands: Iterable[Command] = self._commands.values()
        if not include_hidden:
            commands = (command for command in commands if not command.hidden)
        return sorted(commands, key=lambda command: command.name)


__all__ = [
    "Command",
    "CommandContext",
    "CommandHandler",
    "CommandRegistry",
    "CommandType",
    "UIController",
]
