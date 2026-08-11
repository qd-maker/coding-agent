"""Display one consolidated runtime status report."""

from __future__ import annotations

from pathlib import Path

from mewcode import __version__
from mewcode.commands.handlers.permission import permission_mode_label
from mewcode.commands.registry import Command, CommandContext, CommandType


async def handle_status(ctx: CommandContext) -> None:
    mode = permission_mode_label(ctx.agent.permission_mode)
    session_id = getattr(ctx.session, "id", "未启用")
    registry = getattr(ctx.agent, "registry", None)
    tools = (
        len(registry.list_tools())
        if registry is not None and hasattr(registry, "list_tools")
        else 0
    )
    memory = ctx.memory_manager.get_display_text() if ctx.memory_manager is not None else ""
    memory_state = "已加载" if memory and "没有任何" not in str(memory) else "空"
    work_dir = Path(ctx.config.get("work_dir", Path.cwd())).resolve()
    version = str(ctx.config.get("version", __version__))
    lines = [
        "MewCode 状态",
        f"模式：{mode}",
        f"会话：{session_id}",
        f"Token：约 {ctx.ui.get_token_count():,}",
        f"工具：{tools}",
        f"记忆：{memory_state}",
        f"工作目录：{work_dir}",
        f"版本：{version}",
    ]
    team_name = getattr(ctx.agent, "team_name", "")
    if team_name:
        team_role = "纯调度" if ctx.agent.coordinator_mode else "Lead"
        lines.insert(2, f"团队：{team_name}（{team_role}）")
    mcp_status = ctx.config.get("mcp_status")
    if callable(mcp_status):
        details = [str(item) for item in mcp_status()]
        if details:
            lines.insert(-1, "MCP：" + "；".join(details))
    await ctx.ui.add_system_message("\n".join(lines))
    ctx.ui.refresh_status()


STATUS_COMMAND = Command(
    name="status",
    aliases=("s",),
    description="显示模式、会话、Token 与运行状态",
    usage="/status",
    type=CommandType.LOCAL,
    handler=handle_status,
)

__all__ = ["STATUS_COMMAND", "handle_status"]
