"""Two-layer context management: tool-result budget + LLM summarization."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from simplecode.conversation import ConversationManager, Message, ToolResultBlock
from simplecode.tools.base import TextDelta

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SINGLE_RESULT_CHAR_LIMIT = 5_000
AGGREGATE_CHAR_LIMIT = 20_000
PREVIEW_CHARS = 2_000
KEEP_RECENT_TURNS = 10
OLD_RESULT_SNIP_CHARS = 2_000
SNIPPED_TAG = "<snipped>"

SUMMARY_OUTPUT_RESERVE = 20_000
AUTO_COMPACT_SAFETY_MARGIN = 13_000
MANUAL_COMPACT_SAFETY_MARGIN = 3_000
PERSISTED_TAG = "<persisted-output>"
SESSION_SUBDIR = ".simplecode/session/tool-results"

REPLACEMENT_RECORDS_FILENAME = "replacement_records.jsonl"

# Post-compact recovery state
RECOVERY_FILE_LIMIT = 5
RECOVERY_TOKENS_PER_FILE = 5_000
RECOVERY_SKILLS_BUDGET = 25_000
RECOVERY_TOKENS_PER_SKILL = 5_000
_RECOVERY_CHARS_PER_TOKEN = 3.5
_ASCII_CHARS_PER_TOKEN = 4
_MESSAGE_TOKEN_OVERHEAD = 4
_CONVERSATION_TOKEN_OVERHEAD = 3

# ---------------------------------------------------------------------------
# Events / state containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompactEvent:
    before_tokens: int
    after_tokens: int


@dataclass
class ContentReplacementState:
    seen_ids: set[str] = field(default_factory=set)
    replacements: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ContentReplacementRecord:
    tool_use_id: str
    replacement: str
    kind: str = "tool-result"


def create_replacement_state() -> ContentReplacementState:
    return ContentReplacementState()


def clone_replacement_state(src: ContentReplacementState) -> ContentReplacementState:
    return ContentReplacementState(
        seen_ids=set(src.seen_ids),
        replacements=dict(src.replacements),
    )


# ---------------------------------------------------------------------------
# Local token estimation
# ---------------------------------------------------------------------------


def estimate_text_tokens(text: str) -> int:
    """Approximate tokens locally without a provider-specific tokenizer."""
    if not text:
        return 0
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return (ascii_chars + _ASCII_CHARS_PER_TOKEN - 1) // _ASCII_CHARS_PER_TOKEN + (non_ascii_chars)


def estimate_message_tokens(message: Message) -> int:
    """Estimate one provider-neutral message, including structured blocks."""
    total = _MESSAGE_TOKEN_OVERHEAD + estimate_text_tokens(message.content)
    for thinking in message.thinking_blocks:
        total += 4
        total += estimate_text_tokens(thinking.thinking)
        total += estimate_text_tokens(thinking.signature)
    for tool_use in message.tool_uses:
        total += 8
        total += estimate_text_tokens(tool_use.tool_id)
        total += estimate_text_tokens(tool_use.name)
        total += estimate_text_tokens(
            json.dumps(
                tool_use.input,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
        )
    for tool_result in message.tool_results:
        total += 4
        total += estimate_text_tokens(tool_result.tool_use_id)
        total += estimate_text_tokens(tool_result.content)
    return total


def estimate_conversation_tokens(conversation: ConversationManager) -> int:
    """Approximate the current conversation size, including message overhead."""
    if not conversation.history:
        return 0
    return _CONVERSATION_TOKEN_OVERHEAD + sum(
        estimate_message_tokens(message) for message in conversation.history
    )


# ---------------------------------------------------------------------------
# Transcript JSONL I/O
# ---------------------------------------------------------------------------


def append_replacement_records(
    session_dir: str | Path,
    records: list[ContentReplacementRecord],
) -> None:
    if not records:
        return
    path = Path(session_dir) / REPLACEMENT_RECORDS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(
                json.dumps(
                    {
                        "kind": record.kind,
                        "tool_use_id": record.tool_use_id,
                        "replacement": record.replacement,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def load_replacement_records(session_dir: str | Path) -> list[ContentReplacementRecord]:
    path = Path(session_dir) / REPLACEMENT_RECORDS_FILENAME
    if not path.is_file():
        return []
    records: list[ContentReplacementRecord] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            records.append(
                ContentReplacementRecord(
                    tool_use_id=str(data["tool_use_id"]),
                    replacement=str(data["replacement"]),
                    kind=str(data.get("kind", "tool-result")),
                )
            )
    return records


def reconstruct_replacement_state(
    messages: list[Message],
    records: list[ContentReplacementRecord],
    inherited_replacements: dict[str, str] | None = None,
) -> ContentReplacementState:
    candidates = {
        tr.tool_use_id for message in messages for tr in message.tool_results if tr.tool_use_id
    }
    state = ContentReplacementState(seen_ids=set(candidates))
    covered: set[str] = set()
    for record in records:
        if record.kind != "tool-result":
            continue
        if record.tool_use_id not in candidates:
            continue
        state.replacements[record.tool_use_id] = record.replacement
        covered.add(record.tool_use_id)
    if inherited_replacements:
        for tool_id, preview in inherited_replacements.items():
            if tool_id in candidates and tool_id not in covered:
                state.replacements[tool_id] = preview
    return state


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def ensure_session_dir(work_dir: str | Path) -> Path:
    session = Path(work_dir).resolve() / SESSION_SUBDIR
    session.mkdir(parents=True, exist_ok=True)
    return session


def cleanup_tool_results(session_dir: str | Path) -> None:
    path = Path(session_dir)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Layer 1: persist + preview
# ---------------------------------------------------------------------------


def persist_tool_result(
    tool_use_id: str,
    content: str,
    session_dir: str | Path,
) -> Path:
    directory = Path(session_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{tool_use_id}.txt"
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError:
        pass
    return path


def make_persisted_preview(content: str, file_path: str | Path) -> str:
    size_kb = max(1, (len(content) + 1023) // 1024)
    return (
        f"{PERSISTED_TAG}\n"
        f"输出太大（{size_kb}KB），完整内容已保存到：\n"
        f"{file_path}\n\n"
        f"预览（前 2KB）：\n"
        f"{content[:PREVIEW_CHARS]}\n"
        f"</persisted-output>"
    )


def _count_turns(messages: list[Message]) -> int:
    return sum(1 for message in messages if message.role == "assistant" and not message.tool_uses)


def _copy_message_with_results(
    msg: Message,
    new_tool_results: list[ToolResultBlock],
) -> Message:
    return Message(
        role=msg.role,
        content=msg.content,
        tool_uses=msg.tool_uses,
        tool_results=new_tool_results,
        thinking_blocks=msg.thinking_blocks,
    )


def _snip_preview(content: str) -> str:
    head = content[:200]
    return f"{SNIPPED_TAG}\n(旧结果已裁剪，原始长度 {len(content)} 字符)\n{head}\n… (snipped)"


def _snip_stale_messages(history: list[Message]) -> list[Message]:
    total_turns = _count_turns(history)
    if total_turns <= KEEP_RECENT_TURNS:
        return history

    # Walk from the end; keep the last KEEP_RECENT_TURNS pure-assistant turns.
    keep_from = 0
    turns_seen = 0
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if message.role == "assistant" and not message.tool_uses:
            turns_seen += 1
            if turns_seen >= KEEP_RECENT_TURNS:
                keep_from = index
                break

    result: list[Message] = []
    for index, message in enumerate(history):
        if index >= keep_from or not message.tool_results:
            result.append(message)
            continue
        new_results: list[ToolResultBlock] = []
        changed = False
        for tr in message.tool_results:
            content = tr.content
            if (
                len(content) > OLD_RESULT_SNIP_CHARS
                and not content.startswith(PERSISTED_TAG)
                and not content.startswith(SNIPPED_TAG)
            ):
                new_results.append(
                    ToolResultBlock(tr.tool_use_id, _snip_preview(content), tr.is_error)
                )
                changed = True
            else:
                new_results.append(tr)
        result.append(_copy_message_with_results(message, new_results) if changed else message)
    return result


def apply_tool_result_budget(
    conversation: ConversationManager,
    session_dir: str | Path,
    state: ContentReplacementState,
) -> tuple[ConversationManager, list[ContentReplacementRecord]]:
    """Return a new conversation with tool-result budgets applied (input is immutable)."""
    decisions: dict[str, str] = {}
    new_records: list[ContentReplacementRecord] = []
    fresh: list[tuple[str, str, bool]] = []  # id, content, is_error (is_error unused later)

    # Stage 1: classify every tool result
    for message in conversation.history:
        for tr in message.tool_results:
            tool_id = tr.tool_use_id
            content = tr.content
            if tool_id in state.replacements:
                decisions[tool_id] = state.replacements[tool_id]
            elif tool_id in state.seen_ids:
                decisions[tool_id] = content
            elif content.startswith(PERSISTED_TAG):
                state.seen_ids.add(tool_id)
                state.replacements[tool_id] = content
                decisions[tool_id] = content
                new_records.append(
                    ContentReplacementRecord(tool_use_id=tool_id, replacement=content)
                )
            else:
                fresh.append((tool_id, content, tr.is_error))

    # Stage 2 (Pass 1): single-result overflow → persist
    remaining_fresh: list[tuple[str, str]] = []
    for tool_id, content, _is_error in fresh:
        if len(content) > SINGLE_RESULT_CHAR_LIMIT:
            path = persist_tool_result(tool_id, content, session_dir)
            preview = make_persisted_preview(content, path)
            state.seen_ids.add(tool_id)
            state.replacements[tool_id] = preview
            decisions[tool_id] = preview
            new_records.append(ContentReplacementRecord(tool_use_id=tool_id, replacement=preview))
        else:
            remaining_fresh.append((tool_id, content))

    # Stage 3 (Pass 2): aggregate overflow — pick largest fresh first
    pool = list(remaining_fresh)
    while pool:
        total = sum(len(value) for value in decisions.values()) + sum(
            len(content) for _, content in pool
        )
        if total <= AGGREGATE_CHAR_LIMIT:
            break
        pool.sort(key=lambda item: len(item[1]), reverse=True)
        tool_id, content = pool.pop(0)
        path = persist_tool_result(tool_id, content, session_dir)
        preview = make_persisted_preview(content, path)
        state.seen_ids.add(tool_id)
        state.replacements[tool_id] = preview
        decisions[tool_id] = preview
        new_records.append(ContentReplacementRecord(tool_use_id=tool_id, replacement=preview))

    # Stage 4: freeze undecided fresh as keep-original
    for tool_id, content in pool:
        state.seen_ids.add(tool_id)
        decisions[tool_id] = content

    # Build new history (do not mutate input)
    new_history: list[Message] = []
    for message in conversation.history:
        if not message.tool_results:
            new_history.append(message)
            continue
        new_results = [
            ToolResultBlock(
                tool_use_id=tr.tool_use_id,
                content=decisions.get(tr.tool_use_id, tr.content),
                is_error=tr.is_error,
            )
            for tr in message.tool_results
        ]
        new_history.append(_copy_message_with_results(message, new_results))

    new_history = _snip_stale_messages(new_history)
    api_conv = ConversationManager(history=list(new_history))
    api_conv.env_injected = conversation.env_injected
    api_conv.ltm_injected = conversation.ltm_injected
    api_conv.last_input_tokens = conversation.last_input_tokens
    return api_conv, new_records


# ---------------------------------------------------------------------------
# Layer 2: threshold + summary helpers
# ---------------------------------------------------------------------------


def compute_compact_threshold(context_window: int, manual: bool = False) -> int:
    margin = MANUAL_COMPACT_SAFETY_MARGIN if manual else AUTO_COMPACT_SAFETY_MARGIN
    return context_window - SUMMARY_OUTPUT_RESERVE - margin


def should_auto_compact(last_input_tokens: int, context_window: int) -> bool:
    return last_input_tokens >= compute_compact_threshold(context_window, manual=False)


SUMMARY_PROMPT = """\
你是对话压缩助手。你的唯一任务是根据提供的对话历史生成结构化摘要。

【严禁】你不得调用任何工具。不要输出 tool_use / function_call。只输出文本。

请先在 <analysis>…</analysis> 中写下简短分析草稿（用完即弃，正式摘要中不要引用草稿标签）。
然后输出正式摘要，必须包裹在 <summary>…</summary> 中，并严格按以下九节组织：

1. 主要请求：用户的核心目标与约束
2. 关键概念：领域术语、约定、关键决策
3. 文件与代码段：已读/已改路径与关键片段（保留路径原文）
4. 错误与修复：报错信息与已尝试/已成功的修复
5. 解决过程：关键步骤与中间结论
6. 用户原话：必须原文保留的用户指令/约束（禁止改写）
7. 待办：尚未完成的事项
8. 当前工作：压缩前正在做的事
9. 下一步：明确的后续动作

规则：
- 用户原始消息尽量原文保留，不得摘要改写用户原话
- 文件路径、错误信息、命令、标识符保持原样
- 不要编造对话中不存在的文件、代码或结论
- 若某节无内容写「无」

【再次强调】禁止调用任何工具；只输出 <analysis> 与 <summary> 文本。
"""

COMPACT_BOUNDARY_MESSAGE = (
    "上下文已压缩。以上摘要替换了更早的对话历史。"
    "若需要文件、工具输出或代码的精确细节，请重新读取对应文件或路径，"
    "不要根据摘要脑补不存在的代码或内容。"
)


def extract_summary(llm_output: str) -> str:
    start = llm_output.find("<summary>")
    end = llm_output.find("</summary>")
    if start != -1 and end != -1 and end > start:
        return llm_output[start + len("<summary>") : end].strip()
    return llm_output


def build_compact_messages(summary: str, attachment: str = "") -> list[Message]:
    user_body = f"[摘要]\n{summary}"
    if attachment:
        user_body = f"{user_body}\n\n---\n\n{attachment}"
    return [
        Message(role="user", content=user_body),
        Message(role="assistant", content=COMPACT_BOUNDARY_MESSAGE),
    ]


def _group_messages_by_turn(messages: list[Message]) -> list[list[Message]]:
    turns: list[list[Message]] = []
    current: list[Message] = []
    for message in messages:
        current.append(message)
        if message.role == "assistant" and not message.tool_uses:
            turns.append(current)
            current = []
    if current:
        turns.append(current)
    return turns


@dataclass
class CompactCircuitBreaker:
    max_failures: int = 3
    consecutive_failures: int = field(init=False, default=0)

    def record_failure(self) -> None:
        self.consecutive_failures += 1

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def is_open(self) -> bool:
        return self.consecutive_failures >= self.max_failures


# ---------------------------------------------------------------------------
# Recovery state (post-compact)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FileReadRecord:
    path: str
    content: str
    timestamp: float


@dataclass(slots=True)
class SkillInvocationRecord:
    name: str
    body: str
    timestamp: float


class RecoveryState:
    """Thread-safe snapshots of recent file reads and skill invocations."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._files: dict[str, FileReadRecord] = {}
        self._skills: dict[str, SkillInvocationRecord] = {}
        self._seq = 0

    def _next_ts(self) -> float:
        # Monotonic-ish timestamp so rapid successive writes still order correctly.
        self._seq += 1
        return time.time() + self._seq * 1e-6

    def record_file_read(self, path: str, content: str) -> None:
        if not path:
            return
        with self._lock:
            self._files[path] = FileReadRecord(path, content, self._next_ts())

    def record_skill_invocation(self, name: str, body: str) -> None:
        if not name:
            return
        with self._lock:
            self._skills[name] = SkillInvocationRecord(name, body, self._next_ts())

    def snapshot_files(self, limit: int = RECOVERY_FILE_LIMIT) -> list[FileReadRecord]:
        with self._lock:
            items = list(self._files.values())
        items.sort(key=lambda item: item.timestamp, reverse=True)
        return items[:limit]

    def snapshot_skills(self) -> list[SkillInvocationRecord]:
        with self._lock:
            items = list(self._skills.values())
        items.sort(key=lambda item: item.timestamp, reverse=True)
        return items


def _approx_tokens(text: str) -> float:
    return len(text) / _RECOVERY_CHARS_PER_TOKEN


def _truncate_by_tokens(text: str, token_budget: int) -> str:
    max_chars = int(token_budget * _RECOVERY_CHARS_PER_TOKEN)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n… (内容已截断)"


def _first_line(text: str) -> str:
    line = text.splitlines()[0] if text else ""
    return line[:120]


def build_recovery_attachment(
    state: RecoveryState | None,
    tool_schemas: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    if state is None and not tool_schemas:
        return ""

    sections: list[str] = []

    if state is not None:
        files = state.snapshot_files(RECOVERY_FILE_LIMIT)
        if files:
            lines = ["## 最近读过的文件"]
            for file_record in files:
                body = _truncate_by_tokens(
                    file_record.content,
                    RECOVERY_TOKENS_PER_FILE,
                )
                lines.append(f"### {file_record.path}\n{body}")
            sections.append("\n".join(lines))

        skills = state.snapshot_skills()
        if skills:
            lines = ["## 已激活的技能"]
            used_tokens = 0.0
            for skill_record in skills:
                body = _truncate_by_tokens(
                    skill_record.body,
                    RECOVERY_TOKENS_PER_SKILL,
                )
                cost = _approx_tokens(body)
                if used_tokens + cost > RECOVERY_SKILLS_BUDGET:
                    break
                lines.append(f"### {skill_record.name}\n{body}")
                used_tokens += cost
            if len(lines) > 1:
                sections.append("\n".join(lines))

    if tool_schemas:
        lines = ["## 可用工具"]
        for schema in tool_schemas:
            name = str(schema.get("name") or schema.get("function", {}).get("name") or "?")
            description = str(
                schema.get("description") or schema.get("function", {}).get("description") or ""
            )
            lines.append(f"- {name}: {_first_line(description)}")
        sections.append("\n".join(lines))

    if sections:
        sections.append(
            "## 提示\n"
            "以上为压缩后恢复的工作记忆。需要精确内容时请重新读取文件，"
            "不要根据摘要臆造代码。"
        )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Layer 2: auto_compact
# ---------------------------------------------------------------------------


def is_prompt_too_long(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        needle in text
        for needle in (
            "prompt is too long",
            "prompt_too_long",
            "context_length_exceeded",
            "maximum context length",
            "too many tokens",
        )
    )


async def auto_compact(
    conversation: ConversationManager,
    client: Any,
    context_window: int,
    session_dir: str | Path,
    protocol: str = "anthropic",
    manual: bool = False,
    breaker: CompactCircuitBreaker | None = None,
    recovery: RecoveryState | None = None,
    tool_schemas: Sequence[Mapping[str, Any]] | None = None,
) -> CompactEvent | str | None:
    """Summarize conversation when over threshold. Mutates conversation on success."""
    before_tokens = max(
        conversation.last_input_tokens,
        estimate_conversation_tokens(conversation),
    )
    conversation.last_input_tokens = before_tokens
    threshold = compute_compact_threshold(context_window, manual=manual)

    if not manual and before_tokens < threshold:
        return None
    if manual and not conversation.history:
        return None
    if not manual and breaker is not None and breaker.is_open():
        return "Context compaction circuit breaker is open; auto-compact disabled."

    source_messages = list(conversation.history)
    max_attempts = 3
    last_error: str | None = None

    for attempt in range(max_attempts):
        if attempt > 0:
            turns = _group_messages_by_turn(source_messages)
            if len(turns) <= 1:
                break
            drop = max(1, len(turns) // 5)
            source_messages = [message for turn in turns[drop:] for message in turn]

        summary_conv = ConversationManager()
        summary_conv.add_user_message(
            "请根据以下对话历史生成结构化摘要。再次提醒：禁止调用任何工具。\n\n"
            "===== 对话历史开始 ====="
        )
        for message in source_messages:
            summary_conv.history.append(message)
        summary_conv.add_user_message(
            "===== 对话历史结束 =====\n"
            "请输出 <analysis> 草稿，然后输出 <summary> 正式摘要。"
            "禁止调用任何工具。"
        )

        parts: list[str] = []
        try:
            stream = client.stream(summary_conv, system=SUMMARY_PROMPT, tools=None)
            async for event in stream:
                if isinstance(event, TextDelta):
                    parts.append(event.text)
        except Exception as exc:  # noqa: BLE001 — compact boundary
            if is_prompt_too_long(exc) and attempt + 1 < max_attempts:
                last_error = str(exc)
                continue
            if breaker is not None:
                breaker.record_failure()
            return f"Context compaction failed: {type(exc).__name__}: {exc}"

        raw = "".join(parts).strip()
        if not raw:
            if breaker is not None:
                breaker.record_failure()
            return "Context compaction failed: empty summary from model."

        summary = extract_summary(raw)
        attachment = build_recovery_attachment(recovery, tool_schemas)
        conversation.replace_history(build_compact_messages(summary, attachment=attachment))
        after_tokens = estimate_conversation_tokens(conversation)
        conversation.last_input_tokens = after_tokens
        cleanup_tool_results(session_dir)
        if breaker is not None:
            breaker.record_success()
        return CompactEvent(before_tokens=before_tokens, after_tokens=after_tokens)

    if breaker is not None:
        breaker.record_failure()
    return f"Context compaction failed after retries: {last_error or 'prompt too long'}"


async def manual_compact(
    conversation: ConversationManager,
    client: Any,
    context_window: int,
    session_dir: str | Path,
    protocol: str = "anthropic",
    breaker: CompactCircuitBreaker | None = None,
    recovery: RecoveryState | None = None,
    tool_schemas: Sequence[Mapping[str, Any]] | None = None,
) -> CompactEvent | str | None:
    return await auto_compact(
        conversation,
        client,
        context_window,
        session_dir,
        protocol=protocol,
        manual=True,
        breaker=breaker,
        recovery=recovery,
        tool_schemas=tool_schemas,
    )
