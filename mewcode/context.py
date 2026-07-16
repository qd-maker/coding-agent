"""Context budgeting, deterministic compaction, and large tool-result persistence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from mewcode.conversation import ConversationManager, Message

SINGLE_RESULT_CHAR_LIMIT = 8_000
MAX_OUTPUT_CHARS = 20_000
COMPACT_TRIGGER_RATIO = 0.85


@dataclass(frozen=True, slots=True)
class CompactEvent:
    removed_messages: int
    summary: str


@dataclass(slots=True)
class CompactCircuitBreaker:
    failures: int = 0
    max_failures: int = 3

    @property
    def open(self) -> bool:
        return self.failures >= self.max_failures


def ensure_session_dir(work_dir: str | Path) -> Path:
    session = Path(work_dir).resolve() / ".mewcode" / "sessions" / uuid.uuid4().hex[:12]
    session.mkdir(parents=True, exist_ok=True)
    return session


def persist_tool_result(session_dir: Path, tool_id: str, output: str) -> Path:
    safe_id = "".join(char for char in tool_id if char.isalnum() or char in "-_") or "tool"
    path = session_dir / f"{safe_id}.txt"
    path.write_text(output, encoding="utf-8")
    return path


def make_persisted_preview(output: str, path: Path, limit: int = 2_000) -> str:
    preview = output[:limit]
    omitted = max(0, len(output) - len(preview))
    return (
        f"{preview}\n\n[full tool result saved to {path}; "
        f"{omitted} character(s) omitted from context]"
    )


def apply_tool_result_budget(conversation: ConversationManager) -> int:
    """Return the current tool-result character budget already held in history."""
    return sum(
        len(result.content) for message in conversation.history for result in message.tool_results
    )


def _estimated_tokens(conversation: ConversationManager) -> int:
    characters = sum(
        len(message.content) + sum(len(result.content) for result in message.tool_results)
        for message in conversation.history
    )
    return max(conversation.last_input_tokens, characters // 4)


def auto_compact(
    conversation: ConversationManager,
    context_window: int,
    breaker: CompactCircuitBreaker,
) -> CompactEvent | str | None:
    if breaker.open or _estimated_tokens(conversation) < context_window * COMPACT_TRIGGER_RATIO:
        return None
    if len(conversation.history) < 6:
        return None
    try:
        keep = conversation.history[-4:]
        removed = conversation.history[:-4]
        summary_lines = [
            f"- {message.role}: {message.content[:240]}" for message in removed if message.content
        ]
        summary = "Conversation compacted:\n" + "\n".join(summary_lines[-20:])
        flags = (conversation.env_injected, conversation.ltm_injected)
        conversation.history = [Message("user", summary), *keep]
        conversation.env_injected, conversation.ltm_injected = flags
        breaker.failures = 0
        return CompactEvent(len(removed), summary)
    except Exception as exc:  # pragma: no cover - defensive context boundary
        breaker.failures += 1
        return f"Context compaction failed: {type(exc).__name__}: {exc}"


__all__ = [
    "CompactCircuitBreaker",
    "CompactEvent",
    "MAX_OUTPUT_CHARS",
    "SINGLE_RESULT_CHAR_LIMIT",
    "apply_tool_result_budget",
    "auto_compact",
    "ensure_session_dir",
    "make_persisted_preview",
    "persist_tool_result",
]
