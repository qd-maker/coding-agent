"""Public API for declarative MewCode lifecycle Hooks."""

from mewcode.hooks.conditions import (
    Condition,
    ConditionGroup,
    ConditionParseError,
    parse_condition,
)
from mewcode.hooks.engine import HookEngine
from mewcode.hooks.events import LifecycleEvent
from mewcode.hooks.executors import (
    execute_action,
    execute_agent,
    execute_command,
    execute_http,
    execute_prompt,
)
from mewcode.hooks.loader import HookConfigError, load_hooks
from mewcode.hooks.models import (
    Action,
    ActionResult,
    Hook,
    HookContext,
    HookNotification,
    HookResult,
    ToolRejectedError,
)

__all__ = [
    "Action",
    "ActionResult",
    "Condition",
    "ConditionGroup",
    "ConditionParseError",
    "Hook",
    "HookConfigError",
    "HookContext",
    "HookEngine",
    "HookNotification",
    "HookResult",
    "LifecycleEvent",
    "ToolRejectedError",
    "execute_action",
    "execute_agent",
    "execute_command",
    "execute_http",
    "execute_prompt",
    "load_hooks",
    "parse_condition",
]
