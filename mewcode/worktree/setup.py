"""Best-effort environment initialization for newly created worktrees."""

from __future__ import annotations

import fnmatch
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from mewcode.worktree.changes import run_git
from mewcode.worktree.models import SetupReport

DEFAULT_LOCAL_FILES = (
    ".env",
    ".env.local",
    ".mewcode/settings.local.json",
    ".claude/settings.local.json",
)
LOCAL_CONFIG_FILES = DEFAULT_LOCAL_FILES


def _copy_path(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _load_include_patterns(repo_root: Path) -> list[str]:
    source = repo_root / ".worktreeinclude"
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [
        line.strip().replace("\\", "/")
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    ]


def _copy_ignored_includes(repo_root: Path, worktree_path: Path) -> list[str]:
    copied: list[str] = []
    patterns = _load_include_patterns(repo_root)
    if not patterns:
        return copied
    ignored = run_git(
        repo_root,
        "ls-files",
        "--others",
        "--ignored",
        "--exclude-standard",
        "--directory",
    )
    if ignored.returncode != 0:
        return copied
    for raw in ignored.stdout.splitlines():
        relative_text = raw.strip().rstrip("/").replace("\\", "/")
        if not relative_text or not any(
            fnmatch.fnmatch(relative_text, pattern) or fnmatch.fnmatch(f"{relative_text}/", pattern)
            for pattern in patterns
        ):
            continue
        relative = Path(relative_text)
        if relative.is_absolute() or ".." in relative.parts:
            continue
        normalized = relative.as_posix().casefold()
        if normalized == ".git" or normalized.startswith(".mewcode/worktrees"):
            continue
        source = repo_root / relative
        if not source.exists():
            continue
        _copy_path(source, worktree_path / relative)
        copied.append(relative.as_posix())
    return copied


def perform_post_creation_setup(
    repo_root: str | Path,
    worktree_path: str | Path,
    *,
    symlink_directories: Iterable[str] = (),
    local_files: Iterable[str] = DEFAULT_LOCAL_FILES,
) -> SetupReport:
    """Initialize local-only files, hooks and dependency links without failing creation."""

    root = Path(repo_root).resolve()
    target = Path(worktree_path).resolve()
    copied: list[str] = []
    linked: list[str] = []
    warnings: list[str] = []

    for item in local_files:
        relative = Path(item)
        if relative.is_absolute() or ".." in relative.parts:
            warnings.append(f"skipped unsafe local file path: {item}")
            continue
        source = root / relative
        if not source.exists():
            continue
        try:
            _copy_path(source, target / relative)
            copied.append(relative.as_posix())
        except OSError as exc:
            warnings.append(f"cannot copy {relative.as_posix()}: {exc}")

    hooks_path = next(
        (candidate for candidate in (root / ".husky", root / ".git-hooks") if candidate.is_dir()),
        None,
    )
    if hooks_path is not None:
        result = run_git(target, "config", "core.hooksPath", str(hooks_path))
        if result.returncode != 0:
            warnings.append(result.stderr.strip() or "cannot configure core.hooksPath")

    for item in symlink_directories:
        relative = Path(item)
        if relative.is_absolute() or ".." in relative.parts:
            warnings.append(f"skipped unsafe symlink path: {item}")
            continue
        source = root / relative
        destination = target / relative
        if not source.exists() or destination.exists() or destination.is_symlink():
            continue
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(source, destination, target_is_directory=source.is_dir())
            linked.append(relative.as_posix())
        except OSError as exc:
            warnings.append(f"cannot link {relative.as_posix()}: {exc}")

    try:
        copied.extend(_copy_ignored_includes(root, target))
    except OSError as exc:
        warnings.append(f"cannot copy .worktreeinclude files: {exc}")
    return SetupReport(tuple(copied), tuple(linked), tuple(warnings))


__all__ = ["DEFAULT_LOCAL_FILES", "LOCAL_CONFIG_FILES", "perform_post_creation_setup"]
