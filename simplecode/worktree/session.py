"""Crash-safe worktree-session persistence and fast HEAD recovery."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from simplecode.worktree.models import WorktreeSession

SESSION_FILENAME = "worktree_session.json"


def _read_ref(common_dir: Path, ref_name: str) -> str | None:
    loose = common_dir / Path(ref_name)
    try:
        value = loose.read_text(encoding="utf-8").strip()
    except OSError:
        value = ""
    if value:
        return value.split()[0]
    try:
        lines = (common_dir / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line or line.startswith(("#", "^")):
            continue
        sha, _, packed_ref = line.partition(" ")
        if packed_ref == ref_name:
            return sha
    return None


def read_worktree_head(worktree_path: str | Path) -> tuple[str, str] | None:
    """Read ``(sha, branch)`` without spawning Git; return ``None`` if invalid."""

    root = Path(worktree_path).resolve()
    dot_git = root / ".git"
    if dot_git.is_dir():
        git_dir = dot_git.resolve()
    else:
        try:
            marker = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        prefix = "gitdir:"
        if not marker.casefold().startswith(prefix):
            return None
        raw_git_dir = marker[len(prefix) :].strip()
        git_dir = Path(raw_git_dir)
        if not git_dir.is_absolute():
            git_dir = (root / git_dir).resolve()
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head:
        return None
    if not head.startswith("ref: "):
        return (head.split()[0], "")
    ref_name = head[5:].strip()
    try:
        common_raw = (git_dir / "commondir").read_text(encoding="utf-8").strip()
        common_dir = Path(common_raw)
        if not common_dir.is_absolute():
            common_dir = (git_dir / common_dir).resolve()
    except OSError:
        common_dir = git_dir
    sha = _read_ref(common_dir, ref_name)
    if not sha:
        return None
    return (sha, ref_name.removeprefix("refs/heads/"))


def read_worktree_head_sha(worktree_path: str | Path) -> str | None:
    result = read_worktree_head(worktree_path)
    return result[0] if result is not None else None


def save_session(path: str | Path, session: WorktreeSession | None) -> None:
    """Atomically persist a session; ``None`` removes the state file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if session is None:
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_text("{}", encoding="utf-8")
        os.replace(temporary, target)
        return
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    temporary.write_text(
        json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary, target)


def load_session(path: str | Path) -> WorktreeSession | None:
    target = Path(path)
    try:
        raw: Any = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        return WorktreeSession.from_dict(raw)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _session_path(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / ".simplecode" / SESSION_FILENAME


def save_worktree_session(
    repo_root: str | Path,
    session: WorktreeSession | None,
) -> None:
    save_session(_session_path(repo_root), session)


def load_worktree_session(repo_root: str | Path) -> WorktreeSession | None:
    return load_session(_session_path(repo_root))


__all__ = [
    "load_session",
    "load_worktree_session",
    "read_worktree_head",
    "read_worktree_head_sha",
    "save_session",
    "save_worktree_session",
]
