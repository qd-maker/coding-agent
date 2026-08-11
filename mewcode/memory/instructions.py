"""Project instruction discovery with sandboxed ``@include`` expansion."""

from __future__ import annotations

from pathlib import Path

MAX_INCLUDE_DEPTH = 5
INCLUDE_PREFIX = "@include "

_BLOCKED_INCLUDE = "<!-- @include blocked: path outside project -->"
_MISSING_INCLUDE = "<!-- @include skipped: file not found -->"


def _include_target(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith(INCLUDE_PREFIX):
        return None
    target = stripped[len(INCLUDE_PREFIX) :].strip()
    if len(target) >= 2 and (
        (target.startswith("<") and target.endswith(">"))
        or (target.startswith('"') and target.endswith('"'))
        or (target.startswith("'") and target.endswith("'"))
    ):
        target = target[1:-1].strip()
    return target or None


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def process_includes(
    content: str,
    base_dir: str | Path,
    project_root: str | Path,
    depth: int = 0,
) -> str:
    """Expand include-only lines while preventing traversal outside the project."""
    if depth >= MAX_INCLUDE_DEPTH:
        return content

    base = Path(base_dir).resolve()
    root = Path(project_root).resolve()
    output: list[str] = []
    for line in content.splitlines():
        target = _include_target(line)
        if target is None:
            output.append(line)
            continue

        try:
            include_path = (base / target).resolve()
        except OSError:
            output.append(_MISSING_INCLUDE)
            continue
        if not _inside_root(include_path, root):
            output.append(_BLOCKED_INCLUDE)
            continue
        if not include_path.is_file():
            output.append(_MISSING_INCLUDE)
            continue
        try:
            included = include_path.read_text(encoding="utf-8")
        except OSError:
            output.append(_MISSING_INCLUDE)
            continue
        output.append(
            process_includes(
                included,
                include_path.parent,
                root,
                depth + 1,
            )
        )
    return "\n".join(output)


def load_instructions(project_root: str | Path) -> str:
    """Load project-to-user instruction layers in descending priority order."""
    root = Path(project_root).resolve()
    candidates = (
        root / "MEWCODE.md",
        root / ".mewcode" / "MEWCODE.md",
        Path.home() / ".mewcode" / "MEWCODE.md",
    )
    sections: list[str] = []
    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError:
            continue
        expanded = process_includes(content, resolved.parent, root).strip()
        if expanded:
            sections.append(expanded)
    return "\n---\n".join(sections)


__all__ = [
    "INCLUDE_PREFIX",
    "MAX_INCLUDE_DEPTH",
    "load_instructions",
    "process_includes",
]
