"""Slash-command framework used by the Textual application."""

from mewcode.commands.parser import complete, parse_command
from mewcode.commands.registry import (
    Command,
    CommandContext,
    CommandRegistry,
    CommandType,
    UIController,
)

__all__ = [
    "Command",
    "CommandContext",
    "CommandRegistry",
    "CommandType",
    "UIController",
    "complete",
    "parse_command",
]
