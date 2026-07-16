"""Helpers for optional file-cache integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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
