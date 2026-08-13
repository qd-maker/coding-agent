"""ContentReplacementState factories, JSONL transcript, and reconstruct."""

from __future__ import annotations

from pathlib import Path

from simplecode.context import (
    ContentReplacementRecord,
    append_replacement_records,
    apply_tool_result_budget,
    clone_replacement_state,
    create_replacement_state,
    load_replacement_records,
    reconstruct_replacement_state,
)
from simplecode.conversation import ConversationManager, Message, ToolResultBlock


def test_create_returns_empty() -> None:
    state = create_replacement_state()
    assert state.seen_ids == set()
    assert state.replacements == {}


def test_clone_independent() -> None:
    src = create_replacement_state()
    src.seen_ids.add("a")
    src.replacements["a"] = "preview"
    cloned = clone_replacement_state(src)
    cloned.seen_ids.add("b")
    cloned.replacements["b"] = "other"
    assert "b" not in src.seen_ids
    assert "b" not in src.replacements
    src.replacements["a"] = "changed"
    assert cloned.replacements["a"] == "preview"


def test_append_and_load_records_roundtrip(tmp_path: Path) -> None:
    records = [
        ContentReplacementRecord(tool_use_id="t1", replacement="<persisted-output>x"),
        ContentReplacementRecord(tool_use_id="t2", replacement="<persisted-output>y"),
    ]
    append_replacement_records(tmp_path, [])
    assert load_replacement_records(tmp_path) == []
    append_replacement_records(tmp_path, records)
    loaded = load_replacement_records(tmp_path)
    assert len(loaded) == 2
    assert loaded[0].tool_use_id == "t1"
    assert loaded[1].replacement == "<persisted-output>y"
    assert loaded[0].kind == "tool-result"


def test_reconstruct_from_records() -> None:
    messages = [
        Message(
            role="user",
            tool_results=[
                ToolResultBlock("t1", "full1"),
                ToolResultBlock("t2", "full2"),
            ],
        )
    ]
    records = [
        ContentReplacementRecord("t1", "preview1"),
        ContentReplacementRecord("t3", "orphan"),  # not in messages → ignored
    ]
    state = reconstruct_replacement_state(messages, records)
    assert state.seen_ids == {"t1", "t2"}
    assert state.replacements == {"t1": "preview1"}


def test_reconstruct_with_inherited_parent() -> None:
    messages = [
        Message(
            role="user",
            tool_results=[
                ToolResultBlock("t1", "a"),
                ToolResultBlock("t2", "b"),
            ],
        )
    ]
    records = [ContentReplacementRecord("t1", "from-record")]
    inherited = {"t1": "from-parent", "t2": "from-parent-2"}
    state = reconstruct_replacement_state(messages, records, inherited_replacements=inherited)
    # records win for t1; inherited gap-fills t2
    assert state.replacements["t1"] == "from-record"
    assert state.replacements["t2"] == "from-parent-2"


def test_apply_does_not_mutate_conv(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_user_message("hi")
    conversation.add_tool_results_message(
        [ToolResultBlock("big", "Z" * 6_000)]
    )
    original = conversation.history[-1].tool_results[0].content
    state = create_replacement_state()
    api_conv, _ = apply_tool_result_budget(conversation, tmp_path, state)
    assert conversation.history[-1].tool_results[0].content is original or (
        conversation.history[-1].tool_results[0].content == original
    )
    assert api_conv is not conversation
    assert api_conv.history[-1].tool_results[0].content.startswith("<persisted-output>")


def test_first_call_freezes_unreplaced(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_tool_results_message([ToolResultBlock("small", "ok")])
    state = create_replacement_state()
    apply_tool_result_budget(conversation, tmp_path, state)
    assert "small" in state.seen_ids
    assert "small" not in state.replacements


def test_replacement_byte_identical(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_tool_results_message([ToolResultBlock("big", "Q" * 6_000)])
    state = create_replacement_state()
    a1, r1 = apply_tool_result_budget(conversation, tmp_path, state)
    a2, r2 = apply_tool_result_budget(conversation, tmp_path, state)
    assert a1.history[-1].tool_results[0].content == a2.history[-1].tool_results[0].content
    assert r1 and not r2


def test_frozen_never_replaced(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_tool_results_message([ToolResultBlock("s", "short")])
    state = create_replacement_state()
    apply_tool_result_budget(conversation, tmp_path, state)
    # Second pass must not invent a replacement for a frozen keep.
    api_conv, records = apply_tool_result_budget(conversation, tmp_path, state)
    assert records == []
    assert "s" not in state.replacements
    assert api_conv.history[-1].tool_results[0].content == "short"


def test_aggregate_only_picks_fresh(tmp_path: Path) -> None:
    conversation = ConversationManager()
    results = [ToolResultBlock(f"t{i}", "A" * 4_500) for i in range(5)]
    conversation.add_tool_results_message(results)
    state = create_replacement_state()
    api_conv, records = apply_tool_result_budget(conversation, tmp_path, state)
    assert records
    # At least one persisted; already-seen on next call only re-reads replacements.
    persisted_ids = {
        tr.tool_use_id
        for tr in api_conv.history[-1].tool_results
        if tr.content.startswith("<persisted-output>")
    }
    assert persisted_ids
    assert set(state.replacements) == persisted_ids

    api2, records2 = apply_tool_result_budget(conversation, tmp_path, state)
    assert records2 == []
    for tr in api2.history[-1].tool_results:
        if tr.tool_use_id in persisted_ids:
            assert tr.content == state.replacements[tr.tool_use_id]
