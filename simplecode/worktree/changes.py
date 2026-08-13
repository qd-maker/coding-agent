"""Fail-closed Git worktree change detection."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from simplecode.worktree.models import CleanupResult, WorktreeChanges

GIT_ENV = {"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}


@dataclass(frozen=True, slots=True)
class Changes:
    uncommitted: int = 0
    new_commits: int = 0


def run_git(
    cwd: str | Path,
    *args: str,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    """Run Git without prompting and always return a completed-process object."""

    command = ["git", *args]
    try:
        return subprocess.run(
            command,
            cwd=Path(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            env={**os.environ, **GIT_ENV},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, -1, "", str(exc))


def _count_lines(output: str) -> int:
    return sum(1 for line in output.splitlines() if line.strip())


def inspect_worktree_changes(
    path: str | Path,
    base_commit: str | None = None,
) -> WorktreeChanges:
    """Return a conservative dirty-state summary; Git errors count as changes."""

    root = Path(path)
    status = run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        return WorktreeChanges(
            check_failed=True,
            error=status.stderr.strip() or "git status failed",
        )
    staged = unstaged = untracked = 0
    uncommitted_files = 0
    for line in status.stdout.splitlines():
        if not line:
            continue
        uncommitted_files += 1
        if line.startswith("??"):
            untracked += 1
            continue
        if len(line) >= 2:
            staged += int(line[0] not in {" ", "?"})
            unstaged += int(line[1] not in {" ", "?"})

    ahead = 0
    if base_commit:
        result = run_git(root, "rev-list", "--count", f"{base_commit}..HEAD")
        if result.returncode != 0:
            return WorktreeChanges(
                staged=staged,
                unstaged=unstaged,
                untracked=untracked,
                uncommitted_files=uncommitted_files,
                check_failed=True,
                error=result.stderr.strip() or "cannot count commits ahead",
            )
        try:
            ahead = int(result.stdout.strip() or "0")
        except ValueError:
            return WorktreeChanges(check_failed=True, error="invalid git rev-list output")

    unpushed_result = run_git(root, "rev-list", "HEAD", "--not", "--remotes")
    if unpushed_result.returncode != 0:
        return WorktreeChanges(
            staged=staged,
            unstaged=unstaged,
            untracked=untracked,
            uncommitted_files=uncommitted_files,
            commits_ahead=ahead,
            check_failed=True,
            error=unpushed_result.stderr.strip() or "cannot check unpushed commits",
        )
    unpushed = _count_lines(unpushed_result.stdout)
    if base_commit:
        # Existing commits from the parent checkout are not worktree-created work.
        unpushed = min(unpushed, ahead)
    return WorktreeChanges(
        staged=staged,
        unstaged=unstaged,
        untracked=untracked,
        uncommitted_files=uncommitted_files,
        commits_ahead=ahead,
        unpushed_commits=unpushed,
    )


def has_worktree_changes(path: str | Path, base_commit: str | None = None) -> bool:
    return inspect_worktree_changes(path, base_commit).has_changes


def count_worktree_changes(path: str | Path, head_commit: str) -> Changes:
    """Compatibility API returning file and new-commit counts, fail closed."""

    detailed = inspect_worktree_changes(path, head_commit)
    if detailed.check_failed:
        return Changes(1, 1)
    return Changes(
        detailed.uncommitted_files,
        detailed.commits_ahead,
    )


def has_unpushed_commits(path: str | Path) -> bool:
    changes = inspect_worktree_changes(path)
    return changes.check_failed or changes.unpushed_commits > 0


__all__ = [
    "Changes",
    "CleanupResult",
    "GIT_ENV",
    "count_worktree_changes",
    "has_unpushed_commits",
    "has_worktree_changes",
    "inspect_worktree_changes",
    "run_git",
]
