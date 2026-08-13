"""Post-compact recovery attachment tests."""

from __future__ import annotations

from simplecode.context import (
    RECOVERY_FILE_LIMIT,
    RECOVERY_TOKENS_PER_FILE,
    RecoveryState,
    build_compact_messages,
    build_recovery_attachment,
)
from simplecode.context.manager import _approx_tokens, _truncate_by_tokens


def test_recovery_attachment_empty_when_nothing_recorded() -> None:
    state = RecoveryState()
    assert build_recovery_attachment(state, None) == ""
    assert build_recovery_attachment(None, None) == ""
    assert build_recovery_attachment(None, []) == ""


def test_recovery_attachment_emits_all_sections() -> None:
    state = RecoveryState()
    state.record_file_read("src/a.py", "print(1)")
    state.record_skill_invocation("commit", "Write a good commit message.")
    schemas = [{"name": "ReadFile", "description": "Read a file from disk"}]
    text = build_recovery_attachment(state, schemas)
    assert "## 最近读过的文件" in text
    assert "src/a.py" in text
    assert "## 已激活的技能" in text
    assert "commit" in text
    assert "## 可用工具" in text
    assert "ReadFile" in text
    assert "## 提示" in text

    messages = build_compact_messages("summary body", attachment=text)
    assert "---" in messages[0].content
    assert "summary body" in messages[0].content
    assert "src/a.py" in messages[0].content


def test_recovery_file_limit_and_order() -> None:
    state = RecoveryState()
    for i in range(RECOVERY_FILE_LIMIT + 3):
        state.record_file_read(f"f{i}.py", f"content-{i}")
    files = state.snapshot_files(RECOVERY_FILE_LIMIT)
    assert len(files) == RECOVERY_FILE_LIMIT
    # Most recent first
    assert files[0].path == f"f{RECOVERY_FILE_LIMIT + 2}.py"


def test_recovery_truncates_per_file() -> None:
    state = RecoveryState()
    huge = "Z" * int(RECOVERY_TOKENS_PER_FILE * 3.5 * 2)
    state.record_file_read("big.py", huge)
    text = build_recovery_attachment(state, None)
    assert "… (内容已截断)" in text
    assert len(text) < len(huge)


def test_recovery_skills_budget() -> None:
    state = RecoveryState()
    # Each skill body ~ 5k tokens of content so only a few fit in 25k budget.
    body = "S" * int(5_000 * 3.5)
    for i in range(10):
        state.record_skill_invocation(f"skill-{i}", body)
    text = build_recovery_attachment(state, None)
    # Should stop before all 10 skills are included.
    included = sum(1 for i in range(10) if f"skill-{i}" in text)
    assert 0 < included < 10


def test_truncate_helpers() -> None:
    assert _approx_tokens("abcd") > 0
    short = _truncate_by_tokens("hi", 100)
    assert short == "hi"
    long = _truncate_by_tokens("x" * 10_000, 10)
    assert long.endswith("… (内容已截断)")


def test_empty_path_and_name_ignored() -> None:
    state = RecoveryState()
    state.record_file_read("", "x")
    state.record_skill_invocation("", "y")
    assert state.snapshot_files() == []
    assert state.snapshot_skills() == []
