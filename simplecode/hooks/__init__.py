"""Public API for declarative Simple Code lifecycle Hooks."""

from simplecode.hooks.conditions import (
    Condition,
    ConditionGroup,
    ConditionParseError,
    parse_condition,
)
from simplecode.hooks.engine import HookEngine
from simplecode.hooks.events import LifecycleEvent
from simplecode.hooks.executors import (
    execute_action,
    execute_agent,
    execute_command,
    execute_http,
    execute_prompt,
)
from simplecode.hooks.loader import HookConfigError, load_hooks
from simplecode.hooks.models import (
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
