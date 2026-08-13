"""Permission-mode and rule management slash command."""

from __future__ import annotations

from typing import Protocol

from simplecode.commands.registry import Command, CommandContext, CommandType
from simplecode.permissions import PermissionMode, parse_rule

_MODE_ORDER = (
    PermissionMode.ACCEPT_EDITS,
    PermissionMode.PLAN,
    PermissionMode.BYPASS,
)

_MODE_ALIASES = {
    "acceptedits": PermissionMode.ACCEPT_EDITS,
    "accept-edits": PermissionMode.ACCEPT_EDITS,
    "accept edits": PermissionMode.ACCEPT_EDITS,
    "do": PermissionMode.ACCEPT_EDITS,
    "plan": PermissionMode.PLAN,
    "yolo": PermissionMode.BYPASS,
    "bypass": PermissionMode.BYPASS,
    "bypasspermissions": PermissionMode.BYPASS,
}

_MODE_LABELS = {
    PermissionMode.ACCEPT_EDITS: "Accept Edits",
    PermissionMode.PLAN: "Plan",
    PermissionMode.BYPASS: "YOLO",
}

PERMISSION_USAGE = (
    "用法：/permission [mode <accept-edits|plan|yolo> | rules | "
    "add <allow|deny> <Tool(pattern)> | reset]"
)


class PermissionModeTarget(Protocol):
    @property
    def permission_mode(self) -> PermissionMode: ...

    def set_permission_mode(self, mode: PermissionMode) -> None: ...


def parse_permission_mode(value: str) -> PermissionMode:
    normalized = value.strip().casefold()
    mode = _MODE_ALIASES.get(normalized)
    if mode is not None:
        return mode
    raise ValueError(f"Unknown permission mode {value!r}; choose: acceptEdits, plan, or yolo")


def cycle_permission_mode(current: PermissionMode) -> PermissionMode:
    if current not in _MODE_ORDER:
        return PermissionMode.ACCEPT_EDITS
    index = _MODE_ORDER.index(current)
    return _MODE_ORDER[(index + 1) % len(_MODE_ORDER)]


def permission_mode_label(mode: PermissionMode) -> str:
    return _MODE_LABELS.get(mode, "Accept Edits")


def handle_permission_mode(
    target: PermissionModeTarget,
    requested: str | None = None,
) -> str:
    """Synchronous utility retained for Shift+Tab and inline prompts."""

    mode = (
        parse_permission_mode(requested)
        if requested
        else cycle_permission_mode(target.permission_mode)
    )
    target.set_permission_mode(mode)
    return f"{permission_mode_label(mode)} on (shift+tab to cycle)"


def _set_mode(ctx: CommandContext, mode: PermissionMode) -> None:
    callback = ctx.config.get("set_permission_mode")
    if callable(callback):
        callback(mode)
    else:
        ctx.agent.set_permission_mode(mode)


def _format_rules(ctx: CommandContext) -> str:
    checker = ctx.config.get("permission_checker") or getattr(ctx.agent, "permission_checker", None)
    engine = getattr(checker, "rule_engine", None)
    rules = list(getattr(engine, "rules", []))
    if not rules:
        return "当前没有自定义权限规则。"
    lines = ["权限规则（高优先级规则在后写入时优先）："]
    lines.extend(
        f"  {index}. {rule.effect} {rule.tool_name}({rule.pattern})"
        for index, rule in enumerate(rules, 1)
    )
    return "\n".join(lines)


async def handle_permission(ctx: CommandContext) -> None:
    args = ctx.args.strip()
    parts = args.split(maxsplit=2)
    subcommand = parts[0].casefold() if parts else "status"
    checker = ctx.config.get("permission_checker") or getattr(ctx.agent, "permission_checker", None)
    engine = getattr(checker, "rule_engine", None)

    try:
        if subcommand == "status" and ctx.config.get("invoked_name") == "mode" and not args:
            mode = cycle_permission_mode(ctx.agent.permission_mode)
            _set_mode(ctx, mode)
            message = f"{permission_mode_label(mode)} on (shift+tab to cycle)"
        elif subcommand in {"", "status"}:
            message = f"当前权限模式：{permission_mode_label(ctx.agent.permission_mode)}"
        elif subcommand in _MODE_ALIASES:
            mode = parse_permission_mode(args)
            _set_mode(ctx, mode)
            message = f"{permission_mode_label(mode)} on (shift+tab to cycle)"
        elif subcommand == "mode" and len(parts) >= 2:
            mode = parse_permission_mode(" ".join(parts[1:]))
            _set_mode(ctx, mode)
            message = f"{permission_mode_label(mode)} on (shift+tab to cycle)"
        elif subcommand == "rules":
            message = _format_rules(ctx)
        elif subcommand == "add" and len(parts) == 3:
            if engine is None:
                message = "权限规则引擎未初始化。"
            else:
                rule = parse_rule(parts[2], parts[1].casefold())
                engine.append_local_rule(rule)
                message = f"已添加权限规则：{rule.effect} {rule.tool_name}({rule.pattern})"
        elif subcommand == "reset":
            if engine is None:
                message = "权限规则引擎未初始化。"
            else:
                engine.clear_local_rules()
                message = "本项目本地权限规则已重置。"
        else:
            message = PERMISSION_USAGE
    except ValueError as exc:
        message = f"权限参数错误：{exc}"

    await ctx.ui.add_system_message(message)
    ctx.ui.refresh_status()


PERMISSION_COMMAND = Command(
    name="permission",
    aliases=("perm", "mode"),
    description="查看或修改权限模式与规则",
    usage=PERMISSION_USAGE,
    arg_prompt="mode | rules | add | reset",
    type=CommandType.LOCAL_UI,
    handler=handle_permission,
)

__all__ = [
    "PERMISSION_COMMAND",
    "PERMISSION_USAGE",
    "PermissionModeTarget",
    "cycle_permission_mode",
    "handle_permission",
    "handle_permission_mode",
    "parse_permission_mode",
    "permission_mode_label",
]
