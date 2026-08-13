"""Manual `/compact` slash command."""

from __future__ import annotations

from collections.abc import Awaitable

from simplecode.agent import CompactNotification, ErrorEvent
from simplecode.commands.registry import Command, CommandContext, CommandType
from simplecode.context import estimate_conversation_tokens


async def handle_compact(ctx: CommandContext) -> None:
    """Compress conversation history via Layer 2 summarization."""

    if not getattr(ctx.conversation, "history", None):
        await ctx.ui.add_system_message("当前对话为空，无需压缩。")
        return

    estimated = estimate_conversation_tokens(ctx.conversation)
    reported = int(getattr(ctx.conversation, "last_input_tokens", 0) or 0)
    ctx.conversation.last_input_tokens = max(estimated, reported)
    result = await ctx.agent.manual_compact(ctx.conversation)
    if isinstance(result, CompactNotification):
        message = f"已压缩上下文：约 {result.before_tokens:,} → {result.after_tokens:,} tokens。"
    elif isinstance(result, ErrorEvent):
        message = f"压缩失败：{result.message}"
    else:
        message = "压缩完成。"

    persist = ctx.config.get("persist")
    if callable(persist):
        maybe_awaitable = persist()
        if isinstance(maybe_awaitable, Awaitable):
            await maybe_awaitable
    await ctx.ui.add_system_message(message)
    ctx.ui.refresh_status()


COMPACT_COMMAND = Command(
    name="compact",
    aliases=("c",),
    description="压缩当前对话上下文",
    usage="/compact",
    type=CommandType.LOCAL,
    handler=handle_compact,
)

__all__ = ["COMPACT_COMMAND", "handle_compact"]
