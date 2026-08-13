"""Data contracts for isolated Git worktrees."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal


@dataclass(slots=True)
class Worktree:
    """One managed Git worktree."""

    name: str
    path: Path
    branch: str
    based_on: str
    head_commit: str
    created: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def base(self) -> str:
        return self.based_on

    @property
    def created_at(self) -> datetime:
        return self.created

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["path"] = str(self.path)
        value["created"] = self.created.isoformat()
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> Worktree:
        created = value.get("created", value.get("created_at"))
        return cls(
            name=str(value["name"]),
            path=Path(str(value["path"])).resolve(),
            branch=str(value["branch"]),
            based_on=str(value.get("based_on", value.get("base", "HEAD"))),
            head_commit=str(value["head_commit"]),
            created=(datetime.fromisoformat(str(created)) if created else datetime.now(UTC)),
        )


@dataclass(slots=True)
class WorktreeSession:
    """Persisted information required to restore an entered worktree."""

    original_cwd: Path
    worktree_path: Path
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    hook_based: bool = False

    @property
    def worktree(self) -> Worktree:
        """Project the compatibility session fields into a Worktree record."""

        from simplecode.worktree.slug import branch_for_worktree

        return Worktree(
            self.worktree_name,
            self.worktree_path,
            branch_for_worktree(self.worktree_name),
            self.original_branch,
            self.original_head_commit,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_cwd": str(self.original_cwd),
            "worktree_path": str(self.worktree_path),
            "worktree_name": self.worktree_name,
            "original_branch": self.original_branch,
            "original_head_commit": self.original_head_commit,
            "session_id": self.session_id,
            "hook_based": self.hook_based,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> WorktreeSession:
        legacy_worktree = value.get("worktree")
        legacy = dict(legacy_worktree) if isinstance(legacy_worktree, dict) else {}
        return cls(
            original_cwd=Path(str(value["original_cwd"])).resolve(),
            worktree_path=Path(str(value.get("worktree_path", legacy.get("path", "")))).resolve(),
            worktree_name=str(value.get("worktree_name", legacy.get("name", ""))),
            original_branch=str(
                value.get(
                    "original_branch",
                    legacy.get("based_on", legacy.get("base", "HEAD")),
                )
            ),
            original_head_commit=str(
                value.get("original_head_commit", legacy.get("head_commit", ""))
            ),
            session_id=str(value.get("session_id", "")),
            hook_based=bool(value.get("hook_based", False)),
        )


@dataclass(frozen=True, slots=True)
class WorktreeChanges:
    """Dirty-state summary used by exit and cleanup protection."""

    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    uncommitted_files: int = 0
    commits_ahead: int = 0
    unpushed_commits: int = 0
    check_failed: bool = False
    error: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(
            self.check_failed
            or self.staged
            or self.unstaged
            or self.untracked
            or self.uncommitted_files
            or self.commits_ahead
            or self.unpushed_commits
        )


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """Result of an isolated Agent's automatic cleanup decision."""

    kept: bool
    path: Path | None = None
    branch: str | None = None


@dataclass(slots=True)
class StaleCleanupResult:
    """Diagnostics from one periodic cleanup pass."""

    removed: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SetupReport:
    copied: tuple[str, ...] = ()
    linked: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


WorktreeIsolation = Literal["worktree"]


__all__ = [
    "CleanupResult",
    "SetupReport",
    "StaleCleanupResult",
    "Worktree",
    "WorktreeChanges",
    "WorktreeIsolation",
    "WorktreeSession",
]
