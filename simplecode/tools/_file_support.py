"""Helpers for optional file-cache integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def resolve_tool_path(work_dir: Path | None, raw_path: str) -> Path:
    """Resolve a model-supplied relative path against this tool's isolated root."""

    path = Path(raw_path).expanduser()
    if path.is_absolute() or work_dir is None:
        return path
    return work_dir / path


def cache_get(cache: Any | None, path: Path) -> str | None:
    if cache is None:
        return None
    getter = getattr(cache, "get", None)
    if not callable(getter):
        return None
    value = getter(path)
    return value if isinstance(value, str) else None


def cache_set(cache: Any | None, path: Path, content: str) -> None:
    if cache is None:
        return
    setter = getattr(cache, "set", None)
    if callable(setter):
        setter(path, content)


def cache_invalidate(cache: Any | None, path: Path) -> None:
    if cache is None:
        return
    invalidate = getattr(cache, "invalidate", None)
    if callable(invalidate):
        invalidate(path)


__all__ = ["cache_get", "cache_invalidate", "cache_set", "resolve_tool_path"]
