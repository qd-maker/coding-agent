"""Safe Git worktree isolation public API."""

from simplecode.worktree.changes import (
    Changes,
    count_worktree_changes,
    has_worktree_changes,
)
from simplecode.worktree.cleanup import (
    EPHEMERAL_NAME,
    EPHEMERAL_PATTERNS,
    cleanup_stale_worktrees,
    start_stale_cleanup_task,
)
from simplecode.worktree.integration import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    build_worktree_notice,
)
from simplecode.worktree.manager import (
    WorktreeError,
    WorktreeHasChangesError,
    WorktreeInUseError,
    WorktreeManager,
)
from simplecode.worktree.models import (
    CleanupResult,
    SetupReport,
    StaleCleanupResult,
    Worktree,
    WorktreeChanges,
    WorktreeSession,
)
from simplecode.worktree.session import load_worktree_session, save_worktree_session
from simplecode.worktree.slug import (
    InvalidWorktreeName,
    branch_for_worktree,
    flatten_slug,
    flatten_worktree_name,
    generate_worktree_name,
    validate_slug,
    validate_worktree_name,
)

__all__ = [
    "CleanupResult",
    "Changes",
    "EPHEMERAL_NAME",
    "EPHEMERAL_PATTERNS",
    "EnterWorktreeTool",
    "ExitWorktreeTool",
    "InvalidWorktreeName",
    "SetupReport",
    "StaleCleanupResult",
    "Worktree",
    "WorktreeChanges",
    "WorktreeError",
    "WorktreeHasChangesError",
    "WorktreeInUseError",
    "WorktreeManager",
    "WorktreeSession",
    "branch_for_worktree",
    "build_worktree_notice",
    "cleanup_stale_worktrees",
    "count_worktree_changes",
    "flatten_worktree_name",
    "flatten_slug",
    "generate_worktree_name",
    "validate_worktree_name",
    "validate_slug",
    "has_worktree_changes",
    "load_worktree_session",
    "save_worktree_session",
    "start_stale_cleanup_task",
]
