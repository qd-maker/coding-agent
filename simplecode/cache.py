"""Small modification-aware text cache shared by file tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

CacheStatus = Literal["missing", "stale", "fresh"]


@dataclass(frozen=True, slots=True)
class _CacheEntry:
    modified_ns: int
    size: int
    content: str


class FileCache:
    """Caches UTF-8 file contents while detecting external changes."""

    def __init__(self) -> None:
        self._entries: dict[Path, _CacheEntry] = {}

    def status(self, path: str | Path) -> CacheStatus:
        """Report whether this path was read/written and still matches disk."""

        resolved = Path(path).resolve()
        entry = self._entries.get(resolved)
        if entry is None:
            return "missing"
        try:
            stat = resolved.stat()
        except OSError:
            return "missing"
        if (stat.st_mtime_ns, stat.st_size) != (entry.modified_ns, entry.size):
            return "stale"
        return "fresh"

    def get(self, path: str | Path) -> str | None:
        resolved = Path(path).resolve()
        entry = self._entries.get(resolved)
        if entry is None:
            return None
        try:
            stat = resolved.stat()
        except OSError:
            self._entries.pop(resolved, None)
            return None
        if (stat.st_mtime_ns, stat.st_size) != (entry.modified_ns, entry.size):
            self._entries.pop(resolved, None)
            return None
        return entry.content

    def set(self, path: str | Path, content: str) -> None:
        resolved = Path(path).resolve()
        try:
            stat = resolved.stat()
        except OSError:
            return
        self._entries[resolved] = _CacheEntry(stat.st_mtime_ns, stat.st_size, content)

    def invalidate(self, path: str | Path) -> None:
        self._entries.pop(Path(path).resolve(), None)

    def clear(self) -> None:
        """Drop every cached checkout-local entry."""

        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


__all__ = ["CacheStatus", "FileCache"]
