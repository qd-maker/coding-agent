"""Context budgeting, Layer 1/2 compact, and session helpers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from simplecode.agent import CompactNotification
from simplecode.client import LLMClient
from simplecode.commands import CommandContext
from simplecode.commands.handlers.compact import handle_compact
from simplecode.config import ProviderConfig
from simplecode.context import (
    AGGREGATE_CHAR_LIMIT,
    AUTO_COMPACT_SAFETY_MARGIN,
    COMPACT_BOUNDARY_MESSAGE,
    MANUAL_COMPACT_SAFETY_MARGIN,
    PERSISTED_TAG,
    PREVIEW_CHARS,
    SINGLE_RESULT_CHAR_LIMIT,
    SNIPPED_TAG,
    SUMMARY_OUTPUT_RESERVE,
    SUMMARY_PROMPT,
    CompactCircuitBreaker,
    CompactEvent,
    apply_tool_result_budget,
    auto_compact,
    build_compact_messages,
    cleanup_tool_results,
    compute_compact_threshold,
    create_replacement_state,
    ensure_session_dir,
    estimate_conversation_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
    extract_summary,
    make_persisted_preview,
    persist_tool_result,
    should_auto_compact,
)
from simplecode.conversation import (
    ConversationManager,
    Message,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from simplecode.tools.base import StreamEnd, StreamEvent, TextDelta


def _provider() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "protocol": "anthropic",
            "model": "claude-sonnet-4-6",
            "base_url": "https://api.anthropic.com",
            "api_key": "test-key",
        }
    )


class FakeSummaryClient(LLMClient):
    def __init__(self, text: str = "<summary>\n主要请求: hello\n</summary>") -> None:
        super().__init__(_provider())
        self.text = text
        self.calls = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        yield TextDelta(self.text)
        yield StreamEnd("end_turn", input_tokens=10, output_tokens=5)


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def test_estimate_text_tokens_handles_ascii_and_cjk() -> None:
    assert estimate_text_tokens("") == 0
    assert estimate_text_tokens("a" * 8) == 2
    assert estimate_text_tokens("你好世界") == 4
    assert estimate_text_tokens("abcd你好") == 3


def test_estimate_message_and_conversation_include_structured_blocks() -> None:
    plain = Message(role="user", content="hello")
    structured = Message(
        role="assistant",
        content="hello",
        thinking_blocks=[ThinkingBlock("reasoning", "signature")],
        tool_uses=[ToolUseBlock("call-1", "Bash", {"command": "echo hi"})],
    )
    result_message = Message(
        role="user",
        tool_results=[ToolResultBlock("call-1", "output" * 20)],
    )
    conversation = ConversationManager([structured, result_message])

    assert estimate_message_tokens(structured) > estimate_message_tokens(plain)
    assert estimate_conversation_tokens(conversation) > estimate_message_tokens(structured)
    assert estimate_conversation_tokens(ConversationManager()) == 0


class FailThenSucceedClient(LLMClient):
    def __init__(self) -> None:
        super().__init__(_provider())
        self.calls = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("prompt is too long")
        yield TextDelta("<summary>ok after drop</summary>")
        yield StreamEnd("end_turn", 1, 1)


# ---------------------------------------------------------------------------
# Session / persist / preview
# ---------------------------------------------------------------------------


class TestSessionDir:
    def test_ensure_and_cleanup(self, tmp_path: Path) -> None:
        session = ensure_session_dir(tmp_path)
        assert session.is_dir()
        assert session.name == "tool-results"
        (session / "a.txt").write_text("x", encoding="utf-8")
        cleanup_tool_results(session)
        assert session.is_dir()
        assert list(session.iterdir()) == []


class TestPersistToolResult:
    def test_write_and_idempotent(self, tmp_path: Path) -> None:
        path = persist_tool_result("tool_1", "hello world", tmp_path)
        assert path.read_text(encoding="utf-8") == "hello world"
        # second write is silent (O_EXCL)
        path2 = persist_tool_result("tool_1", "changed", tmp_path)
        assert path2 == path
        assert path.read_text(encoding="utf-8") == "hello world"


class TestMakePersistedPreview:
    def test_format(self, tmp_path: Path) -> None:
        content = "A" * 3000
        path = tmp_path / "out.txt"
        preview = make_persisted_preview(content, path)
        assert preview.startswith(PERSISTED_TAG)
        assert str(path) in preview
        assert content[:PREVIEW_CHARS] in preview
        assert "</persisted-output>" in preview


# ---------------------------------------------------------------------------
# Layer 1
# ---------------------------------------------------------------------------


def _conv_with_results(*sizes: int) -> ConversationManager:
    conversation = ConversationManager()
    conversation.add_user_message("go")
    uses = [
        ToolUseBlock(tool_id=f"t{i}", name="Bash", input={"command": "x"})
        for i in range(len(sizes))
    ]
    conversation.add_assistant_message("working", tool_uses=uses)
    results = [
        ToolResultBlock(tool_use_id=f"t{i}", content="X" * size) for i, size in enumerate(sizes)
    ]
    conversation.add_tool_results_message(results)
    return conversation


class TestApplyToolResultBudget:
    def test_apply_does_not_mutate_conv(self, tmp_path: Path) -> None:
        conversation = _conv_with_results(SINGLE_RESULT_CHAR_LIMIT + 10)
        original = conversation.history[-1].tool_results[0].content
        state = create_replacement_state()
        api_conv, records = apply_tool_result_budget(conversation, tmp_path, state)
        assert conversation.history[-1].tool_results[0].content == original
        assert api_conv.history[-1].tool_results[0].content.startswith(PERSISTED_TAG)
        assert records

    def test_first_call_freezes_unreplaced(self, tmp_path: Path) -> None:
        conversation = _conv_with_results(100)
        state = create_replacement_state()
        apply_tool_result_budget(conversation, tmp_path, state)
        assert "t0" in state.seen_ids
        assert "t0" not in state.replacements

    def test_replacement_byte_identical(self, tmp_path: Path) -> None:
        conversation = _conv_with_results(SINGLE_RESULT_CHAR_LIMIT + 50)
        state = create_replacement_state()
        api1, _ = apply_tool_result_budget(conversation, tmp_path, state)
        content1 = api1.history[-1].tool_results[0].content
        api2, records2 = apply_tool_result_budget(conversation, tmp_path, state)
        content2 = api2.history[-1].tool_results[0].content
        assert content1 == content2
        assert records2 == []

    def test_frozen_never_replaced(self, tmp_path: Path) -> None:
        conversation = _conv_with_results(100)
        state = create_replacement_state()
        apply_tool_result_budget(conversation, tmp_path, state)
        # Even if content later grows in history, frozen id keeps original decision.
        conversation.history[-1].tool_results[0] = ToolResultBlock(
            "t0", "Y" * (SINGLE_RESULT_CHAR_LIMIT + 20)
        )
        api_conv, _ = apply_tool_result_budget(conversation, tmp_path, state)
        assert api_conv.history[-1].tool_results[0].content == "Y" * (
            SINGLE_RESULT_CHAR_LIMIT + 20
        ) or api_conv.history[-1].tool_results[0].content.startswith("X")
        # seen freeze keeps original content from first call
        assert "t0" in state.seen_ids
        assert "t0" not in state.replacements
        # A different value on a seen id remains as-is; the prior decision is frozen.
        assert not api_conv.history[-1].tool_results[0].content.startswith(PERSISTED_TAG)

    def test_aggregate_only_picks_fresh(self, tmp_path: Path) -> None:
        # 5 results of 4500 chars = 22500 > AGGREGATE 20000
        sizes = [4_500] * 5
        conversation = _conv_with_results(*sizes)
        state = create_replacement_state()
        api_conv, records = apply_tool_result_budget(conversation, tmp_path, state)
        persisted = [
            tr
            for tr in api_conv.history[-1].tool_results
            if tr.content.startswith(PERSISTED_TAG)
        ]
        assert persisted
        assert records
        total = sum(len(tr.content) for tr in api_conv.history[-1].tool_results)
        assert total <= AGGREGATE_CHAR_LIMIT or any(
            tr.content.startswith(PERSISTED_TAG) for tr in api_conv.history[-1].tool_results
        )


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


class TestComputeCompactThreshold:
    def test_auto_and_manual(self) -> None:
        assert compute_compact_threshold(200_000) == 167_000
        assert compute_compact_threshold(200_000, manual=True) == 177_000
        assert compute_compact_threshold(128_000) == 95_000
        assert (
            compute_compact_threshold(200_000)
            == 200_000 - SUMMARY_OUTPUT_RESERVE - AUTO_COMPACT_SAFETY_MARGIN
        )
        assert (
            compute_compact_threshold(200_000, manual=True)
            == 200_000 - SUMMARY_OUTPUT_RESERVE - MANUAL_COMPACT_SAFETY_MARGIN
        )


class TestShouldAutoCompact:
    def test_boundaries(self) -> None:
        threshold = compute_compact_threshold(200_000)
        assert should_auto_compact(threshold, 200_000) is True
        assert should_auto_compact(threshold - 1, 200_000) is False


# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


class TestExtractSummary:
    def test_extracts_inner(self) -> None:
        raw = "<analysis>draft</analysis>\n<summary>\n  Hello world  \n</summary>"
        assert extract_summary(raw) == "Hello world"

    def test_missing_tags_returns_all(self) -> None:
        assert extract_summary("plain text only") == "plain text only"


class TestBuildCompactMessages:
    def test_pair_and_attachment(self) -> None:
        messages = build_compact_messages("body", attachment="## 提示\nre-read")
        assert len(messages) == 2
        assert messages[0].role == "user"
        assert messages[0].content.startswith("[摘要]\nbody")
        assert "---" in messages[0].content
        assert messages[1].content == COMPACT_BOUNDARY_MESSAGE


class TestSummaryPrompt:
    def test_forbids_tools_and_has_nine_sections(self) -> None:
        assert "禁止" in SUMMARY_PROMPT or "严禁" in SUMMARY_PROMPT
        assert SUMMARY_PROMPT.count("工具") >= 2
        assert "<analysis>" in SUMMARY_PROMPT
        assert "<summary>" in SUMMARY_PROMPT
        for section in (
            "主要请求",
            "关键概念",
            "文件与代码",
            "错误与修复",
            "解决过程",
            "用户原话",
            "待办",
            "当前工作",
            "下一步",
        ):
            assert section in SUMMARY_PROMPT


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestCompactCircuitBreaker:
    def test_open_after_max_failures(self) -> None:
        breaker = CompactCircuitBreaker(max_failures=3)
        assert breaker.is_open() is False
        breaker.record_failure()
        breaker.record_failure()
        assert breaker.is_open() is False
        breaker.record_failure()
        assert breaker.is_open() is True
        breaker.record_success()
        assert breaker.is_open() is False


# ---------------------------------------------------------------------------
# Layer 2 auto_compact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_compact_skips_when_below_threshold(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_user_message("small")
    conversation.last_input_tokens = 100
    client = FakeSummaryClient()
    result = await auto_compact(
        conversation, client, 200_000, tmp_path, breaker=CompactCircuitBreaker()
    )
    assert result is None
    assert client.calls == 0


@pytest.mark.asyncio
async def test_auto_compact_replaces_history(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_user_message("do work")
    conversation.add_assistant_message("done")
    conversation.last_input_tokens = compute_compact_threshold(200_000) + 1
    client = FakeSummaryClient("<summary>主要请求: do work</summary>")
    result = await auto_compact(
        conversation, client, 200_000, tmp_path, breaker=CompactCircuitBreaker()
    )
    assert isinstance(result, CompactEvent)
    assert result.before_tokens == compute_compact_threshold(200_000) + 1
    assert result.after_tokens == conversation.last_input_tokens
    assert result.after_tokens < result.before_tokens
    assert len(conversation.history) == 2
    assert conversation.history[0].content.startswith("[摘要]")
    assert conversation.history[1].content == COMPACT_BOUNDARY_MESSAGE


@pytest.mark.asyncio
async def test_auto_compact_breaker_open(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_user_message("x")
    conversation.last_input_tokens = 999_999
    breaker = CompactCircuitBreaker(max_failures=1)
    breaker.record_failure()
    client = FakeSummaryClient()
    result = await auto_compact(conversation, client, 200_000, tmp_path, breaker=breaker)
    assert isinstance(result, str)
    assert "circuit breaker" in result.lower() or "open" in result.lower()
    assert client.calls == 0


@pytest.mark.asyncio
async def test_auto_compact_ptl_retry(tmp_path: Path) -> None:
    conversation = ConversationManager()
    for i in range(10):
        conversation.add_user_message(f"u{i}")
        conversation.add_assistant_message(f"a{i}")
    conversation.last_input_tokens = 999_999
    client = FailThenSucceedClient()
    result = await auto_compact(
        conversation, client, 200_000, tmp_path, breaker=CompactCircuitBreaker()
    )
    assert isinstance(result, CompactEvent)
    assert client.calls == 2


@pytest.mark.asyncio
async def test_manual_compact_ignores_threshold(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_user_message("please compact")
    conversation.add_assistant_message("ok")
    conversation.last_input_tokens = 10  # far below auto threshold
    client = FakeSummaryClient("<summary>manual</summary>")
    result = await auto_compact(
        conversation,
        client,
        200_000,
        tmp_path,
        manual=True,
        breaker=CompactCircuitBreaker(),
    )
    assert isinstance(result, CompactEvent)
    assert client.calls == 1


@pytest.mark.asyncio
async def test_auto_compact_can_trigger_from_local_estimate(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_user_message("A" * 4_500)
    assert conversation.last_input_tokens == 0
    client = FakeSummaryClient("<summary>estimated trigger</summary>")

    result = await auto_compact(
        conversation,
        client,
        34_000,  # automatic threshold is 1,000 tokens
        tmp_path,
        breaker=CompactCircuitBreaker(),
    )

    assert isinstance(result, CompactEvent)
    assert result.before_tokens >= 1_000
    assert result.after_tokens == conversation.last_input_tokens
    assert client.calls == 1


@pytest.mark.asyncio
async def test_compact_command_reports_before_and_after_for_short_conversation() -> None:
    conversation = ConversationManager()
    conversation.add_user_message("short but user requested compact")

    class StubAgent:
        async def manual_compact(
            self, target: ConversationManager
        ) -> CompactNotification:
            assert target is conversation
            return CompactNotification(before_tokens=12_345, after_tokens=1_234)

    messages: list[str] = []

    class StubUI:
        async def add_system_message(self, text: str) -> None:
            messages.append(text)

        async def send_user_message(self, text: str) -> None:
            del text

        def set_plan_mode(self, enabled: bool) -> None:
            del enabled

        def get_token_count(self) -> int:
            return 0

        def refresh_status(self) -> None:
            return

    context = CommandContext(
        args="",
        agent=StubAgent(),
        conversation=conversation,
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=StubUI(),
    )
    await handle_compact(context)

    assert "12,345" in messages[-1]
    assert "1,234" in messages[-1]
    assert "→" in messages[-1]


def test_snip_tag_constant() -> None:
    assert SNIPPED_TAG == "<snipped>"
