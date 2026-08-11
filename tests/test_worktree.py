"""CH14 Git worktree lifecycle, isolation, persistence and command tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from mewcode.agent import Agent
from mewcode.agents.loader import AgentLoader
from mewcode.cache import FileCache
from mewcode.client import LLMClient
from mewcode.commands.handlers.worktree import handle_worktree
from mewcode.commands.registry import CommandContext
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.tools import create_default_registry
from mewcode.tools.agent_tool import AgentTool, AgentToolParams
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from mewcode.worktree import (
    InvalidWorktreeName,
    WorktreeHasChangesError,
    WorktreeManager,
    cleanup_stale_worktrees,
    flatten_slug,
    validate_slug,
)


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init")
    _git(root, "config", "user.email", "mewcode@example.test")
    _git(root, "config", "user.name", "MewCode Test")
    (root / "witness.txt").write_text("main", encoding="utf-8")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "initial")
    return root.resolve()


@pytest.mark.parametrize(
    "name",
    ["", ".", "..", "a/../b", "/absolute", "a//b", "a\\..\\b", "a b"],
)
def test_worktree_name_rejects_unsafe_paths(name: str) -> None:
    with pytest.raises(InvalidWorktreeName):
        validate_slug(name)


def test_nested_name_flattens_for_branch() -> None:
    assert validate_slug("feature/ch14.demo") == "feature/ch14.demo"
    assert flatten_slug("feature/ch14.demo") == "feature+ch14.demo"


@pytest.mark.asyncio
async def test_create_enter_retarget_tools_and_protect_changes(git_repo: Path) -> None:
    cache = FileCache()
    registry = create_default_registry(cache, work_dir=git_repo)
    manager = WorktreeManager(git_repo, file_cache=cache)
    manager.add_work_dir_callback(registry.set_work_dir)
    worktree = await manager.create("feature/demo")
    assert worktree.branch == "worktree-feature+demo"
    await manager.enter(worktree.name)
    result = await registry.execute(
        "WriteFile",
        {"file_path": "witness.txt", "content": "isolated"},
    )
    assert result.is_error is False
    assert (git_repo / "witness.txt").read_text(encoding="utf-8") == "main"
    assert (worktree.path / "witness.txt").read_text(encoding="utf-8") == "isolated"
    with pytest.raises(WorktreeHasChangesError, match="1 uncommitted file"):
        await manager.exit(remove=True)
    await manager.exit(remove=True, discard=True)
    assert not worktree.path.exists()


@pytest.mark.asyncio
async def test_file_cache_is_cleared_on_enter_and_exit(git_repo: Path) -> None:
    cache = FileCache()
    cache.set(git_repo / "witness.txt", "main")
    assert len(cache) == 1
    manager = WorktreeManager(git_repo, file_cache=cache)
    worktree = await manager.create("cache-test")
    await manager.enter(worktree.name)
    assert len(cache) == 0
    cache.set(worktree.path / "witness.txt", "main")
    await manager.exit()
    assert len(cache) == 0
    await manager.remove(worktree.name, discard=True)


@pytest.mark.asyncio
async def test_fast_recovery_does_not_spawn_git(
    git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = WorktreeManager(git_repo)
    created = await first.create("recover")
    second = WorktreeManager(git_repo)

    def fail_git(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("fast recovery must not spawn git")

    monkeypatch.setattr("mewcode.worktree.manager.run_git", fail_git)
    recovered = await second.create("recover")
    assert recovered.path == created.path
    assert recovered.head_commit == created.head_commit
    monkeypatch.undo()
    await second.remove("recover", discard=True)


@pytest.mark.asyncio
async def test_session_persistence_and_resume(git_repo: Path) -> None:
    manager = WorktreeManager(git_repo)
    worktree = await manager.create("crashtest")
    await manager.enter(worktree.name)
    payload = manager.session_file.read_text(encoding="utf-8")
    assert "crashtest" in payload
    restored_manager = WorktreeManager(git_repo)
    restored = restored_manager.restore_session()
    assert restored is not None
    assert restored.worktree_path == worktree.path
    await restored_manager.exit(remove=True, discard=True)
    assert restored_manager.session_file.read_text(encoding="utf-8") == "{}"


@pytest.mark.asyncio
async def test_post_creation_setup_copies_local_config(git_repo: Path) -> None:
    (git_repo / ".env").write_text("LOCAL_ONLY=yes", encoding="utf-8")
    manager = WorktreeManager(git_repo)
    worktree = await manager.create("setup")
    assert (worktree.path / ".env").read_text(encoding="utf-8") == "LOCAL_ONLY=yes"
    assert ".env" in manager.last_setup_report.copied
    await manager.remove(worktree.name, discard=True)


@pytest.mark.asyncio
async def test_stale_cleanup_three_layers(git_repo: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", str(remote))
    _git(git_repo, "remote", "add", "origin", str(remote))
    _git(git_repo, "push", "-u", "origin", "HEAD")
    manager = WorktreeManager(git_repo)
    worktree = await manager.create("agent-a1b2c3d4")
    old = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(worktree.path, (old, old))
    result = await cleanup_stale_worktrees(
        manager,
        cutoff=timedelta(hours=1),
    )
    assert result.removed == [worktree.name]


class _ScriptedClient(LLMClient):
    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        super().__init__(
            ProviderConfig(
                protocol="anthropic",
                model="test-model",
                base_url="https://example.test",
                api_key="test-key",
            )
        )
        self.scripts = scripts

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del conversation, system, tools
        for event in self.scripts.pop(0):
            yield event


@pytest.mark.asyncio
async def test_subagent_worktree_preserves_changes_and_parent(git_repo: Path) -> None:
    client = _ScriptedClient(
        [
            [
                ToolCallComplete(
                    "write-1",
                    "WriteFile",
                    {"file_path": "witness.txt", "content": "isolated"},
                ),
                StreamEnd("tool_use"),
            ],
            [TextDelta("done"), StreamEnd("end_turn")],
        ]
    )
    parent = Agent(
        client,
        registry=create_default_registry(work_dir=git_repo),
        work_dir=git_repo,
        conversation=ConversationManager(),
    )
    loader = AgentLoader(git_repo)
    loader.load_all()
    manager = WorktreeManager(git_repo)
    tool = AgentTool(
        parent_agent=parent,
        agent_loader=loader,
        worktree_manager=manager,
        foreground_timeout=5,
    )
    result = await tool.execute(
        AgentToolParams(
            prompt="modify witness",
            description="isolated change",
            subagent_type="general-purpose",
            isolation="worktree",
        )
    )
    assert "Worktree preserved at" in result.output
    assert (git_repo / "witness.txt").read_text(encoding="utf-8") == "main"
    preserved = next((git_repo / ".mewcode/worktrees").glob("agent-*"))
    assert (preserved / "witness.txt").read_text(encoding="utf-8") == "isolated"
    await manager.remove(preserved.name, discard=True)


@pytest.mark.asyncio
async def test_subagent_clean_worktree_is_removed(git_repo: Path) -> None:
    client = _ScriptedClient([[TextDelta("done"), StreamEnd("end_turn")]])
    parent = Agent(
        client,
        registry=create_default_registry(work_dir=git_repo),
        work_dir=git_repo,
        conversation=ConversationManager(),
    )
    loader = AgentLoader(git_repo)
    loader.load_all()
    manager = WorktreeManager(git_repo)
    tool = AgentTool(
        parent_agent=parent,
        agent_loader=loader,
        worktree_manager=manager,
        foreground_timeout=5,
    )
    result = await tool.execute(
        AgentToolParams(
            prompt="inspect only",
            description="clean isolation",
            subagent_type="general-purpose",
            isolation="worktree",
        )
    )
    assert result.output == "done"
    assert list((git_repo / ".mewcode/worktrees").glob("agent-*")) == []


class _UI:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def add_system_message(self, text: str) -> None:
        self.messages.append(text)


@pytest.mark.asyncio
async def test_worktree_slash_command(git_repo: Path) -> None:
    manager = WorktreeManager(git_repo)
    ui = _UI()

    def context(args: str) -> CommandContext:
        return CommandContext(
            args=args,
            agent=None,
            conversation=ConversationManager(),
            session=None,
            session_manager=None,
            memory_manager=None,
            ui=ui,  # type: ignore[arg-type]
            config={"worktree_manager": manager},
        )

    await handle_worktree(context("create demo"))
    assert manager.current_session is not None
    await handle_worktree(context("status"))
    assert "demo" in ui.messages[-1]
    await handle_worktree(context("list"))
    assert "worktree-demo" in ui.messages[-1]
    await handle_worktree(context("exit --remove --discard"))
    assert manager.current_session is None


@pytest.mark.asyncio
async def test_background_isolated_agent_returns_notification(git_repo: Path) -> None:
    client = _ScriptedClient([[TextDelta("background done"), StreamEnd("end_turn")]])
    parent = Agent(
        client,
        registry=create_default_registry(work_dir=git_repo),
        work_dir=git_repo,
        conversation=ConversationManager(),
    )
    loader = AgentLoader(git_repo)
    loader.load_all()
    manager = WorktreeManager(git_repo)
    tool = AgentTool(
        parent_agent=parent,
        agent_loader=loader,
        worktree_manager=manager,
    )
    started = await tool.execute(
        AgentToolParams(
            prompt="inspect",
            description="background isolation",
            subagent_type="general-purpose",
            isolation="worktree",
            run_in_background=True,
        )
    )
    assert "Task ID:" in started.output
    completed = []
    for _ in range(30):
        await asyncio.sleep(0.1)
        completed = tool.task_manager.poll_completed()
        if completed:
            break
    assert completed and completed[0].result == "background done"
