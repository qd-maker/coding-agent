"""Leave Plan Mode and return to normal execution."""

from __future__ import annotations

from mewcode.commands.registry import Command, CommandContext, CommandType


async def handle_do(ctx: CommandContext) -> None:
    ctx.ui.set_plan_mode(False)
    await ctx.ui.add_system_message("Accept Edits on (shift+tab to cycle)")
    ctx.ui.refresh_status()
    if ctx.args:
        await ctx.ui.send_user_message(ctx.args)


DO_COMMAND = Command(
    name="do",
    description="进入 Accept Edits 执行模式",
    usage="/do [任务描述]",
    arg_prompt="可选任务描述",
    type=CommandType.LOCAL_UI,
    handler=handle_do,
)

__all__ = ["DO_COMMAND", "handle_do"]
