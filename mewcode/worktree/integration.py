"""Agent tools and prompt helpers for worktree transitions."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from mewcode.tools.base import Tool, ToolResult
from mewcode.worktree.manager import WorktreeError, WorktreeManager
from mewcode.worktree.slug import generate_worktree_name

WORKTREE_NOTICE_TEMPLATE = """[WORKTREE CONTEXT]
You are running in an isolated Git Worktree at: {wt_path}
The parent checkout is: {parent_cwd}
Translate paths from the parent checkout to your local worktree path. Treat every relative path
as relative to the isolated worktree, never edit the parent checkout, and always re-read files
before editing.
[/WORKTREE CONTEXT]"""


def build_worktree_notice(parent_cwd: str, wt_path: str | None = None) -> str:
    if wt_path is None:
        wt_path = parent_cwd
        parent_cwd = "(parent checkout)"
    return WORKTREE_NOTICE_TEMPLATE.format(parent_cwd=parent_cwd, wt_path=wt_path)


class EnterWorktreeParams(BaseModel):
    name: str | None = Field(default=None, description="Safe worktree name; generated if omitted")


class EnterWorktreeTool(Tool):
    name = "EnterWorktree"
    description = "Create and enter an isolated Git worktree for subsequent tool calls."
    params_model = EnterWorktreeParams
    category = "command"
    should_defer = True
    execution_timeout = None

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    async def execute(self, params: EnterWorktreeParams) -> ToolResult:
        if self.manager.get_current_session() is not None:
            return ToolResult("Already in a worktree session", is_error=True)
        name = params.name or generate_worktree_name()
        try:
            worktree = await self.manager.create(name, "HEAD")
            await self.manager.enter(worktree.name)
        except WorktreeError as exc:
            return ToolResult(f"Error: {exc}", is_error=True)
        return ToolResult(
            f"Entered worktree {worktree.name!r} at {worktree.path}. "
            "All relative file and shell operations now use this directory."
        )


class ExitWorktreeParams(BaseModel):
    action: Literal["keep", "remove"] = Field(
        default="keep",
        description="Keep the worktree or remove it with its branch",
    )
    discard_changes: bool = Field(default=False, description="Allow discarding protected changes")


class ExitWorktreeTool(Tool):
    name = "ExitWorktree"
    description = "Return to the main checkout, optionally removing the isolated worktree."
    params_model = ExitWorktreeParams
    category = "command"
    should_defer = True
    execution_timeout = None

    def __init__(self, manager: WorktreeManager) -> None:
        self.manager = manager

    async def execute(self, params: ExitWorktreeParams) -> ToolResult:
        try:
            worktree = await self.manager.exit(
                remove=params.action == "remove",
                discard=params.discard_changes,
            )
        except WorktreeError as exc:
            return ToolResult(f"Error: {exc}", is_error=True)
        suffix = " and removed it" if params.action == "remove" else ""
        return ToolResult(
            f"Exited worktree {worktree.name!r}{suffix}; returned to the main checkout."
        )


__all__ = [
    "EnterWorktreeParams",
    "EnterWorktreeTool",
    "ExitWorktreeParams",
    "ExitWorktreeTool",
    "WORKTREE_NOTICE_TEMPLATE",
    "build_worktree_notice",
]
