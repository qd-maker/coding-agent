"""Deterministic completion evidence and verification for one Agent turn."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from mewcode.tools.base import ToolResult

_ACTION_RE = re.compile(
    r"(?:实现|修改|创建|新增|写入|编写|删除|修复|运行|执行|测试|构建|提交|部署|重构|优化|"
    r"implement|modify|change|create|write|add|delete|remove|fix|run|test|build|commit|"
    r"deploy|refactor|optimi[sz]e)",
    re.IGNORECASE,
)
_QUESTION_RE = re.compile(
    r"^\s*(?:如何|怎么|怎样|为什么|解释|介绍|什么是|能否解释|how\b|why\b|what\b|"
    r"explain\b|describe\b)",
    re.IGNORECASE,
)
_TEST_REQUEST_RE = re.compile(
    r"(?:测试|校验|验证|lint|type.?check|pytest|unittest|npm\s+test|pnpm\s+test|"
    r"cargo\s+test|go\s+test|build|构建)",
    re.IGNORECASE,
)
_TEST_COMMAND_RE = re.compile(
    r"(?:\bpytest\b|\bpython(?:\.exe)?\s+-m\s+(?:pytest|compileall|build)\b|"
    r"\bnpm\s+(?:run\s+)?test\b|\bpnpm\s+(?:run\s+)?test\b|\byarn\s+test\b|"
    r"\bcargo\s+test\b|\bgo\s+test\b|\bruff\s+(?:check|format)\b|\bmypy\b|"
    r"\bpyright\b|\btsc\b|\bnpm\s+run\s+build\b|\bpnpm\s+build\b)",
    re.IGNORECASE,
)


class CommandEvidence(BaseModel):
    command: str
    exit_code: int | None = None
    elapsed_seconds: float = 0.0
    output_preview: str = ""
    is_test: bool = False


class DiffStat(BaseModel):
    files: int = 0
    added: int = 0
    removed: int = 0
    available: bool = False


class VerificationIssue(BaseModel):
    code: str
    message: str
    blocking: bool = True


class EvidenceBundle(BaseModel):
    summary: str = ""
    outcome: Literal["answered", "completed", "waiting_background", "verification_failed"]
    changed_files: list[str] = Field(default_factory=list)
    commands: list[CommandEvidence] = Field(default_factory=list)
    tests: list[CommandEvidence] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    diff_stat: DiffStat = Field(default_factory=DiffStat)
    unresolved: list[str] = Field(default_factory=list)
    issues: list[VerificationIssue] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class TaskIntent:
    requires_execution: bool
    requires_tests: bool


def classify_task_intent(prompt: str) -> TaskIntent:
    """Recognize explicit action requests without spending another model call."""

    normalized = prompt.strip()
    informational = bool(_QUESTION_RE.search(normalized))
    return TaskIntent(
        requires_execution=bool(_ACTION_RE.search(normalized)) and not informational,
        requires_tests=bool(_TEST_REQUEST_RE.search(normalized)) and not informational,
    )


def is_test_command(command: str) -> bool:
    return bool(_TEST_COMMAND_RE.search(command.strip()))


@dataclass(slots=True)
class RunEvidenceTracker:
    """Collect structured evidence and enforce a bounded completion gate."""

    work_dir: Path
    prompt: str
    intent: TaskIntent = field(init=False)
    changed_files: set[str] = field(default_factory=set)
    commands: list[CommandEvidence] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    latest_errors: dict[tuple[str, str], str] = field(default_factory=dict)
    saw_execution: bool = False
    saw_successful_execution: bool = False
    saw_mutation: bool = False
    background_pending: bool = False
    plan_pending: bool = False
    repair_attempts: int = 0

    def __post_init__(self) -> None:
        self.work_dir = self.work_dir.resolve()
        self.intent = classify_task_intent(self.prompt)

    @property
    def gate_required(self) -> bool:
        return (
            self.intent.requires_execution
            or self.saw_execution
            or self.background_pending
            or self.plan_pending
        )

    def record(
        self,
        tool_name: str,
        category: str,
        arguments: dict[str, Any],
        result: ToolResult,
        elapsed_seconds: float,
    ) -> None:
        is_test_command_run = False
        target = result.artifact_path or self._target(arguments)
        key = (tool_name, target[:240])
        file_mutation = tool_name in {"WriteFile", "EditFile", "WritePlan"}
        self.saw_execution = self.saw_execution or category in {"write", "command"}
        self.saw_successful_execution = self.saw_successful_execution or (
            category in {"write", "command"} and not result.is_error
        )
        self.saw_mutation = self.saw_mutation or (
            category == "write" and not result.is_error
        )

        if file_mutation and not result.is_error and target:
            self.changed_files.add(self._display_path(target))

        if tool_name == "Bash":
            command = str(arguments.get("command", ""))
            is_test_command_run = is_test_command(command)
            evidence = CommandEvidence(
                command=command,
                exit_code=result.exit_code,
                elapsed_seconds=elapsed_seconds,
                output_preview=(result.preview or result.output)[:1000],
                is_test=is_test_command_run,
            )
            self.commands.append(evidence)

        if tool_name == "Agent" and not result.is_error:
            lowered = result.output.casefold()
            if "background" in lowered and ("started" in lowered or "running" in lowered):
                self.background_pending = True

        if result.diagnostics:
            self.diagnostics.extend(result.diagnostics)
        expected_rejection = result.output.startswith(
            (
                "Permission denied:",
                "User denied permission",
                "Tool rejected by hook",
                "Hook rejected:",
            )
        )
        if result.is_error and not expected_rejection:
            # 测试命令的成败由 verify() 用最后一条测试命令的退出码判定，
            # 不在此处作为工具错误累积——否则"中间失败、最终通过"会被误报为 blocking。
            if not is_test_command_run:
                self.latest_errors[key] = result.output.splitlines()[0][:500]
        elif not result.is_error:
            self.latest_errors.pop(key, None)

    async def verify(self, summary: str) -> EvidenceBundle:
        issues: list[VerificationIssue] = []
        if self.intent.requires_execution and not self.saw_successful_execution:
            issues.append(
                VerificationIssue(
                    code="missing_execution_evidence",
                    message="The request asks for an action, but no write/command evidence exists.",
                )
            )
        if self.plan_pending and not self.saw_successful_execution:
            issues.append(
                VerificationIssue(
                    code="unfinished_plan",
                    message=(
                        "An implementation plan is pending, but this turn has no "
                        "execution evidence."
                    ),
                )
            )

        for path_text in sorted(self.changed_files):
            path = Path(path_text)
            if not path.is_absolute():
                path = self.work_dir / path
            if not path.exists():
                issues.append(
                    VerificationIssue(
                        code="missing_artifact",
                        message=f"Changed artifact no longer exists: {path_text}",
                    )
                )

        tests = [item for item in self.commands if item.is_test]
        if self.intent.requires_tests and not tests:
            issues.append(
                VerificationIssue(
                    code="tests_not_run",
                    message=(
                        "The request explicitly requires verification, "
                        "but no test command ran."
                    ),
                )
            )
        if tests and tests[-1].exit_code not in {0}:
            issues.append(
                VerificationIssue(
                    code="tests_failed",
                    message=f"Latest test command failed: {tests[-1].command}",
                )
            )
        for message in self.latest_errors.values():
            issues.append(VerificationIssue(code="tool_error", message=message))

        diff_stat = await self._git_diff_stat()
        if self.saw_mutation and self._is_git_repo() and not diff_stat.available:
            issues.append(
                VerificationIssue(code="diff_unavailable", message="Git diff could not be read.")
            )

        blocking = [item.message for item in issues if item.blocking]
        if blocking:
            outcome: Literal[
                "answered", "completed", "waiting_background", "verification_failed"
            ] = "verification_failed"
        elif self.background_pending:
            outcome = "waiting_background"
        elif self.gate_required:
            outcome = "completed"
        else:
            outcome = "answered"
        return EvidenceBundle(
            summary=summary.strip()[:2000],
            outcome=outcome,
            changed_files=sorted(self.changed_files),
            commands=list(self.commands),
            tests=tests,
            diagnostics=list(dict.fromkeys(self.diagnostics)),
            diff_stat=diff_stat,
            unresolved=blocking,
            issues=issues,
        )

    def repair_message(self, evidence: EvidenceBundle) -> str:
        lines = [
            "<verification-failed>",
            "Completion was rejected by the deterministic harness. Continue working and fix:",
        ]
        lines.extend(f"- [{item.code}] {item.message}" for item in evidence.issues if item.blocking)
        lines.append("Do not claim completion until these checks pass.")
        lines.append("</verification-failed>")
        return "\n".join(lines)

    def _target(self, arguments: dict[str, Any]) -> str:
        for key in ("file_path", "path", "command", "description"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def _display_path(self, value: str) -> str:
        path = Path(value)
        if not path.is_absolute():
            return path.as_posix()
        try:
            return path.resolve().relative_to(self.work_dir).as_posix()
        except ValueError:
            return str(path)

    def _is_git_repo(self) -> bool:
        return (self.work_dir / ".git").exists()

    async def _git_diff_stat(self) -> DiffStat:
        if not self._is_git_repo():
            return DiffStat()
        try:
            command = ["git", "diff", "--numstat", "HEAD", "--"]
            if self.changed_files:
                command.extend(sorted(self.changed_files))
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self.work_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5.0)
        except (OSError, TimeoutError):
            return DiffStat()
        if process.returncode != 0:
            return DiffStat()
        files = added = removed = 0
        for raw_line in stdout.decode("utf-8", errors="replace").splitlines():
            parts = raw_line.split("\t", 2)
            if len(parts) != 3:
                continue
            files += 1
            if parts[0].isdigit():
                added += int(parts[0])
            if parts[1].isdigit():
                removed += int(parts[1])
        if self.changed_files:
            try:
                untracked_process = await asyncio.create_subprocess_exec(
                    "git",
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                    "--",
                    *sorted(self.changed_files),
                    cwd=str(self.work_dir),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                untracked_stdout, _ = await asyncio.wait_for(
                    untracked_process.communicate(),
                    timeout=5.0,
                )
            except (OSError, TimeoutError):
                untracked_stdout = b""
            for raw_path in untracked_stdout.split(b"\0"):
                if not raw_path:
                    continue
                path = self.work_dir / raw_path.decode("utf-8", errors="replace")
                try:
                    added += len(path.read_text(encoding="utf-8").splitlines())
                except (OSError, UnicodeError):
                    pass
                files += 1
        return DiffStat(files=files, added=added, removed=removed, available=True)


__all__ = [
    "CommandEvidence",
    "DiffStat",
    "EvidenceBundle",
    "RunEvidenceTracker",
    "TaskIntent",
    "VerificationIssue",
    "classify_task_intent",
    "is_test_command",
]
