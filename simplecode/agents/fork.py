"""Context-preserving fork construction with nested-fork protection."""

from __future__ import annotations

import copy

from simplecode.conversation import ConversationManager, ToolResultBlock

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"
FORK_BOILERPLATE = """<fork_boilerplate>
You are a non-interactive fork of the parent coding Agent.
- Execute the assigned task directly with the available tools.
- Never create another Agent or fork.
- Do not ask the user questions or request confirmation.
- Preserve the inherited context, but focus only on the assigned task.
- Return a concise report with result, evidence, risks, and follow-ups.
</fork_boilerplate>"""


class ForkError(ValueError):
    """Raised when a fork cannot safely be constructed."""


def build_forked_messages(
    conversation: ConversationManager,
    task: str,
) -> ConversationManager:
    """Deep-copy every message and append the fork execution directive."""

    if any(FORK_BOILERPLATE_TAG in message.content for message in conversation.history):
        raise ForkError("Cannot fork from a forked agent.")
    forked = ConversationManager(
        history=copy.deepcopy(conversation.history),
        env_injected=conversation.env_injected,
        last_input_tokens=conversation.last_input_tokens,
    )
    forked.ltm_injected = conversation.ltm_injected
    if forked.history and forked.history[-1].role == "assistant":
        pending = forked.history[-1].tool_uses
        if pending:
            forked.add_tool_results_message(
                [ToolResultBlock(item.tool_id, "interrupted", is_error=True) for item in pending]
            )
    forked.add_user_message(f"{FORK_BOILERPLATE}\n\n你的任务：\n{task.strip()}")
    return forked


__all__ = [
    "FORK_BOILERPLATE",
    "FORK_BOILERPLATE_TAG",
    "ForkError",
    "build_forked_messages",
]
