"""Context budgeting tests for the ch04 Agent loop."""

from __future__ import annotations

from mewcode.context import CompactCircuitBreaker, CompactEvent, auto_compact
from mewcode.conversation import ConversationManager


def test_auto_compact_preserves_recent_messages() -> None:
    conversation = ConversationManager()
    for index in range(8):
        conversation.add_user_message(f"message-{index}")
    conversation.last_input_tokens = 900

    compacted = auto_compact(conversation, 1_000, CompactCircuitBreaker())

    assert isinstance(compacted, CompactEvent)
    assert compacted.removed_messages == 4
    assert conversation.history[0].content.startswith("Conversation compacted:")
    assert [message.content for message in conversation.history[-4:]] == [
        "message-4",
        "message-5",
        "message-6",
        "message-7",
    ]


def test_auto_compact_skips_when_below_threshold() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("small")
    assert auto_compact(conversation, 10_000, CompactCircuitBreaker()) is None
