"""Crash-tolerant JSONL conversation archives and lightweight metadata."""

from __future__ import annotations

import builtins
import json
import os
import random
import re
import string
from collections.abc import AsyncIterator, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, TextIO
from uuid import uuid4

from mewcode.context import estimate_message_tokens
from mewcode.conversation import (
    ConversationManager,
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.tools.base import StreamEvent, TextDelta

SESSIONS_DIR = ".mewcode/sessions"
TIME_GAP_THRESHOLD = timedelta(hours=24)
DEFAULT_MAX_AGE_DAYS = 30
TITLE_MAX_LENGTH = 50
SESSION_SUMMARY_MAX_LENGTH = 160

_SESSION_ID_RE = re.compile(r"^session_\d{8}_\d{6}_[a-z0-9]{4}$")

SESSION_SUMMARY_PROMPT = """\
请用一句话总结这段会话的当前目标、已完成工作和下一步。不要调用工具，不要使用 Markdown 列表。
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso_now() -> str:
    return _now().isoformat()


class RecordType(StrEnum):
    SYSTEM_PROMPT = "system_prompt"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL_RESULT = "tool_result"
    COMPRESSION = "compression"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    type: RecordType
    content: Any
    timestamp: str = field(default_factory=_iso_now)
    tool_use_id: str | None = None
    is_error: bool = False

    def to_jsonl(self) -> str:
        payload: dict[str, Any] = {
            "type": self.type.value,
            "content": self.content,
            "timestamp": self.timestamp,
        }
        if self.tool_use_id:
            payload["tool_use_id"] = self.tool_use_id
        if self.type is RecordType.TOOL_RESULT:
            payload["is_error"] = self.is_error
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_jsonl(cls, line: str) -> SessionRecord | None:
        try:
            payload = json.loads(line)
            if not isinstance(payload, dict):
                return None
            return cls(
                type=RecordType(str(payload["type"])),
                content=payload.get("content", ""),
                timestamp=str(payload["timestamp"]),
                tool_use_id=(
                    str(payload["tool_use_id"]) if payload.get("tool_use_id") is not None else None
                ),
                is_error=bool(payload.get("is_error", False)),
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_message(cls, message: Message) -> list[SessionRecord]:
        timestamp = _iso_now()
        if message.tool_results:
            return [
                cls(
                    RecordType.TOOL_RESULT,
                    result.content,
                    timestamp,
                    tool_use_id=result.tool_use_id,
                    is_error=result.is_error,
                )
                for result in message.tool_results
            ]

        if message.role == "user" and message.content.startswith("[摘要]\n"):
            return [
                cls(
                    RecordType.COMPRESSION,
                    message.content.removeprefix("[摘要]\n"),
                    timestamp,
                )
            ]

        record_type = RecordType.ASSISTANT if message.role == "assistant" else RecordType.USER
        if message.role == "assistant" and (message.tool_uses or message.thinking_blocks):
            blocks: list[dict[str, Any]] = []
            for thinking in message.thinking_blocks:
                blocks.append(
                    {
                        "type": "thinking",
                        "thinking": thinking.thinking,
                        "signature": thinking.signature,
                    }
                )
            if message.content:
                blocks.append({"type": "text", "text": message.content})
            for tool_use in message.tool_uses:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_use.tool_id,
                        "name": tool_use.name,
                        "input": tool_use.input,
                    }
                )
            return [cls(record_type, blocks, timestamp)]
        return [cls(record_type, message.content, timestamp)]


def _assistant_from_blocks(blocks: list[Any]) -> Message:
    text: list[str] = []
    tool_uses: list[ToolUseBlock] = []
    thinking_blocks: list[ThinkingBlock] = []
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        block_type = raw.get("type")
        if block_type == "text":
            text.append(str(raw.get("text", "")))
        elif block_type == "tool_use":
            tool_id = str(raw.get("id", ""))
            name = str(raw.get("name", ""))
            arguments = raw.get("input", {})
            if tool_id and name and isinstance(arguments, dict):
                tool_uses.append(ToolUseBlock(tool_id, name, dict(arguments)))
        elif block_type == "thinking":
            thinking_blocks.append(
                ThinkingBlock(
                    str(raw.get("thinking", "")),
                    str(raw.get("signature", "")),
                )
            )
    return Message(
        role="assistant",
        content="".join(text),
        tool_uses=tool_uses,
        thinking_blocks=thinking_blocks,
    )


def records_to_messages(records: Iterable[SessionRecord]) -> list[Message]:
    """Rebuild provider-neutral messages from valid JSONL records."""
    messages: list[Message] = []
    pending_results: list[ToolResultBlock] = []

    def flush_results() -> None:
        if pending_results:
            messages.append(Message(role="user", tool_results=list(pending_results)))
            pending_results.clear()

    for record in records:
        if record.type is RecordType.TOOL_RESULT:
            if record.tool_use_id:
                pending_results.append(
                    ToolResultBlock(
                        record.tool_use_id,
                        str(record.content),
                        record.is_error,
                    )
                )
            continue

        flush_results()
        if record.type is RecordType.SYSTEM_PROMPT:
            continue
        if record.type is RecordType.COMPRESSION:
            # A compression record is an append-only checkpoint: its summary
            # replaces every earlier message in the archive on restoration.
            messages.clear()
            messages.append(Message(role="user", content=f"[摘要]\n{record.content}"))
        elif record.type is RecordType.USER:
            messages.append(Message(role="user", content=str(record.content)))
        elif record.type is RecordType.ASSISTANT:
            if isinstance(record.content, list):
                messages.append(_assistant_from_blocks(record.content))
            else:
                messages.append(Message(role="assistant", content=str(record.content)))
    flush_results()
    return messages


def _tool_use_ids(record: SessionRecord) -> set[str]:
    if record.type is not RecordType.ASSISTANT or not isinstance(record.content, list):
        return set()
    return {
        str(block.get("id"))
        for block in record.content
        if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
    }


def validate_message_chain(records: list[SessionRecord]) -> int:
    """Return the record count of the largest valid tool-chain prefix."""
    pending: set[str] = set()
    last_complete = 0
    for index, record in enumerate(records):
        if pending and record.type not in {RecordType.TOOL_RESULT}:
            break
        if record.type is RecordType.ASSISTANT:
            pending.update(_tool_use_ids(record))
        elif record.type is RecordType.TOOL_RESULT:
            tool_id = record.tool_use_id
            if not tool_id or tool_id not in pending:
                break
            pending.remove(tool_id)
        if not pending:
            last_complete = index + 1
    return last_complete


@dataclass(slots=True)
class SessionMeta:
    id: str
    title: str = "新会话"
    summary: str = ""
    message_count: int = 0
    total_tokens: int = 0
    created_at: datetime = field(default_factory=_now)
    last_active: datetime = field(default_factory=_now)

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["last_active"] = self.last_active.isoformat()
        temp = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            temp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temp, target)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: str | Path) -> SessionMeta | None:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
            return cls(
                id=str(payload["id"]),
                title=str(payload.get("title", "新会话")),
                summary=str(payload.get("summary", "")),
                message_count=int(payload.get("message_count", 0)),
                total_tokens=int(payload.get("total_tokens", 0)),
                created_at=datetime.fromisoformat(str(payload["created_at"])),
                last_active=datetime.fromisoformat(str(payload["last_active"])),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None


class Session:
    """Append-only handle for one active JSONL session."""

    def __init__(
        self,
        session_id: str,
        file: TextIO,
        meta: SessionMeta,
        sessions_dir: str | Path,
    ) -> None:
        self.id = session_id
        self._file = file
        self.meta = meta
        self.sessions_dir = Path(sessions_dir)

    @property
    def closed(self) -> bool:
        return self._file.closed

    @property
    def meta_path(self) -> Path:
        return self.sessions_dir / f"{self.id}.meta"

    def append(self, message: Message) -> None:
        if self._file.closed:
            return
        records = SessionRecord.from_message(message)
        for record in records:
            self._file.write(record.to_jsonl() + "\n")
        self._file.flush()
        try:
            os.fsync(self._file.fileno())
        except OSError:
            pass
        self.meta.message_count += 1
        self.meta.total_tokens += estimate_message_tokens(message)
        self.meta.last_active = _now()
        if (
            self.meta.title == "新会话"
            and message.role == "user"
            and message.content.strip()
            and not message.content.startswith("[")
        ):
            title = " ".join(message.content.strip().split())
            self.meta.title = title[:TITLE_MAX_LENGTH]
        self.meta.save(self.meta_path)

    def update_summary(self, summary: str) -> None:
        normalized = " ".join(summary.strip().split())
        if not normalized:
            return
        self.meta.summary = normalized[:SESSION_SUMMARY_MAX_LENGTH]
        self.meta.save(self.meta_path)

    def close(self) -> None:
        if self._file.closed:
            return
        self._file.flush()
        self._file.close()


@dataclass(slots=True)
class ResumeResult:
    session: Session
    messages: list[Message]
    last_active: datetime


def _message_text(message: Message) -> str:
    if message.content:
        return f"{message.role}: {message.content}"
    if message.tool_results:
        return "tool: " + "\n".join(result.content for result in message.tool_results)
    if message.tool_uses:
        return "assistant tools: " + ", ".join(use.name for use in message.tool_uses)
    return ""


async def generate_session_summary(
    client: Any,
    conversation: ConversationManager,
    protocol: str = "anthropic",
) -> str:
    """Generate an optional one-line meta summary without entering the Agent loop."""
    del protocol
    recent = [_message_text(message) for message in conversation.history[-10:]]
    prompt = f"{SESSION_SUMMARY_PROMPT}\n\n===== 最近消息 =====\n" + "\n".join(
        line for line in recent if line
    )
    summary_conv = ConversationManager([Message(role="user", content=prompt)])
    parts: list[str] = []
    try:
        stream: AsyncIterator[StreamEvent] = client.stream(
            summary_conv,
            system="你是会话摘要助手。只输出一句话。",
            tools=None,
        )
        async for event in stream:
            if isinstance(event, TextDelta):
                parts.append(event.text)
    except Exception:  # noqa: BLE001 - optional metadata must never break a session
        return ""
    return "".join(parts).strip()


def build_time_gap_message(
    last_active: datetime,
    *,
    now: datetime | None = None,
) -> Message | None:
    current = now or _now()
    if last_active.tzinfo is None:
        last_active = last_active.replace(tzinfo=UTC)
    gap = current - last_active
    if gap < TIME_GAP_THRESHOLD:
        return None
    hours = max(24, int(gap.total_seconds() // 3600))
    elapsed = f"{hours // 24} 天" if hours >= 48 else f"{hours} 小时"
    return Message(
        role="user",
        content=(
            f"[系统提示] 距离上次会话已过去 {elapsed}。"
            "代码可能有变更，建议在操作前重新读取相关文件。"
        ),
    )


def _generate_session_id() -> str:
    suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
    return f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{suffix}"


class SessionManager:
    def __init__(self, work_dir: str | Path) -> None:
        self.sessions_dir = Path(work_dir).resolve() / SESSIONS_DIR
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _paths(self, session_id: str) -> tuple[Path, Path] | None:
        if not _SESSION_ID_RE.fullmatch(session_id):
            return None
        return (
            self.sessions_dir / f"{session_id}.jsonl",
            self.sessions_dir / f"{session_id}.meta",
        )

    def create(self) -> Session:
        session_id = _generate_session_id()
        while (self.sessions_dir / f"{session_id}.jsonl").exists():
            session_id = _generate_session_id()
        jsonl_path = self.sessions_dir / f"{session_id}.jsonl"
        meta = SessionMeta(id=session_id)
        meta.save(self.sessions_dir / f"{session_id}.meta")
        file = jsonl_path.open("a", encoding="utf-8", newline="\n")
        return Session(session_id, file, meta, self.sessions_dir)

    def list(self) -> list[SessionMeta]:
        metas = [
            meta
            for path in self.sessions_dir.glob("*.meta")
            if (meta := SessionMeta.load(path)) is not None
        ]
        return sorted(metas, key=lambda meta: meta.last_active, reverse=True)

    def _read_records(
        self,
        path: Path,
    ) -> tuple[builtins.list[SessionRecord], bool]:
        records: builtins.list[SessionRecord] = []
        had_invalid_line = False
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    record = SessionRecord.from_jsonl(stripped)
                    if record is not None:
                        records.append(record)
                    else:
                        had_invalid_line = True
        except OSError:
            return [], False
        return records, had_invalid_line

    @staticmethod
    def _rewrite_records(
        path: Path,
        records: builtins.list[SessionRecord],
    ) -> None:
        temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temp.open("w", encoding="utf-8", newline="\n") as handle:
                for record in records:
                    handle.write(record.to_jsonl() + "\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError:
                    pass
            os.replace(temp, path)
        finally:
            if temp.exists():
                temp.unlink(missing_ok=True)

    def resume(self, session_id: str) -> ResumeResult | None:
        paths = self._paths(session_id)
        if paths is None:
            return None
        jsonl_path, meta_path = paths
        if not jsonl_path.is_file():
            return None
        meta = SessionMeta.load(meta_path)
        meta_was_missing = meta is None
        if meta is None:
            meta = SessionMeta(id=session_id)
        records, had_invalid_line = self._read_records(jsonl_path)
        valid_count = validate_message_chain(records)
        valid_records = records[:valid_count]
        if had_invalid_line or valid_count != len(records):
            self._rewrite_records(jsonl_path, valid_records)

        restored = records_to_messages(valid_records)
        expected_tokens = sum(estimate_message_tokens(message) for message in restored)
        latest_record_time: datetime | None = None
        for record in valid_records:
            try:
                timestamp = datetime.fromisoformat(record.timestamp)
            except ValueError:
                continue
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=UTC)
            if latest_record_time is None or timestamp > latest_record_time:
                latest_record_time = timestamp
        last_active_changed = False
        if latest_record_time is not None and meta_was_missing:
            meta.last_active = latest_record_time
            last_active_changed = True

        metadata_changed = (
            meta.message_count != len(restored)
            or meta.total_tokens != expected_tokens
            or meta_was_missing
            or last_active_changed
            or had_invalid_line
            or valid_count != len(records)
        )
        if meta.title == "新会话":
            first_user = next(
                (
                    message.content
                    for message in restored
                    if message.role == "user"
                    and message.content.strip()
                    and not message.content.startswith("[")
                ),
                "",
            )
            if first_user:
                meta.title = " ".join(first_user.strip().split())[:TITLE_MAX_LENGTH]
                metadata_changed = True
        if metadata_changed:
            meta.message_count = len(restored)
            meta.total_tokens = expected_tokens
            meta.save(meta_path)
        last_active = meta.last_active
        file = jsonl_path.open("a", encoding="utf-8", newline="\n")
        return ResumeResult(
            Session(session_id, file, meta, self.sessions_dir),
            restored,
            last_active,
        )

    def delete(self, session_id: str) -> bool:
        paths = self._paths(session_id)
        if paths is None:
            return False
        deleted = False
        for path in paths:
            if path.exists():
                try:
                    path.unlink()
                    deleted = True
                except OSError:
                    pass
        return deleted

    def cleanup(self, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> int:
        cutoff = _now() - timedelta(days=max_age_days)
        removed = 0
        for meta in self.list():
            last_active = meta.last_active
            if last_active.tzinfo is None:
                last_active = last_active.replace(tzinfo=UTC)
            if last_active < cutoff and self.delete(meta.id):
                removed += 1
        return removed


__all__ = [
    "DEFAULT_MAX_AGE_DAYS",
    "RecordType",
    "ResumeResult",
    "SESSION_SUMMARY_PROMPT",
    "SESSIONS_DIR",
    "Session",
    "SessionManager",
    "SessionMeta",
    "SessionRecord",
    "TIME_GAP_THRESHOLD",
    "TITLE_MAX_LENGTH",
    "build_time_gap_message",
    "generate_session_summary",
    "records_to_messages",
    "validate_message_chain",
]
