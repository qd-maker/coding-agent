"""WriteFile tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from simplecode.tools._file_support import cache_set, resolve_tool_path
from simplecode.tools.base import Tool, ToolResult


class Params(BaseModel):
    file_path: str = Field(description="Path to create or overwrite")
    content: str = Field(description="Complete UTF-8 file content")


class WriteFile(Tool):
    name = "WriteFile"
    description = "Create or overwrite a UTF-8 text file, creating parent directories."
    params_model: ClassVar[type[BaseModel]] = Params
    category = "write"
    is_destructive = True

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
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(params.content, encoding="utf-8")
            cache_set(self.file_cache, path, params.content)
        except (OSError, UnicodeError) as exc:
            return ToolResult(f"Error: could not write {path}: {exc}", is_error=True)
        return ToolResult(
            f"Successfully wrote to {path}",
            data={"file_path": str(path), "bytes": len(params.content.encode("utf-8"))},
            preview=f"{len(params.content.splitlines())} lines",
        )


__all__ = ["Params", "WriteFile"]
