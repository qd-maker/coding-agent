"""Action executors used by HookEngine."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.request import Request, urlopen

from simplecode.hooks.models import Action, ActionResult, HookContext


async def execute_command(action: Action, context: HookContext) -> ActionResult:
    command = context.expand(action.command)
    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=action.timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            return ActionResult(
                f"Command timed out after {action.timeout}s: {command}",
                success=False,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
    except (OSError, ValueError) as exc:
        return ActionResult(f"Command could not start: {exc}", success=False)

    output = stdout.decode("utf-8", errors="replace").strip()
    success = process.returncode == 0
    if not output:
        output = (
            "Command completed" if success else f"Command exited with code {process.returncode}"
        )
    return ActionResult(output, success=success)


async def execute_prompt(action: Action, context: HookContext) -> ActionResult:
    return ActionResult(context.expand(action.message), success=True)


def _expand_value(value: Any, context: HookContext) -> Any:
    if isinstance(value, str):
        return context.expand(value)
    if isinstance(value, dict):
        return {str(key): _expand_value(item, context) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_value(item, context) for item in value]
    return value


async def execute_http(action: Action, context: HookContext) -> ActionResult:
    url = context.expand(action.url)
    headers = {context.expand(key): context.expand(value) for key, value in action.headers.items()}
    data: bytes | None = None
    if action.body is not None:
        expanded_body = _expand_value(action.body, context)
        if isinstance(expanded_body, str):
            data = expanded_body.encode("utf-8")
        else:
            data = json.dumps(expanded_body, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    request = Request(
        url,
        data=data,
        headers=headers,
        method=(action.method or "POST").upper(),
    )

    def do_request() -> tuple[int, str]:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - configured Hook URL
            status = int(getattr(response, "status", response.getcode()))
            content = response.read(500).decode("utf-8", errors="replace")
            return status, content

    try:
        loop = asyncio.get_running_loop()
        status, content = await loop.run_in_executor(None, do_request)
    except Exception as exc:  # noqa: BLE001 - Hook failures are isolated from the Agent
        return ActionResult(f"HTTP request failed: {exc}", success=False)
    return ActionResult(f"HTTP {status}: {content}", success=200 <= status < 400)


async def execute_agent(action: Action, context: HookContext) -> ActionResult:
    del action, context
    return ActionResult("agent executor not yet implemented", success=True)


Executor = Callable[[Action, HookContext], Awaitable[ActionResult]]

_EXECUTOR_MAP: dict[str, Executor] = {
    "command": execute_command,
    "prompt": execute_prompt,
    "http": execute_http,
    "agent": execute_agent,
}


async def execute_action(action: Action, context: HookContext) -> ActionResult:
    executor = _EXECUTOR_MAP.get(action.type)
    if executor is None:
        return ActionResult(f"Unknown action type: {action.type}", success=False)
    return await executor(action, context)


__all__ = [
    "_EXECUTOR_MAP",
    "execute_action",
    "execute_agent",
    "execute_command",
    "execute_http",
    "execute_prompt",
]
