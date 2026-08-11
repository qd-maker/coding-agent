"""Validation and parsing for declarative Hook configuration."""

from __future__ import annotations

from typing import Any

from mewcode.hooks.conditions import ConditionParseError, parse_condition
from mewcode.hooks.events import LifecycleEvent
from mewcode.hooks.models import Action, Hook

_VALID_ACTION_TYPES = {"command", "prompt", "http", "agent"}
_REQUIRED_FIELDS = {
    "command": "command",
    "prompt": "message",
    "http": "url",
    "agent": "prompt",
}


class HookConfigError(ValueError):
    pass


def _error(label: str, message: str) -> HookConfigError:
    return HookConfigError(f"{label}: {message}")


def _require_bool(raw: dict[str, Any], key: str, label: str) -> bool:
    value = raw.get(key, False)
    if not isinstance(value, bool):
        raise _error(label, f"{key!r} must be a boolean")
    return value


def load_hooks(raw_hooks: list[dict[str, Any]] | None) -> list[Hook]:
    if raw_hooks is None or raw_hooks == []:
        return []
    if not isinstance(raw_hooks, list):
        raise HookConfigError("hooks must be a YAML list")

    loaded: list[Hook] = []
    valid_events = {event.value for event in LifecycleEvent}
    for index, raw_hook in enumerate(raw_hooks):
        default_label = f"hook #{index + 1}"
        if not isinstance(raw_hook, dict):
            raise _error(default_label, "must be a YAML mapping")

        raw_id = raw_hook.get("id")
        if raw_id is not None and (not isinstance(raw_id, str) or not raw_id.strip()):
            raise _error(default_label, "id must be a non-empty string")
        label = f"hook {raw_id.strip()!r}" if isinstance(raw_id, str) else default_label

        event = raw_hook.get("event")
        if not isinstance(event, str) or event not in valid_events:
            raise _error(label, f"invalid event {event!r}")
        hook_id = raw_id.strip() if isinstance(raw_id, str) else f"{event}_{index}"
        label = f"hook {hook_id!r}"

        raw_action = raw_hook.get("action")
        if not isinstance(raw_action, dict):
            raise _error(label, "action must be a YAML mapping")
        action_type = raw_action.get("type")
        if not isinstance(action_type, str) or action_type not in _VALID_ACTION_TYPES:
            raise _error(label, f"invalid action type {action_type!r}")
        required = _REQUIRED_FIELDS[action_type]
        required_value = raw_action.get(required)
        if not isinstance(required_value, str) or not required_value.strip():
            raise _error(
                label,
                f"action type {action_type!r} requires {required!r} field",
            )

        timeout = raw_action.get("timeout", 30)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout <= 0:
            raise _error(label, "action.timeout must be a positive integer")
        headers = raw_action.get("headers", {})
        if not isinstance(headers, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
        ):
            raise _error(label, "action.headers must be a string mapping")
        method = raw_action.get("method", "POST")
        if not isinstance(method, str) or not method.strip():
            raise _error(label, "action.method must be a non-empty string")

        reject = _require_bool(raw_hook, "reject", label)
        once = _require_bool(raw_hook, "once", label)
        async_exec = _require_bool(raw_hook, "async", label)
        if reject and event != LifecycleEvent.PRE_TOOL_USE:
            raise _error(label, "reject is only valid for pre_tool_use")
        if async_exec and event == LifecycleEvent.PRE_TOOL_USE:
            raise _error(label, "async is not allowed for pre_tool_use")

        raw_condition = raw_hook.get("condition")
        if raw_condition is not None and not isinstance(raw_condition, str):
            raise _error(label, "condition must be a string")
        try:
            condition = parse_condition(raw_condition)
        except ConditionParseError as exc:
            raise _error(label, f"invalid condition: {exc}") from exc

        action = Action(
            type=action_type,
            command=str(raw_action.get("command", "")),
            message=str(raw_action.get("message", "")),
            url=str(raw_action.get("url", "")),
            method=method,
            body=raw_action.get("body"),
            headers=dict(headers),
            prompt=str(raw_action.get("prompt", "")),
            timeout=timeout,
        )
        loaded.append(
            Hook(
                id=hook_id,
                event=event,
                action=action,
                condition=condition,
                reject=reject,
                once=once,
                async_exec=async_exec,
            )
        )
    return loaded


__all__ = ["HookConfigError", "_REQUIRED_FIELDS", "_VALID_ACTION_TYPES", "load_hooks"]
