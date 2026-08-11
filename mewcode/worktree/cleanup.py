"""Conservative periodic cleanup for old ephemeral worktrees."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from mewcode.worktree.changes import has_unpushed_commits, inspect_worktree_changes
from mewcode.worktree.models import StaleCleanupResult, Worktree
from mewcode.worktree.session import read_worktree_head
from mewcode.worktree.slug import branch_for_worktree

if TYPE_CHECKING:
    from mewcode.worktree.manager import WorktreeManager

EPHEMERAL_NAME = re.compile(r"^agent-[0-9a-f]{8}$")
EPHEMERAL_PATTERNS = (
    EPHEMERAL_NAME,
    re.compile(r"^wf_[0-9a-f]{8}-[0-9a-f]{3}-\d+$"),
    re.compile(r"^wf-\d+$"),
    re.compile(r"^bridge-[A-Za-z0-9_]+(?:-[A-Za-z0-9_]+)*$"),
    re.compile(r"^job-[A-Za-z0-9._-]{1,55}-[0-9a-f]{8}$"),
)
log = logging.getLogger(__name__)


def _is_ephemeral(name: str) -> bool:
    return any(pattern.fullmatch(name) for pattern in EPHEMERAL_PATTERNS)


async def cleanup_stale_worktrees(
    manager: WorktreeManager,
    *,
    cutoff: timedelta = timedelta(hours=24),
    now: datetime | None = None,
) -> StaleCleanupResult:
    """Delete only old, inactive, clean, fully pushed ephemeral worktrees."""

    result = StaleCleanupResult()
    current = getattr(manager, "current_session", None)
    root = Path(manager.worktree_dir)
    clock = now or datetime.now(UTC)
    if not root.exists():
        return result
    for candidate in root.iterdir():
        name = candidate.name
        if not candidate.is_dir() or not _is_ephemeral(name):
            continue
        if current is not None and current.worktree_path.resolve() == candidate.resolve():
            result.skipped[name] = "currently active"
            continue
        modified = datetime.fromtimestamp(candidate.stat().st_mtime, tz=UTC)
        if clock - modified < cutoff:
            result.skipped[name] = "not expired"
            continue
        recovered = read_worktree_head(candidate)
        if recovered is None:
            result.skipped[name] = "invalid worktree metadata (fail closed)"
            continue
        head, branch = recovered
        changes = inspect_worktree_changes(candidate, head)
        if changes.has_changes or has_unpushed_commits(candidate):
            result.skipped[name] = changes.error or "dirty or unpushed"
            continue
        worktree = Worktree(
            name, candidate.resolve(), branch or branch_for_worktree(name), "HEAD", head, modified
        )
        try:
            await manager.remove(worktree.name)
            result.removed.append(name)
        except Exception as exc:  # noqa: BLE001 - one stale directory must not block others
            result.errors[name] = str(exc)
    return result


async def start_stale_cleanup_task(
    manager: WorktreeManager,
    interval: float = 3600.0,
    cutoff_hours: float = 24.0,
) -> None:
    """Run cleanup periodically; lifecycle failures never stop the application."""

    while True:
        await asyncio.sleep(interval)
        try:
            await cleanup_stale_worktrees(
                manager,
                cutoff=timedelta(hours=cutoff_hours),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - auxiliary loop is isolated
            log.warning("stale worktree cleanup failed: %s", exc)


__all__ = [
    "EPHEMERAL_NAME",
    "EPHEMERAL_PATTERNS",
    "cleanup_stale_worktrees",
    "start_stale_cleanup_task",
]
