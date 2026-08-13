"""Structured task completion messages injected into the parent conversation."""

from __future__ import annotations

from collections.abc import Iterable

from simplecode.agents.task_manager import BackgroundTask
from simplecode.conversation import ConversationManager

MAX_NOTIFICATION_RESULT_LENGTH = 5000


def format_task_notification(task: BackgroundTask) -> str:
    result = task.result or task.error or "(no result)"
    if len(result) > MAX_NOTIFICATION_RESULT_LENGTH:
        result = result[:MAX_NOTIFICATION_RESULT_LENGTH] + "\n... (truncated)"
    return (
        "<task-notification>\n"
        f"Task ID: {task.task_id}\n"
        f"Agent: {task.agent_type}\n"
        f"Status: {task.status}\n"
        f"Elapsed: {task.elapsed_seconds:.1f}s\n"
        f"Tokens: {task.input_tokens} in / {task.output_tokens} out\n"
        f"Result:\n{result}\n"
        "</task-notification>"
    )


def inject_task_notifications(
    conversation: ConversationManager,
    completed: Iterable[BackgroundTask],
) -> list[str]:
    notifications = [format_task_notification(task) for task in completed]
    for notification in notifications:
        conversation.add_user_message(notification)
    return notifications


__all__ = [
    "MAX_NOTIFICATION_RESULT_LENGTH",
    "format_task_notification",
    "inject_task_notifications",
]
