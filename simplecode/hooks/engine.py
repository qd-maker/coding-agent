"""Hook matching, execution, isolation, prompt collection and pre-tool rejection."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections import defaultdict

from simplecode.hooks.events import LifecycleEvent
from simplecode.hooks.executors import execute_action
from simplecode.hooks.models import (
    ActionResult,
    Hook,
    HookCallback,
    HookContext,
    HookNotification,
    ToolRejectedError,
)

logger = logging.getLogger(__name__)


class HookEngine:
    def __init__(self, hooks: list[Hook] | None = None) -> None:
        self.hooks = list(hooks or [])
        self._prompt_messages: list[str] = []
        self._notifications: list[HookNotification] = []
        self._background_tasks: set[asyncio.Task[ActionResult]] = set()
        self._callbacks: dict[str, list[HookCallback]] = defaultdict(list)

    def register(self, event: str, callback: HookCallback) -> None:
        """Retain the small programmatic callback API used by extension code."""

        self._callbacks[event].append(callback)

    def find_matching_hooks(self, event: str, context: HookContext) -> list[Hook]:
        event_name = str(event)
        return [
            hook for hook in self.hooks if hook.event == event_name and hook.should_run(context)
        ]

    async def run_hooks(self, event: str, context: HookContext) -> None:
        context.event_name = str(event)
        for hook in self.find_matching_hooks(str(event), context):
            hook.mark_executed()
            if hook.async_exec:
                task = asyncio.ensure_future(self._run_single(hook, context))
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)
            else:
                await self._run_single(hook, context)
        await self._run_callbacks(str(event), context)

    async def _run_single(self, hook: Hook, context: HookContext) -> ActionResult:
        try:
            result = await execute_action(hook.action, context)
        except Exception as exc:  # noqa: BLE001 - auxiliary automation must not break Agent
            logger.warning("Hook %s failed: %s", hook.id, exc, exc_info=True)
            result = ActionResult(f"Hook action failed: {exc}", success=False)
        if hook.action.type == "prompt" and result.success:
            self._prompt_messages.append(result.output)
        self._notifications.append(
            HookNotification(hook.id, context.event_name, result.output, result.success)
        )
        return result

    async def _run_callbacks(self, event: str, context: HookContext) -> ToolRejectedError | None:
        for index, callback in enumerate(self._callbacks.get(event, [])):
            try:
                value = callback(context)
                if inspect.isawaitable(value):
                    value = await value
                if value is None:
                    continue
                if value.notification:
                    self._notifications.append(
                        HookNotification(
                            event,
                            event,
                            value.notification,
                            value.allowed,
                        )
                    )
                if not value.allowed:
                    return ToolRejectedError(
                        context.tool_name,
                        value.reason,
                        f"callback_{event}_{index}",
                    )
            except Exception as exc:  # noqa: BLE001 - callback isolation matches YAML Hooks
                logger.warning("Hook callback for %s failed: %s", event, exc, exc_info=True)
                self._notifications.append(
                    HookNotification(
                        f"callback_{event}_{index}",
                        event,
                        f"Hook callback failed: {exc}",
                        False,
                    )
                )
        return None

    async def run_pre_tool_hooks(self, context: HookContext) -> ToolRejectedError | None:
        context.event_name = LifecycleEvent.PRE_TOOL_USE
        for hook in self.find_matching_hooks(LifecycleEvent.PRE_TOOL_USE, context):
            hook.mark_executed()
            result = await self._run_single(hook, context)
            if hook.reject and result.success:
                return ToolRejectedError(context.tool_name, result.output, hook.id)
        return await self._run_callbacks(LifecycleEvent.PRE_TOOL_USE, context)

    def get_prompt_messages(self) -> list[str]:
        messages = list(self._prompt_messages)
        self._prompt_messages.clear()
        return messages

    def drain_notifications(self) -> list[HookNotification]:
        notifications = list(self._notifications)
        self._notifications.clear()
        return notifications

    async def wait_background(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)


__all__ = ["HookEngine", "HookNotification"]
