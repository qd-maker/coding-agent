"""Enter read-only Plan Mode."""

from __future__ import annotations

from simplecode.commands.registry import Command, CommandContext, CommandType


async def handle_plan(ctx: CommandContext) -> None:
    ctx.ui.set_plan_mode(True)
    await ctx.ui.add_system_message("Plan on (shift+tab to cycle)")
    ctx.ui.refresh_status()
    if ctx.args:
        await ctx.ui.send_user_message(ctx.args)


PLAN_COMMAND = Command(
    name="plan",
    aliases=("p",),
    description="进入只读计划模式",
    usage="/plan [任务描述]",
    arg_prompt="可选任务描述",
    type=CommandType.LOCAL_UI,
    handler=handle_plan,
)

__all__ = ["PLAN_COMMAND", "handle_plan"]
