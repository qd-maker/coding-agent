"""Switch or cycle the three user-facing permission modes."""

from __future__ import annotations

from typing import Protocol

from mewcode.permissions import PermissionMode

_MODE_ORDER = (
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
)

_MODE_ALIASES = {
    "acceptedits": PermissionMode.ACCEPT_EDITS,
    "accept-edits": PermissionMode.ACCEPT_EDITS,
    "accept edits": PermissionMode.ACCEPT_EDITS,
    "do": PermissionMode.ACCEPT_EDITS,
    "plan": PermissionMode.PLAN,
    "yolo": PermissionMode.BYPASS,
    "bypass": PermissionMode.BYPASS,
    "bypasspermissions": PermissionMode.BYPASS,
}

_MODE_LABELS = {
    PermissionMode.ACCEPT_EDITS: "Accept Edits",
    PermissionMode.PLAN: "Plan",
    PermissionMode.BYPASS: "YOLO",
}


class PermissionModeTarget(Protocol):
    @property
    def permission_mode(self) -> PermissionMode: ...

    def set_permission_mode(self, mode: PermissionMode) -> None: ...


def parse_permission_mode(value: str) -> PermissionMode:
    normalized = value.strip().casefold()
    mode = _MODE_ALIASES.get(normalized)
    if mode is not None:
        return mode
    raise ValueError(f"Unknown permission mode {value!r}; choose: acceptEdits, plan, or yolo")


def cycle_permission_mode(current: PermissionMode) -> PermissionMode:
    if current not in _MODE_ORDER:
        return PermissionMode.ACCEPT_EDITS
    index = _MODE_ORDER.index(current)
    return _MODE_ORDER[(index + 1) % len(_MODE_ORDER)]


def permission_mode_label(mode: PermissionMode) -> str:
    return _MODE_LABELS.get(mode, "Accept Edits")


def handle_permission_mode(
    target: PermissionModeTarget,
    requested: str | None = None,
) -> str:
    mode = (
        parse_permission_mode(requested)
        if requested
        else cycle_permission_mode(target.permission_mode)
    )
    target.set_permission_mode(mode)
    return f"{permission_mode_label(mode)} on (shift+tab to cycle)"


__all__ = [
    "PermissionModeTarget",
    "cycle_permission_mode",
    "handle_permission_mode",
    "parse_permission_mode",
    "permission_mode_label",
]
