"""Completion gate and structured evidence tests."""

from __future__ import annotations

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from mewcode.agent import Agent, ErrorEvent, LoopComplete, VerificationEvent
from mewcode.client import LLMClient
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.evidence import RunEvidenceTracker, classify_task_intent
from mewcode.tools import create_default_registry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete, ToolResult


class ScriptedClient(LLMClient):
    def __init__(self, responses: list[list[StreamEvent]]) -> None:
        super().__init__(
            ProviderConfig(
                protocol="anthropic",
                model="test-model",
                base_url="https://example.invalid",
                api_key="test-key",
            )
        )
        self.responses = list(responses)

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del conversation, system, tools
        if not self.responses:
            raise AssertionError("response script exhausted")
        for event in self.responses.pop(0):
            yield event


def test_tool_result_keeps_legacy_positionals_and_structured_fields() -> None:
    legacy = ToolResult("ok", False)
    structured = ToolResult("ok", data={"path": "a.py"}, exit_code=0, diagnostics=("clean",))

    assert legacy.output == "ok"
    assert legacy.data is None
    assert structured.data == {"path": "a.py"}
    assert structured.exit_code == 0
    assert structured.diagnostics == ("clean",)


def test_intent_classifier_distinguishes_action_from_question() -> None:
    assert classify_task_intent("请创建 api.py 并运行测试").requires_execution is True
    assert classify_task_intent("请创建 api.py 并运行测试").requires_tests is True
    assert classify_task_intent("如何修改 api.py？").requires_execution is False


@pytest.mark.asyncio
async def test_tracker_rejects_action_without_execution(tmp_path: Path) -> None:
    tracker = RunEvidenceTracker(tmp_path, "创建 result.txt")
    evidence = await tracker.verify("done")

    assert evidence.outcome == "verification_failed"
    assert {issue.code for issue in evidence.issues} == {"missing_execution_evidence"}


@pytest.mark.asyncio
async def test_tracker_rejects_unfinished_plan_without_execution(tmp_path: Path) -> None:
    tracker = RunEvidenceTracker(tmp_path, "continue")
    tracker.plan_pending = True

    evidence = await tracker.verify("done")

    assert evidence.outcome == "verification_failed"
    assert {issue.code for issue in evidence.issues} == {"unfinished_plan"}


@pytest.mark.asyncio
async def test_tracker_counts_untracked_file_in_diff_stat(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=tmp_path, check=True)
    (tmp_path / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "base.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)

    target = tmp_path / "new.py"
    target.write_text("VALUE = 42\n", encoding="utf-8")
    tracker = RunEvidenceTracker(tmp_path, "创建 new.py")
    tracker.record(
        "WriteFile",
        "write",
        {"file_path": str(target)},
        ToolResult("written"),
        0.01,
    )

    evidence = await tracker.verify("done")

    assert evidence.diff_stat.available is True
    assert evidence.diff_stat.files == 1
    assert evidence.diff_stat.added == 1


@pytest.mark.asyncio
async def test_tracker_completes_when_later_test_run_succeeds(tmp_path: Path) -> None:
    """带管道截断而失败的测试命令之后全量通过时，不应误报 verification_failed。"""
    tracker = RunEvidenceTracker(tmp_path, "创建 api.py 并运行测试")
    tracker.record(
        "Bash",
        "command",
        {"command": "python -m pytest -q 2>&1 | head -40"},
        ToolResult("BrokenPipeError", is_error=True, exit_code=120),
        1.0,
    )
    tracker.record(
        "Bash",
        "command",
        {"command": "python -m pytest -q"},
        ToolResult("514 passed, 1 skipped", exit_code=0),
        2.0,
    )

    evidence = await tracker.verify("done")

    assert evidence.outcome == "completed"
    assert evidence.tests[-1].exit_code == 0
    assert {issue.code for issue in evidence.issues} == set()


@pytest.mark.asyncio
async def test_tracker_still_fails_when_latest_test_run_fails(tmp_path: Path) -> None:
    """最后一条测试命令失败时，验证仍应判定失败。"""
    tracker = RunEvidenceTracker(tmp_path, "创建 api.py 并运行测试")
    tracker.record(
        "Bash",
        "command",
        {"command": "python -m pytest -q"},
        ToolResult("1 failed", is_error=True, exit_code=1),
        1.0,
    )

    evidence = await tracker.verify("done")

    assert evidence.outcome == "verification_failed"
    assert any(issue.code == "tests_failed" for issue in evidence.issues)


@pytest.mark.asyncio
async def test_tracker_still_blocks_non_test_tool_error(tmp_path: Path) -> None:
    """非测试命令的错误仍应被判定为 blocking issue。"""
    tracker = RunEvidenceTracker(tmp_path, "say hello")
    tracker.record(
        "Bash",
        "command",
        {"command": "rm -rf /some/dir"},
        ToolResult("denied", is_error=True, exit_code=1),
        1.0,
    )

    evidence = await tracker.verify("done")

    assert evidence.outcome == "verification_failed"
    assert any(issue.code == "tool_error" for issue in evidence.issues)


@pytest.mark.asyncio
async def test_agent_repairs_missing_evidence_then_completes(tmp_path: Path) -> None:
    target = tmp_path / "result.txt"
    client = ScriptedClient(
        [
            [TextDelta("Done without evidence"), StreamEnd("end_turn")],
            [
                ToolCallComplete(
                    "write-1",
                    "WriteFile",
                    {"file_path": str(target), "content": "verified"},
                ),
                StreamEnd("tool_use"),
            ],
            [TextDelta("Created and verified"), StreamEnd("end_turn")],
        ]
    )
    agent = Agent(client, registry=create_default_registry(work_dir=tmp_path), work_dir=tmp_path)

    events = [event async for event in agent.run("创建 result.txt")]

    verification = [event for event in events if isinstance(event, VerificationEvent)]
    assert [event.status for event in verification] == ["started", "failed", "started", "passed"]
    completed = next(event for event in events if isinstance(event, LoopComplete))
    assert completed.outcome == "completed"
    assert completed.evidence is not None
    assert completed.evidence.changed_files == ["result.txt"]
    assert target.read_text(encoding="utf-8") == "verified"


@pytest.mark.asyncio
async def test_agent_stops_after_two_failed_repairs(tmp_path: Path) -> None:
    client = ScriptedClient(
        [[TextDelta(text), StreamEnd("end_turn")] for text in ("done", "done again", "still done")]
    )
    agent = Agent(client, registry=create_default_registry(work_dir=tmp_path), work_dir=tmp_path)

    events = [event async for event in agent.run("实现功能并运行测试")]

    completed = next(event for event in events if isinstance(event, LoopComplete))
    assert completed.outcome == "verification_failed"
    assert completed.evidence is not None
    assert completed.evidence.unresolved
    error = next(event for event in events if isinstance(event, ErrorEvent))
    assert "after 2 repair attempts" in error.message
