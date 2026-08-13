"""EditFile tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from simplecode.tools._file_support import cache_get, cache_set, resolve_tool_path
from simplecode.tools.base import Tool, ToolResult


class Params(BaseModel):
    file_path: str = Field(description="Path to the UTF-8 text file to edit")
    old_string: str = Field(min_length=1, description="Exact text that must occur once")
    new_string: str = Field(description="Replacement text")


def _cache_status(cache: Any | None, path: Path) -> str | None:
    if cache is None:
        return None
    status = getattr(cache, "status", None)
    if not callable(status):
        return "fresh" if cache_get(cache, path) is not None else "missing"
    value = status(path)
    return value if isinstance(value, str) else "missing"


def _detect_newline(path: Path) -> str:
    try:
        return "\r\n" if b"\r\n" in path.read_bytes() else "\n"
    except OSError:
        return "\n"


def _write_preserving_newline(path: Path, content: str, newline: str) -> None:
    payload = content if newline == "\n" else content.replace("\n", newline)
    path.write_bytes(payload.encode("utf-8"))


class EditFile(Tool):
    name = "EditFile"
    description = (
        "Replace one uniquely matching text block in an existing file. "
        "Read the file with ReadFile first (or write it with WriteFile). "
        "If the file changed after that read, call ReadFile again."
    )
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

    def _freshness_error(self, path: Path) -> ToolResult | None:
        status = _cache_status(self.file_cache, path)
        if status is None:
            return None
        if status == "missing":
            return ToolResult(
                f"Error: file has not been read in this session: {path}. Call ReadFile first.",
                is_error=True,
            )
        if status == "stale":
            return ToolResult(
                f"Error: file changed since last read: {path}. Call ReadFile again.",
                is_error=True,
            )
        return None

    async def execute(self, params: Params) -> ToolResult:
        path = resolve_tool_path(self.work_dir, params.file_path)
        if not path.exists():
            return ToolResult(f"Error: file not found: {path}", is_error=True)
        if not path.is_file():
            return ToolResult(f"Error: path is not a file: {path}", is_error=True)
        blocked = self._freshness_error(path)
        if blocked is not None:
            return blocked
        try:
            content = cache_get(self.file_cache, path)
            if content is None:
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
        blocked = self._freshness_error(path)
        if blocked is not None:
            return blocked
        newline = _detect_newline(path)
        try:
            _write_preserving_newline(path, updated, newline)
            cache_set(self.file_cache, path, updated)
        except (OSError, UnicodeError) as exc:
            return ToolResult(f"Error: could not write {path}: {exc}", is_error=True)
        return ToolResult(
            f"Successfully edited {path}",
            data={"file_path": str(path), "replacements": 1},
            preview=f"{len(params.old_string)} chars -> {len(params.new_string)} chars",
        )


__all__ = ["EditFile", "Params"]
