"""Cross-process mailbox using one atomic JSON file per message."""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

MessageType = Literal["text", "shutdown_request", "shutdown_response", "approval_response"]


@dataclass(frozen=True, slots=True)
class MailboxMessage:
    id: str
    from_agent: str
    to_agent: str
    content: str
    summary: str
    message_type: MessageType
    timestamp: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> MailboxMessage:
        return cls(
            id=str(value["id"]),
            from_agent=str(value["from_agent"]),
            to_agent=str(value["to_agent"]),
            content=str(value.get("content", "")),
            summary=str(value.get("summary", "")),
            message_type=str(value.get("message_type", "text")),  # type: ignore[arg-type]
            timestamp=float(value["timestamp"]),
            metadata=dict(value.get("metadata") or {}),
        )


class Mailbox:
    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._last_write_ns = 0

    def write(self, agent_id: str, message: MailboxMessage) -> Path:
        directory = self.base_dir / agent_id
        directory.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            write_ns = max(time.time_ns(), self._last_write_ns + 1)
            self._last_write_ns = write_ns
            filename = f"{write_ns:020d}_{message.id}.json"
            target = directory / filename
            temporary = directory / f".{filename}.{uuid.uuid4().hex}.tmp"
            temporary.write_text(
                json.dumps(message.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(target)
        return target

    def _read_files(self, agent_id: str, *, consume: bool) -> list[MailboxMessage]:
        directory = self.base_dir / agent_id
        if not directory.is_dir():
            return []
        messages: list[MailboxMessage] = []
        for source in sorted(directory.glob("*.json"), key=lambda item: item.name):
            try:
                raw = json.loads(source.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    messages.append(MailboxMessage.from_dict(raw))
                if consume:
                    source.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                # A corrupt or concurrently replaced file is left for diagnosis.
                continue
        return messages

    def read(self, agent_id: str) -> list[MailboxMessage]:
        return self._read_files(agent_id, consume=False)

    def consume(self, agent_id: str) -> list[MailboxMessage]:
        return self._read_files(agent_id, consume=True)

    def broadcast(
        self,
        team_members: list[str] | tuple[str, ...] | set[str],
        message: MailboxMessage,
        exclude: str = "",
    ) -> list[Path]:
        paths = []
        for agent_id in dict.fromkeys(team_members):
            if agent_id != exclude:
                copy = create_message(
                    message.from_agent,
                    agent_id,
                    message.content,
                    message.summary,
                    message.message_type,
                    message.metadata,
                )
                paths.append(self.write(agent_id, copy))
        return paths

    def cleanup(self, agent_id: str) -> None:
        shutil.rmtree(self.base_dir / agent_id, ignore_errors=True)

    def cleanup_all(self) -> None:
        shutil.rmtree(self.base_dir, ignore_errors=True)


def create_message(
    from_agent: str,
    to_agent: str,
    content: str,
    summary: str = "",
    message_type: MessageType = "text",
    metadata: dict[str, Any] | None = None,
) -> MailboxMessage:
    return MailboxMessage(
        id=uuid.uuid4().hex[:12],
        from_agent=from_agent,
        to_agent=to_agent,
        content=content,
        summary=summary,
        message_type=message_type,
        timestamp=time.time(),
        metadata=dict(metadata or {}),
    )


__all__ = ["Mailbox", "MailboxMessage", "MessageType", "create_message"]
