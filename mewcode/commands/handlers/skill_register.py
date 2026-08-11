"""Register loaded Skills as dynamic Slash Commands."""

from __future__ import annotations

import asyncio
import logging
from weakref import WeakKeyDictionary

from mewcode.commands.registry import (
    Command,
    CommandContext,
    CommandHandler,
    CommandRegistry,
    CommandType,
)
from mewcode.skills import SkillExecutor, SkillLoader

log = logging.getLogger(__name__)
_REGISTERED_SKILL_NAMES: WeakKeyDictionary[CommandRegistry, set[str]] = WeakKeyDictionary()


async def _run_fork(
    ctx: CommandContext,
    executor: SkillExecutor,
    skill_name: str,
) -> None:
    skill = ctx.config["skill_loader"].get(skill_name)
    if skill is None:
        await ctx.ui.add_system_message(f"Skill '{skill_name}' disappeared during execution.")
        return
    try:
        result = await executor.execute_fork(skill, ctx.args, ctx.conversation)
    except Exception as exc:  # noqa: BLE001 - background Skill failure must reach the UI
        result = f"Skill failed: {type(exc).__name__}: {exc}"
    await ctx.ui.add_system_message(f"[{skill_name} skill result]\n{result or '(no output)'}")


def _make_skill_handler(
    loader: SkillLoader,
    executor: SkillExecutor,
    skill_name: str,
) -> CommandHandler:
    async def handle(ctx: CommandContext) -> None:
        skill = loader.get(skill_name)
        if skill is None:
            await ctx.ui.add_system_message(f"找不到 Skill：{skill_name}")
            return
        if skill.mode == "inline":
            executor.execute_inline(skill, ctx.args)
            trigger = ctx.args or f"Execute the activated Skill '{skill.name}' now."
            await ctx.ui.send_user_message(trigger)
            return

        task = asyncio.create_task(_run_fork(ctx, executor, skill_name))
        background_tasks = ctx.config.get("background_tasks")
        if isinstance(background_tasks, set):
            background_tasks.add(task)
            task.add_done_callback(background_tasks.discard)
        await ctx.ui.add_system_message(f"Skill '{skill_name}' 正在隔离上下文中执行…")

    return handle


def register_skill_commands(
    registry: CommandRegistry,
    loader: SkillLoader,
    executor: SkillExecutor,
) -> int:
    """Replace this registry's prior dynamic Skill commands and register current catalog."""

    for name in _REGISTERED_SKILL_NAMES.get(registry, set()):
        registry.unregister(name)

    registered: set[str] = set()
    for skill in loader.skills.values():
        existing = registry.find(skill.name)
        if existing is not None:
            if skill.name == "review" and existing.name == "review":
                registry.unregister("review")
            else:
                log.warning(
                    "Skipping Skill command '/%s': conflicts with command '/%s'",
                    skill.name,
                    existing.name,
                )
                continue
        command = Command(
            name=skill.name,
            description=f"{skill.description} [skill]",
            usage=f"/{skill.name} [arguments]",
            arg_prompt="Skill arguments",
            type=CommandType.PROMPT,
            handler=_make_skill_handler(loader, executor, skill.name),
        )
        registry.register_sync(command)
        registered.add(skill.name)
    _REGISTERED_SKILL_NAMES[registry] = registered
    return len(registered)


__all__ = ["_REGISTERED_SKILL_NAMES", "register_skill_commands"]
