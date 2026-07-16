"""Built-in command handlers."""

from mewcode.commands.handlers.do import handle_do
from mewcode.commands.handlers.permission import handle_permission_mode, permission_mode_label
from mewcode.commands.handlers.plan import handle_plan

__all__ = ["handle_do", "handle_permission_mode", "handle_plan", "permission_mode_label"]
