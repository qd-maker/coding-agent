"""Glob file discovery tool."""

from __future__ import annotations

from glob import has_magic
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, Field

from mewcode.tools.base import SKIP_DIRS, Tool, ToolResult


class Params(BaseModel):
    pattern: str = Field(min_length=1, description="Glob pattern, such as **/*.py")
    path: str = Field(default=".", description="Directory to search from")


class Glob(Tool):
    name = "Glob"
    description = (
        "Find files by glob pattern and return sorted relative paths. A bare exact filename "
        "automatically falls back to a recursive project search when it is not in the start "
        "directory."
    )
    params_model: ClassVar[type[BaseModel]] = Params
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: Params) -> ToolResult:
        base = Path(params.path).expanduser()
        if not base.exists():
            return ToolResult(f"Error: path not found: {base}", is_error=True)
        if not base.is_dir():
            return ToolResult(f"Error: path is not a directory: {base}", is_error=True)
        try:
            candidates = list(base.glob(params.pattern))
            normalized_pattern = params.pattern.replace("\\", "/")
            is_exact_basename = "/" not in normalized_pattern and not has_magic(
                normalized_pattern
            )
            if not candidates and is_exact_basename:
                candidates = list(base.rglob(params.pattern))
            matches = []
            for candidate in candidates:
                relative = candidate.relative_to(base)
                if candidate.is_file() and not any(part in SKIP_DIRS for part in relative.parts):
                    matches.append(relative.as_posix())
        except (OSError, ValueError) as exc:
            return ToolResult(f"Error: glob failed: {exc}", is_error=True)
        matches.sort(key=str.casefold)
        return ToolResult("\n".join(matches) if matches else "No files matched the pattern.")


__all__ = ["Glob", "Params"]
