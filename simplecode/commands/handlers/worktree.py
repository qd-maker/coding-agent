"""Local ``/worktree`` lifecycle command."""

from __future__ import annotations

import shlex

from simplecode.commands.registry import Command, CommandContext, CommandType
from simplecode.worktree import WorktreeError, WorktreeManager


def _manager(ctx: CommandContext) -> WorktreeManager:
    manager = ctx.config.get("worktree_manager")
    if not isinstance(manager, WorktreeManager):
        raise RuntimeError("WorktreeManager is not available")
    return manager


async def handle_worktree(ctx: CommandContext) -> None:
    try:
        parts = shlex.split(ctx.args, posix=False)
    except ValueError as exc:
        await ctx.ui.add_system_message(f"参数解析失败：{exc}")
        return
    if not parts:
        await ctx.ui.add_system_message(
            "用法：/worktree create <name> [base] | list | enter <name> | "
            "exit [--remove] [--discard] | status"
        )
        return
    manager = _manager(ctx)
    action = parts[0].casefold()
    try:
        if action == "create":
            if len(parts) not in {2, 3}:
                raise WorktreeError("用法：/worktree create <name> [base]")
            worktree = await manager.create(parts[1], parts[2] if len(parts) == 3 else "HEAD")
            await manager.enter(worktree.name)
            await ctx.ui.add_system_message(
                f"已创建并进入 worktree {worktree.name}：{worktree.path}\n分支：{worktree.branch}"
            )
        elif action == "list":
            current = manager.current_session.worktree.name if manager.current_session else ""
            worktrees = manager.list_worktrees()
            if not worktrees:
                await ctx.ui.add_system_message("当前没有已管理的 worktree。")
                return
            lines = [
                f"{'*' if item.name == current else '-'} {item.name} · {item.branch} · {item.path}"
                for item in worktrees
            ]
            await ctx.ui.add_system_message("Worktrees\n" + "\n".join(lines))
        elif action == "enter":
            if len(parts) != 2:
                raise WorktreeError("用法：/worktree enter <name>")
            worktree = await manager.enter(parts[1])
            await ctx.ui.add_system_message(f"已进入 {worktree.name}：{worktree.path}")
        elif action == "exit":
            flags = set(parts[1:])
            unknown = flags - {"--remove", "--discard"}
            if unknown:
                raise WorktreeError(f"未知参数：{' '.join(sorted(unknown))}")
            worktree = await manager.exit(
                remove="--remove" in flags,
                discard="--discard" in flags,
            )
            await ctx.ui.add_system_message(
                f"已退出 {worktree.name}。"
                + (" worktree 和分支已删除。" if "--remove" in flags else " 工作内容已保留。")
            )
        elif action == "status":
            session = manager.status()
            if session is None:
                await ctx.ui.add_system_message(f"当前位于主工作目录：{manager.repo_root}")
            else:
                await ctx.ui.add_system_message(
                    f"当前 worktree：{session.worktree.name}\n"
                    f"路径：{session.worktree.path}\n分支：{session.worktree.branch}\n"
                    f"主目录：{session.original_cwd}"
                )
        else:
            await ctx.ui.add_system_message(f"未知子命令：{parts[0]}")
    except WorktreeError as exc:
        await ctx.ui.add_system_message(f"Worktree 操作失败：{exc}")


def create_worktree_command(manager: WorktreeManager | None = None) -> Command:
    """Create command metadata; runtime manager comes from CommandContext."""

    del manager
    return Command(
        name="worktree",
        aliases=("wt",),
        description="创建、进入、退出和检查 Git worktree",
        usage=(
            "/worktree create <name> [base] | list | enter <name> | "
            "exit [--remove] [--discard] | status"
        ),
        arg_prompt="create | list | enter | exit | status",
        type=CommandType.LOCAL_UI,
        handler=handle_worktree,
    )


WORKTREE_COMMAND = create_worktree_command()

__all__ = ["WORKTREE_COMMAND", "create_worktree_command", "handle_worktree"]
