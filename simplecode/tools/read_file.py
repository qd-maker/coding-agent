"""ReadFile tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from simplecode.tools._file_support import cache_get, cache_set, resolve_tool_path
from simplecode.tools.base import Tool, ToolResult


class Params(BaseModel):
    file_path: str = Field(description="Path to the text file to read")
    offset: int = Field(default=0, ge=0, description="Zero-based first line index")
    limit: int = Field(default=2000, ge=1, description="Maximum lines to return")


class ReadFile(Tool):
    name = "ReadFile"
    description = "Read a UTF-8 text file and return numbered lines."
    params_model: ClassVar[type[BaseModel]] = Params
    category = "read"
    is_concurrency_safe = True

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
            content = cache_get(self.file_cache, path)
            if content is None:
                content = path.read_text(encoding="utf-8")
                cache_set(self.file_cache, path, content)
        except (OSError, UnicodeError) as exc:
            return ToolResult(f"Error: could not read {path}: {exc}", is_error=True)

        lines = content.splitlines()
        selected = lines[params.offset : params.offset + params.limit]
        output = "\n".join(
            f"{index + params.offset + 1}\t{line}" for index, line in enumerate(selected)
        )
        if output:
            return ToolResult(output)
        return ToolResult(
            f"No lines available from offset {params.offset}; file has {len(lines)} lines."
        )


__all__ = ["Params", "ReadFile"]
