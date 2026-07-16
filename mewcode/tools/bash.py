"""Bash-compatible shell command tool."""

from __future__ import annotations

import asyncio
import locale
import os
from typing import ClassVar

from pydantic import BaseModel, Field

from mewcode.tools.base import Tool, ToolResult

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
    execution_timeout = MAX_TIMEOUT + 5.0

    async def execute(self, params: Params) -> ToolResult:
        timeout = min(params.timeout, MAX_TIMEOUT)
        process = await asyncio.create_subprocess_shell(
            params.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
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
        return ToolResult(output, is_error=returncode != 0)


__all__ = ["Bash", "MAX_TIMEOUT", "Params", "_decode_output"]
