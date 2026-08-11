"""Persist teammate conversations so an idle member can resume in-place."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mewcode.conversation import (
    ConversationManager,
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.teams.models import resolve_team_dir


def _serialize_conversation(conversation: ConversationManager) -> dict[str, Any]:
    return {
        "history": [
            {
                "role": message.role,
                "content": message.content,
                "tool_uses": [
                    {"tool_id": block.tool_id, "name": block.name, "input": block.input}
                    for block in message.tool_uses
                ],
                "tool_results": [
                    {
                        "tool_use_id": block.tool_use_id,
                        "content": block.content,
                        "is_error": block.is_error,
                    }
                    for block in message.tool_results
                ],
                "thinking_blocks": [
                    {"thinking": block.thinking, "signature": block.signature}
                    for block in message.thinking_blocks
                ],
            }
            for message in conversation.history
        ],
        "last_input_tokens": conversation.last_input_tokens,
    }


def _deserialize_conversation(value: dict[str, Any]) -> ConversationManager:
    history: list[Message] = []
    for raw in value.get("history", []):
        if not isinstance(raw, dict) or raw.get("role") not in {"user", "assistant"}:
            continue
        history.append(
            Message(
                role=raw["role"],
                content=str(raw.get("content", "")),
                tool_uses=[
                    ToolUseBlock(
                        tool_id=str(item.get("tool_id", item.get("tool_use_id", ""))),
                        name=str(item.get("name", item.get("tool_name", ""))),
                        input=dict(item.get("input", item.get("arguments")) or {}),
                    )
                    for item in raw.get("tool_uses", [])
                    if isinstance(item, dict)
                ],
                tool_results=[
                    ToolResultBlock(
                        tool_use_id=str(item.get("tool_use_id", "")),
                        content=str(item.get("content", "")),
                        is_error=bool(item.get("is_error", False)),
                    )
                    for item in raw.get("tool_results", [])
                    if isinstance(item, dict)
                ],
                thinking_blocks=[
                    ThinkingBlock(
                        thinking=str(item.get("thinking", "")),
                        signature=str(item.get("signature", "")),
                    )
                    for item in raw.get("thinking_blocks", [])
                    if isinstance(item, dict)
                ],
            )
        )
    conversation = ConversationManager(history=history)
    conversation.env_injected = True
    conversation.ltm_injected = True
    conversation.last_input_tokens = int(value.get("last_input_tokens", 0))
    return conversation


def _transcript_path(
    team_name: str,
    agent_id: str,
    teams_root: str | Path | None = None,
) -> Path:
    return resolve_team_dir(team_name, teams_root) / "transcripts" / f"{agent_id}.json"


def save_transcript(
    team_name: str,
    agent_id: str,
    conversation: ConversationManager,
    teams_root: str | Path | None = None,
) -> Path:
    path = _transcript_path(team_name, agent_id, teams_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(_serialize_conversation(conversation), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def load_transcript(
    team_name: str,
    agent_id: str,
    teams_root: str | Path | None = None,
) -> ConversationManager | None:
    path = _transcript_path(team_name, agent_id, teams_root)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _deserialize_conversation(raw) if isinstance(raw, dict) else None


__all__ = [
    "_deserialize_conversation",
    "_serialize_conversation",
    "load_transcript",
    "save_transcript",
]
