"""Built-in command handlers and one-shot registration."""

from mewcode.commands.handlers.clear import CLEAR_COMMAND, handle_clear
from mewcode.commands.handlers.compact import COMPACT_COMMAND, handle_compact
from mewcode.commands.handlers.do import DO_COMMAND, handle_do
from mewcode.commands.handlers.help import HELP_COMMAND, handle_help
from mewcode.commands.handlers.memory import MEMORY_COMMAND, handle_memory
from mewcode.commands.handlers.permission import (
    PERMISSION_COMMAND,
    handle_permission,
    handle_permission_mode,
    permission_mode_label,
)
from mewcode.commands.handlers.plan import PLAN_COMMAND, handle_plan
from mewcode.commands.handlers.review import REVIEW_COMMAND, handle_review
from mewcode.commands.handlers.session import SESSION_COMMAND, handle_session
from mewcode.commands.handlers.status import STATUS_COMMAND, handle_status
from mewcode.commands.handlers.tasks import (
    TASK_COMMAND,
    TASKS_COMMAND,
    TRACE_COMMAND,
    handle_task,
    handle_tasks,
    handle_trace,
)
from mewcode.commands.handlers.team import TEAM_COMMAND, handle_team
from mewcode.commands.handlers.worktree import (
    WORKTREE_COMMAND,
    create_worktree_command,
    handle_worktree,
)
from mewcode.commands.registry import Command, CommandRegistry

ALL_COMMANDS: tuple[Command, ...] = (
    HELP_COMMAND,
    COMPACT_COMMAND,
    CLEAR_COMMAND,
    PLAN_COMMAND,
    DO_COMMAND,
    SESSION_COMMAND,
    MEMORY_COMMAND,
    PERMISSION_COMMAND,
    STATUS_COMMAND,
    REVIEW_COMMAND,
    TASKS_COMMAND,
    TASK_COMMAND,
    TRACE_COMMAND,
)


def register_all_commands(registry: CommandRegistry) -> None:
    for command in ALL_COMMANDS:
        registry.register_sync(command)


__all__ = [
    "ALL_COMMANDS",
    "CLEAR_COMMAND",
    "COMPACT_COMMAND",
    "DO_COMMAND",
    "HELP_COMMAND",
    "MEMORY_COMMAND",
    "PERMISSION_COMMAND",
    "PLAN_COMMAND",
    "REVIEW_COMMAND",
    "SESSION_COMMAND",
    "STATUS_COMMAND",
    "TASKS_COMMAND",
    "TASK_COMMAND",
    "TRACE_COMMAND",
    "TEAM_COMMAND",
    "WORKTREE_COMMAND",
    "create_worktree_command",
    "handle_clear",
    "handle_compact",
    "handle_do",
    "handle_help",
    "handle_memory",
    "handle_permission",
    "handle_permission_mode",
    "handle_plan",
    "handle_review",
    "handle_session",
    "handle_status",
    "handle_task",
    "handle_tasks",
    "handle_trace",
    "handle_team",
    "handle_worktree",
    "permission_mode_label",
    "register_all_commands",
]
