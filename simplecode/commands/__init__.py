"""Slash-command framework used by the Textual application."""

from simplecode.commands.parser import complete, parse_command
from simplecode.commands.registry import (
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
