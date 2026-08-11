"""Local `/memory` command."""

from __future__ import annotations

from mewcode.commands.registry import Command, CommandContext, CommandType

MEMORY_USAGE = "用法：/memory [list | clear | edit]"


async def handle_memory(ctx: CommandContext) -> None:
    manager = ctx.memory_manager
    if manager is None:
        await ctx.ui.add_system_message("记忆管理器未初始化。")
        return

    subcommand = (ctx.args or "list").strip().casefold()
    if subcommand in {"", "list"}:
        message = str(manager.get_display_text())
    elif subcommand == "clear":
        manager.clear()
        message = "所有自动记忆已清空。"
    elif subcommand == "edit":
        message = (
            f"请直接编辑以下文件：\n用户级：{manager.user_path}\n项目级：{manager.project_path}"
        )
    else:
        message = MEMORY_USAGE
    await ctx.ui.add_system_message(message)
    ctx.ui.refresh_status()


MEMORY_COMMAND = Command(
    name="memory",
    description="查看或管理自动记忆",
    usage=MEMORY_USAGE,
    arg_prompt="list | clear | edit",
    type=CommandType.LOCAL,
    handler=handle_memory,
)

__all__ = ["MEMORY_COMMAND", "MEMORY_USAGE", "handle_memory"]
