"""WriteFile tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from mewcode.tools._file_support import cache_invalidate
from mewcode.tools.base import Tool, ToolResult


class Params(BaseModel):
    file_path: str = Field(description="Path to create or overwrite")
    content: str = Field(description="Complete UTF-8 file content")


class WriteFile(Tool):
    name = "WriteFile"
    description = "Create or overwrite a UTF-8 text file, creating parent directories."
    params_model: ClassVar[type[BaseModel]] = Params
    category = "write"

    def __init__(self, file_cache: Any | None = None) -> None:
        self.file_cache = file_cache

    async def execute(self, params: Params) -> ToolResult:
        path = Path(params.file_path).expanduser()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
            cache_invalidate(self.file_cache, path)
        except (OSError, UnicodeError) as exc:
            return ToolResult(f"Error: could not write {path}: {exc}", is_error=True)
        return ToolResult(f"Successfully wrote to {path}")


__all__ = ["Params", "WriteFile"]
