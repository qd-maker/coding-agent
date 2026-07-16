"""Minimal async lifecycle hook engine for Agent extensions."""

from __future__ import annotations

import inspect
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class HookContext:
    event: str
    agent_id: str
    tool_name: str | None = None
    file_path: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str | None = None


@dataclass(frozen=True, slots=True)
class HookResult:
    allowed: bool = True
    reason: str = ""
    notification: str = ""


HookCallback = Callable[[HookContext], HookResult | None | Awaitable[HookResult | None]]


class HookEngine:
    def __init__(self) -> None:
        self._callbacks: dict[str, list[HookCallback]] = defaultdict(list)
        self._notifications: list[tuple[str, str]] = []

    def register(self, event: str, callback: HookCallback) -> None:
        self._callbacks[event].append(callback)

    async def run_hooks(self, event: str, context: HookContext) -> HookResult:
        for callback in self._callbacks.get(event, []):
            value = callback(context)
            if inspect.isawaitable(value):
                value = await value
            if value is None:
                continue
            if value.notification:
                self._notifications.append((event, value.notification))
            if not value.allowed:
                return value
        return HookResult()

    async def run_pre_tool_hooks(self, context: HookContext) -> HookResult:
        return await self.run_hooks("pre_tool_use", context)

    def drain_notifications(self) -> list[tuple[str, str]]:
        notifications = list(self._notifications)
        self._notifications.clear()
        return notifications


__all__ = ["HookContext", "HookEngine", "HookResult"]
