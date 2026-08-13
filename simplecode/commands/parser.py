"""Parsing and prefix completion for slash commands."""

from __future__ import annotations

from simplecode.commands.registry import CommandRegistry


def parse_command(text: str) -> tuple[str, str, bool]:
    """Return ``(name, args, is_command)`` for one input line."""

    stripped = text.lstrip()
    if not stripped.startswith("/"):
        return "", "", False

    body = stripped[1:].strip()
    if not body:
        return "", "", True
    parts = body.split(maxsplit=1)
    name = parts[0].casefold()
    args = parts[1].strip() if len(parts) > 1 else ""
    return name, args, True


def complete(registry: CommandRegistry, prefix: str) -> list[str]:
    """Return visible canonical names and aliases matching a prefix."""

    normalized = prefix.lstrip().removeprefix("/").casefold()
    matches: set[str] = set()
    for command in registry.list_commands():
        for candidate in (command.name, *command.aliases):
            if candidate.startswith(normalized):
                matches.add(f"/{candidate}")
    return sorted(matches)


__all__ = ["complete", "parse_command"]
