"""Bash-compatible shell command tool."""

from __future__ import annotations

import asyncio
import locale
import os
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from simplecode.permissions.dangerous import is_safe_command
from simplecode.tools.base import Tool, ToolResult

MAX_TIMEOUT = 600


def _decode_output(data: bytes) -> str:
    """Decode shell output using UTF-8 or the host's native console encoding."""

    encodings = ["utf-8", locale.getpreferredencoding(False), "gb18030"]
    if os.name == "nt":
        encodings.append("mbcs")
    tried: set[str] = set()
    for encoding in encodings:
        normalized = encoding.casefold()
        if normalized in tried:
            continue
        tried.add(normalized)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


class Params(BaseModel):
    command: str = Field(min_length=1, description="Shell command to execute")
    timeout: float = Field(default=120, gt=0, description="Timeout in seconds")


class Bash(Tool):
    name = "Bash"
    description = "Execute a shell command and return stdout, stderr, and exit status."
    params_model: ClassVar[type[BaseModel]] = Params
    category = "command"
    is_destructive = True
    execution_timeout = MAX_TIMEOUT + 5.0

    def concurrency_safe_for(self, arguments: dict[str, Any]) -> bool:
        command = arguments.get("command")
        return isinstance(command, str) and is_safe_command(command)

    def __init__(self, work_dir: str | Path | None = None) -> None:
        self.work_dir = Path(work_dir).resolve() if work_dir is not None else None

    def set_work_dir(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir).resolve()

    async def execute(self, params: Params) -> ToolResult:
        timeout = min(params.timeout, MAX_TIMEOUT)
        process = await asyncio.create_subprocess_shell(
            params.command,
            # An agent has no way to feed interactive input, so detach stdin.
            # Without this, a command that reads stdin (a stdio MCP server, an
            # interactive REPL/prompt, a pager) inherits the terminal and blocks
            # until the timeout. DEVNULL delivers immediate EOF instead.
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(self.work_dir) if self.work_dir is not None else None,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            process.kill()
            await process.communicate()
            return ToolResult(
                f"Error: command timed out after {timeout:g}s",
                is_error=True,
                exit_code=-1,
            )
        except asyncio.CancelledError:
            process.kill()
            await process.communicate()
            raise

        stdout = _decode_output(stdout_bytes).rstrip()
        stderr = _decode_output(stderr_bytes).rstrip()
        if not stdout and not stderr:
            output = "(no output)"
        else:
            sections: list[str] = []
            if stdout:
                sections.append(f"STDOUT:\n{stdout}")
            if stderr:
                sections.append(f"STDERR:\n{stderr}")
            output = "\n\n".join(sections)
        returncode = int(process.returncode or 0)
        if returncode != 0:
            output = f"{output}\n\nExit code: {returncode}"
        return ToolResult(
            output,
            is_error=returncode != 0,
            data={"command": params.command, "exit_code": returncode},
            preview=output[:2000],
            exit_code=returncode,
        )


__all__ = ["Bash", "MAX_TIMEOUT", "Params", "_decode_output"]
