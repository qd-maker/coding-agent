"""Clear the active conversation and visible feed."""

from __future__ import annotations

import inspect

from simplecode.commands.registry import Command, CommandContext, CommandType


async def handle_clear(ctx: CommandContext) -> None:
    clear_skills = getattr(ctx.agent, "clear_active_skills", None)
    if callable(clear_skills):
        clear_skills()
    clear_conversation = ctx.config.get("clear_conversation")
    if callable(clear_conversation):
        result = clear_conversation()
        if inspect.isawaitable(result):
            await result
    else:
        ctx.conversation.replace_history([])
        ctx.agent.conversation = ctx.conversation
        ctx.agent._loop_count = 0
    await ctx.ui.add_system_message("当前对话已清空。")
    ctx.ui.refresh_status()


CLEAR_COMMAND = Command(
    name="clear",
    aliases=("cls",),
    description="清空当前对话并重置上下文",
    usage="/clear",
    type=CommandType.LOCAL_UI,
    handler=handle_clear,
)

__all__ = ["CLEAR_COMMAND", "handle_clear"]
