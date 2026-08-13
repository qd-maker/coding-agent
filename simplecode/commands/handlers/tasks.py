"""Inspect and control background sub-agent tasks and traces."""

from __future__ import annotations

from simplecode.agents.task_manager import BackgroundTask, TaskManager
from simplecode.agents.trace import TraceRegistry
from simplecode.commands.registry import Command, CommandContext, CommandType


def _task_manager(ctx: CommandContext) -> TaskManager:
    manager = ctx.config.get("task_manager")
    if not isinstance(manager, TaskManager):
        raise RuntimeError("Sub-agent TaskManager is not available")
    return manager


def _one_line(task: BackgroundTask) -> str:
    return (
        f"{task.task_id}  {task.status:<9}  {task.agent_type:<16} "
        f"{task.elapsed_seconds:>6.1f}s  {task.description}"
    )


async def handle_tasks(ctx: CommandContext) -> None:
    tasks = _task_manager(ctx).list_tasks()
    if not tasks:
        await ctx.ui.add_system_message("当前没有后台 Agent 任务。")
        return
    rendered = "\n".join(_one_line(task) for task in tasks)
    await ctx.ui.add_system_message(f"后台 Agent 任务\n{rendered}")


async def handle_task(ctx: CommandContext) -> None:
    parts = ctx.args.split()
    if len(parts) != 2 or parts[0].casefold() not in {"info", "cancel"}:
        await ctx.ui.add_system_message("用法：/task info <id> | /task cancel <id>")
        return
    action, task_id = parts[0].casefold(), parts[1]
    manager = _task_manager(ctx)
    task = manager.get(task_id)
    if task is None:
        await ctx.ui.add_system_message(f"未找到后台任务：{task_id}")
        return
    if action == "cancel":
        cancelled = await manager.cancel(task_id)
        await ctx.ui.add_system_message(
            f"任务 {task_id} 已取消。" if cancelled else f"任务 {task_id} 当前不可取消。"
        )
        return
    result = task.result or task.error or "(尚无结果)"
    await ctx.ui.add_system_message(
        "\n".join(
            (
                f"任务：{task.task_id}",
                f"Agent：{task.agent_type}",
                f"状态：{task.status}",
                f"耗时：{task.elapsed_seconds:.1f}s",
                f"Token：{task.input_tokens} in / {task.output_tokens} out",
                f"Trace：{task.trace_id}",
                f"描述：{task.description}",
                f"结果：\n{result}",
            )
        )
    )


async def handle_trace(ctx: CommandContext) -> None:
    registry = ctx.config.get("trace_registry")
    if not isinstance(registry, TraceRegistry):
        raise RuntimeError("Sub-agent TraceRegistry is not available")
    trace_id = ctx.args.strip() or registry.latest_trace_id
    if not trace_id:
        await ctx.ui.add_system_message("当前没有 Agent 调用链。")
        return
    nodes = registry.get_tree(trace_id)
    if not nodes:
        await ctx.ui.add_system_message(f"未找到 Trace：{trace_id}")
        return
    in_tokens, out_tokens = registry.get_total_tokens(trace_id)
    lines = [f"Trace {trace_id} · {in_tokens} in / {out_tokens} out"]
    lines.extend(
        f"- {node.agent_id} <- {node.parent_id or '-'} · {node.agent_type} · "
        f"{node.status} · {node.elapsed_seconds:.1f}s"
        for node in nodes
    )
    await ctx.ui.add_system_message("\n".join(lines))


TASKS_COMMAND = Command(
    name="tasks",
    description="列出后台 Agent 任务",
    usage="/tasks",
    type=CommandType.LOCAL,
    handler=handle_tasks,
)
TASK_COMMAND = Command(
    name="task",
    description="查看或取消一个后台 Agent 任务",
    usage="/task info <id> | /task cancel <id>",
    arg_prompt="info <id> | cancel <id>",
    type=CommandType.LOCAL,
    handler=handle_task,
)
TRACE_COMMAND = Command(
    name="trace",
    description="查看最近或指定的 Agent 调用链",
    usage="/trace [trace-id]",
    type=CommandType.LOCAL,
    handler=handle_trace,
)

__all__ = [
    "TASKS_COMMAND",
    "TASK_COMMAND",
    "TRACE_COMMAND",
    "handle_task",
    "handle_tasks",
    "handle_trace",
]
