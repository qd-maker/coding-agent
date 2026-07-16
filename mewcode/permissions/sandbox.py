"""Filesystem path sandbox with symlink-aware root checks."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path


class PathSandbox:
    def __init__(
        self,
        project_root: str | Path,
        extra_allowed: Iterable[str | Path] | None = None,
    ) -> None:
        self.project_root = Path(project_root).expanduser().resolve()
        roots = [self.project_root, Path(tempfile.gettempdir()).resolve()]
        roots.extend(Path(path).expanduser().resolve() for path in extra_allowed or ())
        self._allowed_roots = list(dict.fromkeys(roots))

    @staticmethod
    def _resolve_new_path(candidate: Path) -> Path:
        missing: list[str] = []
        existing = candidate
        while not existing.exists():
            if existing.parent == existing:
                raise FileNotFoundError(candidate)
            missing.append(existing.name)
            existing = existing.parent
        resolved = existing.resolve(strict=True)
        for part in reversed(missing):
            resolved /= part
        return resolved

    def check(self, path: str | Path) -> tuple[bool, str]:
        raw = Path(path).expanduser()
        candidate = raw if raw.is_absolute() else self.project_root / raw
        try:
            resolved = (
                candidate.resolve(strict=True)
                if candidate.exists()
                else self._resolve_new_path(candidate)
            )
        except (OSError, RuntimeError) as exc:
            return False, f"路径 {candidate} 无法解析: {exc}"

        for root in self._allowed_roots:
            try:
                resolved.relative_to(root)
                return True, ""
            except ValueError:
                continue
        return False, f"路径 {resolved} 超出沙箱范围"


__all__ = ["PathSandbox"]
