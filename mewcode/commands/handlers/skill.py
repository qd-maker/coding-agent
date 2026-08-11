"""Management command for the active Skill catalog."""

from __future__ import annotations

from mewcode.commands.registry import Command, CommandContext, CommandRegistry, CommandType
from mewcode.skills import SkillDependencyError, SkillExecutor, SkillLoader
from mewcode.skills.executor import validate_skill_dependencies
from mewcode.tools import ToolRegistry

SKILL_USAGE = "用法：/skill [list | info <name> | reload]"


async def handle_skill(ctx: CommandContext) -> None:
    loader = ctx.config.get("skill_loader")
    executor = ctx.config.get("skill_executor")
    registry = ctx.config.get("registry")
    tool_registry = ctx.config.get("tool_registry")
    if not isinstance(loader, SkillLoader) or not isinstance(executor, SkillExecutor):
        await ctx.ui.add_system_message("Skill 系统未初始化。")
        return

    parts = (ctx.args or "list").strip().split(maxsplit=1)
    subcommand = parts[0].casefold() if parts else "list"
    value = parts[1].strip().casefold() if len(parts) > 1 else ""

    if subcommand in {"", "list"}:
        lines = ["已加载 Skills："]
        for name, description in loader.get_catalog():
            item = loader.skills[name]
            lines.append(
                f"  /{name} · {item.mode} · {loader.get_source_label(name)} — {description}"
            )
        message = "\n".join(lines) if loader.get_catalog() else "当前没有可用 Skill。"
    elif subcommand == "info" and value:
        selected = loader.get(value)
        if selected is None:
            message = f"找不到 Skill：{value}"
        else:
            allowed = ", ".join(selected.allowed_tools) or "all"
            message = (
                f"Skill: {selected.name}\nDescription: {selected.description}\n"
                f"Mode: {selected.mode}\nContext: {selected.context}\n"
                f"Model: {selected.model}\nAllowedTools: {allowed}\n"
                f"Source: {loader.get_source_label(selected.name)}\n"
                f"Path: {selected.source_path}\nDirectory: {selected.is_directory}\n\n"
                f"{selected.prompt_body}"
            )
    elif subcommand == "reload":
        if not isinstance(registry, CommandRegistry) or not isinstance(tool_registry, ToolRegistry):
            message = "Skill 重载依赖未初始化。"
        else:
            skills = loader.load_all()
            try:
                validate_skill_dependencies(skills.values(), tool_registry)
            except SkillDependencyError as exc:
                message = f"Skill 重载失败：{exc}"
            else:
                from mewcode.commands.handlers.skill_register import register_skill_commands

                ctx.agent.set_skill_catalog(loader.build_catalog_prompt())
                register_skill_commands(registry, loader, executor)
                message = f"Skill catalog 已重载：{len(skills)} 个。"
    else:
        message = SKILL_USAGE
    await ctx.ui.add_system_message(message)
    ctx.ui.refresh_status()


SKILL_COMMAND = Command(
    name="skill",
    aliases=("skills",),
    description="列出、查看或热重载 Skill",
    usage=SKILL_USAGE,
    arg_prompt="list | info <name> | reload",
    type=CommandType.LOCAL_UI,
    handler=handle_skill,
)

__all__ = ["SKILL_COMMAND", "SKILL_USAGE", "handle_skill"]
