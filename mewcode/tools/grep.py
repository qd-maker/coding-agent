"""Regex-based source search tool."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from mewcode.tools.base import SKIP_DIRS, Tool, ToolResult


class Params(BaseModel):
    pattern: str = Field(description="Python regular expression")
    path: str = Field(default=".", description="Directory or file to search")
    include: str = Field(default="", description="Optional basename glob, such as *.py")


class Grep(Tool):
    name = "Grep"
    description = "Search UTF-8 text files with a regular expression."
    params_model: ClassVar[type[BaseModel]] = Params
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: Params) -> ToolResult:
        try:
            regex = re.compile(params.pattern)
        except re.error as exc:
            return ToolResult(f"Error: invalid regular expression: {exc}", is_error=True)

        base = Path(params.path).expanduser()
        if not base.exists():
            return ToolResult(f"Error: path not found: {base}", is_error=True)
        root = base if base.is_dir() else base.parent
        candidates = [base] if base.is_file() else base.rglob("*")
        matches: list[str] = []
        try:
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(root)
                if any(part in SKIP_DIRS for part in relative.parts):
                    continue
                if params.include and not fnmatch.fnmatch(candidate.name, params.include):
                    continue
                try:
                    lines = candidate.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeError):
                    continue
                for line_number, line in enumerate(lines, start=1):
                    if regex.search(line):
                        matches.append(f"{relative.as_posix()}:{line_number}:{line}")
        except OSError as exc:
            return ToolResult(f"Error: search failed: {exc}", is_error=True)
        return ToolResult("\n".join(matches) if matches else "No matches found.")


__all__ = ["Grep", "Params"]
