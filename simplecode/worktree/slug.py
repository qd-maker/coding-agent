"""Safe worktree and branch-name handling."""

from __future__ import annotations

import re
import secrets

MAX_SLUG_LENGTH = 64
MAX_WORKTREE_NAME_LENGTH = MAX_SLUG_LENGTH
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InvalidWorktreeName(ValueError):
    """Raised when an LLM/user supplied worktree name is unsafe."""


def validate_worktree_name(name: str) -> str:
    """Validate a portable relative name while allowing nested ``/`` segments."""

    value = name.strip().replace("\\", "/")
    if not value or len(value) > MAX_WORKTREE_NAME_LENGTH:
        raise InvalidWorktreeName(
            f"worktree name must contain 1-{MAX_WORKTREE_NAME_LENGTH} characters"
        )
    if value.startswith("/") or value.endswith("/") or "//" in value:
        raise InvalidWorktreeName("worktree name must be a normalized relative path")
    segments = value.split("/")
    for segment in segments:
        if segment in {".", ".."} or not _SEGMENT.fullmatch(segment):
            raise InvalidWorktreeName(
                "worktree name segments may contain only ASCII letters, digits, '.', '_' and '-'"
            )
        if segment.endswith(".") or segment.endswith(" "):
            raise InvalidWorktreeName("worktree name is not portable across operating systems")
    return value


def flatten_worktree_name(name: str) -> str:
    """Map a validated nested directory name to one safe branch suffix."""

    return validate_worktree_name(name).replace("/", "+")


# Public names used by the CH14 API contract.
validate_slug = validate_worktree_name
flatten_slug = flatten_worktree_name


def branch_for_worktree(name: str) -> str:
    return f"worktree-{flatten_worktree_name(name)}"


def generate_worktree_name() -> str:
    """Return a name recognized as ephemeral by the cleanup policy."""

    return f"agent-{secrets.token_hex(4)}"


__all__ = [
    "InvalidWorktreeName",
    "MAX_WORKTREE_NAME_LENGTH",
    "MAX_SLUG_LENGTH",
    "branch_for_worktree",
    "flatten_worktree_name",
    "flatten_slug",
    "generate_worktree_name",
    "validate_worktree_name",
    "validate_slug",
]
