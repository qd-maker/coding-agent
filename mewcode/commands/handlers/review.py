"""Prompt command for code review."""

from __future__ import annotations

from mewcode.commands.registry import Command, CommandContext, CommandType

REVIEW_PROMPT = """请审查当前项目或最近的代码变更，重点检查：
1. 逻辑错误与边界条件
2. 安全问题与权限边界
3. 性能问题与不必要的资源消耗
4. 代码风格、可维护性与测试缺口

请按严重程度列出可执行的问题；如果没有发现问题，请明确说明，并指出剩余风险。"""


async def handle_review(ctx: CommandContext) -> None:
    prompt = REVIEW_PROMPT
    if ctx.args:
        prompt += f"\n\n额外关注：{ctx.args}"
    await ctx.ui.send_user_message(prompt)


REVIEW_COMMAND = Command(
    name="review",
    aliases=("r",),
    description="让 Agent 审查当前代码",
    usage="/review [额外关注点]",
    arg_prompt="额外关注点",
    type=CommandType.PROMPT,
    handler=handle_review,
)

__all__ = ["REVIEW_COMMAND", "REVIEW_PROMPT", "handle_review"]
