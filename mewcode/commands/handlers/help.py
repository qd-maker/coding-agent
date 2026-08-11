"""Help command generated from registry metadata."""

from __future__ import annotations

from mewcode.commands.registry import Command, CommandContext, CommandRegistry, CommandType


async def handle_help(ctx: CommandContext) -> None:
    registry = ctx.config.get("registry")
    if not isinstance(registry, CommandRegistry):
        await ctx.ui.add_system_message("命令注册中心未初始化。")
        return

    query = ctx.args.strip().removeprefix("/")
    if query:
        command = registry.find(query)
        if command is None or command.hidden:
            await ctx.ui.add_system_message(f"未知命令：/{query}。输入 /help 查看可用命令。")
            return
        aliases = ", ".join(f"/{alias}" for alias in command.aliases) or "无"
        usage = command.usage or f"/{command.name}"
        await ctx.ui.add_system_message(
            f"/{command.name} — {command.description}\n"
            f"用法：{usage}\n别名：{aliases}\n类型：{command.type.value}"
        )
        return

    lines = ["可用命令："]
    lines.extend(
        f"  /{command.name:<10} {command.description}" for command in registry.list_commands()
    )
    lines.append("输入 /help <命令> 查看详细用法；输入 / 后按 Tab 补全。")
    await ctx.ui.add_system_message("\n".join(lines))


HELP_COMMAND = Command(
    name="help",
    aliases=("h", "?"),
    description="列出命令或查看详细帮助",
    usage="/help [命令]",
    arg_prompt="命令名",
    type=CommandType.LOCAL,
    handler=handle_help,
)

__all__ = ["HELP_COMMAND", "handle_help"]
