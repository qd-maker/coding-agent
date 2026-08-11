"""EditFile tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mewcode.tools._file_support import cache_invalidate, resolve_tool_path
from mewcode.tools.base import Tool, ToolResult


class Params(BaseModel):
    file_path: str = Field(description="Path to the UTF-8 text file to edit")
    old_string: str = Field(min_length=1, description="Exact text that must occur once")
    new_string: str = Field(description="Replacement text")


class EditFile(Tool):
    name = "EditFile"
    description = "Replace one uniquely matching text block in an existing file."
    params_model: ClassVar[type[BaseModel]] = Params
    category = "write"

    def __init__(
        self,
        file_cache: Any | None = None,
        work_dir: str | Path | None = None,
    ) -> None:
        self.file_cache = file_cache
        self.work_dir = Path(work_dir).resolve() if work_dir is not None else None

    def set_work_dir(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir).resolve()

    async def execute(self, params: Params) -> ToolResult:
        path = resolve_tool_path(self.work_dir, params.file_path)
        if not path.exists():
            return ToolResult(f"Error: file not found: {path}", is_error=True)
        if not path.is_file():
            return ToolResult(f"Error: path is not a file: {path}", is_error=True)
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            return ToolResult(f"Error: could not read {path}: {exc}", is_error=True)

        matches = content.count(params.old_string)
        if matches == 0:
            return ToolResult(
                f"Error: old_string not found in {path}",
                is_error=True,
            )
        if matches > 1:
            return ToolResult(
                f"Error: old_string found {matches} times, must be unique",
                is_error=True,
            )
        updated = content.replace(params.old_string, params.new_string, 1)
        try:
            path.write_text(updated, encoding="utf-8")
            cache_invalidate(self.file_cache, path)
        except (OSError, UnicodeError) as exc:
            return ToolResult(f"Error: could not write {path}: {exc}", is_error=True)
        return ToolResult(
            f"Successfully edited {path}",
            data={"file_path": str(path), "replacements": 1},
            preview=f"{len(params.old_string)} chars -> {len(params.new_string)} chars",
        )


__all__ = ["EditFile", "Params"]
