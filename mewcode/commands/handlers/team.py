"""Local `/team` inspection and lifecycle command."""

from __future__ import annotations

import shlex

from mewcode.commands.registry import Command, CommandContext, CommandType
from mewcode.teams import TeamManager


def _manager(ctx: CommandContext) -> TeamManager:
    manager = ctx.config.get("team_manager")
    if not isinstance(manager, TeamManager):
        raise RuntimeError("TeamManager is not available")
    return manager


async def _run_tool(ctx: CommandContext, name: str, arguments: dict[str, object]) -> None:
    registry = ctx.config.get("tool_registry")
    if registry is None:
        await ctx.ui.add_system_message("团队工具注册表不可用。")
        return
    result = await registry.execute(name, arguments, truncate=False)
    await ctx.ui.add_system_message(result.output)


async def handle_team(ctx: CommandContext) -> None:
    try:
        parts = shlex.split(ctx.args, posix=False)
    except ValueError as exc:
        await ctx.ui.add_system_message(f"参数解析失败：{exc}")
        return
    if not parts:
        await ctx.ui.add_system_message(
            "用法：/team create <name> | list | status [name] | tasks [name] | "
            "merge [name] | stop <member> | delete [name] [--discard]"
        )
        return
    manager = _manager(ctx)
    action = parts[0].casefold()
    if action == "create" and len(parts) >= 2:
        await _run_tool(ctx, "TeamCreate", {"name": parts[1], "description": " ".join(parts[2:])})
        return
    if action == "list":
        teams = manager.list_teams()
        lines = [
            f"- {team.name} · {len(team.members)} members · {len(team.active_members())} active"
            for team in teams
        ]
        await ctx.ui.add_system_message("团队\n" + ("\n".join(lines) if lines else "(none)"))
        return
    team_name = (
        parts[1]
        if len(parts) > 1 and not parts[1].startswith("--") and action not in {"stop"}
        else getattr(ctx.agent, "team_name", "")
    )
    team = manager.get_team(team_name or "__active__")
    if action in {"status", "members"}:
        if team is None:
            await ctx.ui.add_system_message("没有活动团队。")
            return
        members = [
            f"- {member.name} · {member.agent_type} · "
            f"{'active' if member.is_active is not False else 'idle'} · "
            f"{member.backend_type.value}\n"
            f"  {member.worktree_path}"
            for member in team.members
        ]
        await ctx.ui.add_system_message(
            f"团队 {team.name}\nLead: {team.lead_agent_id}\n"
            + ("\n".join(members) if members else "(no members)")
        )
    elif action == "tasks":
        if team is None:
            await ctx.ui.add_system_message("没有活动团队。")
            return
        tasks = manager.get_task_store(team.name).list_tasks()
        await ctx.ui.add_system_message(
            "共享任务\n"
            + (
                "\n".join(
                    f"- #{task.id} [{task.status}] {task.title} -> {task.assignee or '-'}"
                    for task in tasks
                )
                if tasks
                else "(none)"
            )
        )
    elif action == "merge":
        await _run_tool(ctx, "TeamMerge", {"team_name": team_name})
    elif action == "stop" and len(parts) == 2:
        await _run_tool(ctx, "TeamStop", {"member": parts[1]})
    elif action == "delete":
        await _run_tool(
            ctx,
            "TeamDelete",
            {"team_name": team_name, "discard_worktrees": "--discard" in parts},
        )
    else:
        await ctx.ui.add_system_message(f"未知或不完整的团队子命令：{' '.join(parts)}")


TEAM_COMMAND = Command(
    name="team",
    description="创建、查看、合并和删除持久化 Agent 团队",
    usage=(
        "/team create <name> | list | status [name] | tasks [name] | "
        "merge [name] | stop <member> | delete [name] [--discard]"
    ),
    arg_prompt="create | list | status | tasks | merge | stop | delete",
    type=CommandType.LOCAL_UI,
    handler=handle_team,
)

__all__ = ["TEAM_COMMAND", "handle_team"]
