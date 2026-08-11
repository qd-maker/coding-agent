"""Local `/session` archive management command."""

from __future__ import annotations

import inspect
from datetime import datetime
from typing import Any

from mewcode.agent import CompactNotification, ErrorEvent
from mewcode.commands.registry import Command, CommandContext, CommandType
from mewcode.context import estimate_conversation_tokens
from mewcode.conversation import ConversationManager
from mewcode.memory.session import ResumeResult, SessionMeta, build_time_gap_message

SESSION_USAGE = "用法：/session [list | resume <id|序号> | new | delete <id>]"


def _format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _format_sessions(metas: list[SessionMeta], *, heading: str) -> str:
    if not metas:
        return "当前没有可用的会话存档。"
    lines = [heading]
    for index, meta in enumerate(metas, 1):
        summary = f" · {meta.summary}" if meta.summary else ""
        lines.append(
            f"{index}. {meta.title} · {meta.message_count} 条消息 · "
            f"{_format_time(meta.last_active)}{summary}\n   {meta.id}"
        )
    return "\n".join(lines)


def _close_previous(ctx: CommandContext, *, remove_empty: bool) -> None:
    previous = ctx.session
    if previous is None:
        return
    previous_id = previous.id
    was_empty = previous.meta.message_count == 0
    previous.close()
    if remove_empty and was_empty:
        ctx.session_manager.delete(previous_id)


async def _invoke(callback: Any, *args: Any) -> None:
    if not callable(callback):
        return
    result = callback(*args)
    if inspect.isawaitable(result):
        await result


async def _install_session(
    ctx: CommandContext,
    session: Any,
    conversation: ConversationManager,
) -> None:
    set_conversation = ctx.config.get("set_conversation")
    set_session = ctx.config.get("set_session")
    if callable(set_conversation):
        set_conversation(conversation)
    else:
        ctx.agent.conversation = conversation
    if callable(set_session):
        set_session(session)
    ctx.agent._loop_count = 0
    reset_cursor = getattr(ctx.memory_manager, "reset_cursor", None)
    if callable(reset_cursor):
        reset_cursor(len(conversation.history))
    await _invoke(ctx.config.get("render_restored"))


async def _resume(ctx: CommandContext, token: str) -> str:
    candidates = ctx.config.setdefault("resume_candidates", [])
    session_id = token
    if token.isdigit():
        index = int(token) - 1
        if index < 0 or index >= len(candidates):
            return "会话序号无效，请先运行 /session resume 查看候选列表。"
        session_id = str(candidates[index])
    if session_id == getattr(ctx.session, "id", None):
        return "该会话已是当前会话。"

    result: ResumeResult | None = ctx.session_manager.resume(session_id)
    if result is None:
        return f"找不到会话：{session_id}"

    _close_previous(ctx, remove_empty=True)
    restored = ConversationManager()
    restored.replace_history(result.messages)
    if gap_message := build_time_gap_message(result.last_active):
        restored.history.append(gap_message)

    compact_message = ""
    if estimate_conversation_tokens(restored) >= int(ctx.agent.context_window):
        compacted = await ctx.agent.manual_compact(restored)
        if isinstance(compacted, CompactNotification):
            compact_message = (
                f"；已自动压缩 {compacted.before_tokens:,} → {compacted.after_tokens:,} tokens"
            )
        elif isinstance(compacted, ErrorEvent):
            compact_message = f"；自动压缩失败：{compacted.message}"

    await _install_session(ctx, result.session, restored)
    return f"已恢复会话：{session_id}（{len(restored.history)} 条消息）{compact_message}。"


async def handle_session(ctx: CommandContext) -> None:
    manager = ctx.session_manager
    if manager is None:
        await ctx.ui.add_system_message("会话管理器未初始化。")
        return
    is_busy = ctx.config.get("is_busy")
    if callable(is_busy) and is_busy():
        await ctx.ui.add_system_message("生成进行中，暂时不能管理会话。")
        return

    parts = (ctx.args or "list").strip().split(maxsplit=1)
    subcommand = (parts[0] if parts else "list").casefold()
    value = parts[1].strip() if len(parts) > 1 else ""

    if subcommand in {"", "list"}:
        message = _format_sessions(manager.list()[:10], heading="最近会话：")
    elif subcommand == "resume" and not value:
        candidates = manager.list()[:15]
        resume_candidates = ctx.config.setdefault("resume_candidates", [])
        resume_candidates[:] = [meta.id for meta in candidates]
        message = _format_sessions(
            candidates,
            heading="可恢复会话（可用 /session resume <序号>）：",
        )
    elif subcommand == "resume":
        message = await _resume(ctx, value)
    elif subcommand == "new":
        _close_previous(ctx, remove_empty=True)
        session = manager.create()
        conversation = ConversationManager()
        await _install_session(ctx, session, conversation)
        message = f"已创建新会话：{session.id}"
    elif subcommand == "delete" and not value:
        message = SESSION_USAGE
    elif subcommand == "delete" and value == getattr(ctx.session, "id", None):
        message = "不能删除当前会话，请先切换或创建新会话。"
    elif subcommand == "delete":
        message = "会话已删除。" if manager.delete(value) else f"找不到会话：{value}"
    else:
        message = SESSION_USAGE

    await ctx.ui.add_system_message(message)
    ctx.ui.refresh_status()


SESSION_COMMAND = Command(
    name="session",
    description="列出、恢复、新建或删除会话",
    usage=SESSION_USAGE,
    arg_prompt="list | resume <id|序号> | new | delete <id>",
    type=CommandType.LOCAL_UI,
    handler=handle_session,
)

__all__ = ["SESSION_COMMAND", "SESSION_USAGE", "handle_session"]
