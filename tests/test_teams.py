"""CH15 persistent Agent-team and collaboration tests."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mewcode.agent import Agent
from mewcode.agents.tool_filter import apply_coordinator_filter
from mewcode.client import LLMClient
from mewcode.config import ProviderConfig
from mewcode.conversation import (
    ConversationManager,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.teams.backend_detect import BackendDetectionError, detect_backend
from mewcode.teams.coordinator import get_coordinator_system_prompt, is_coordinator_mode
from mewcode.teams.mailbox import Mailbox, create_message
from mewcode.teams.manager import TeamError, TeamManager
from mewcode.teams.models import (
    AgentTeam,
    BackendType,
    TeammateInfo,
    TeamModelError,
    resolve_team_dir,
    sanitize_name,
    unique_team_name,
)
from mewcode.teams.registry import AgentNameRegistry
from mewcode.teams.shared_task import SharedTaskStore
from mewcode.teams.spawn_inprocess import spawn_inprocess_teammate
from mewcode.teams.spawn_tmux import build_cli_command
from mewcode.teams.transcript import load_transcript, save_transcript
from mewcode.tools import create_default_registry
from mewcode.tools.agent_tool import AgentTool, AgentToolParams
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolResult
from mewcode.tools.send_message import SendMessageParams, SendMessageTool
from mewcode.tools.synthetic_output import SyntheticOutputTool
from mewcode.worktree import WorktreeManager


@pytest.fixture(autouse=True)
def _reset_agent_name_registry() -> None:
    AgentNameRegistry.reset()


def _member(name: str = "worker", agent_id: str = "agent-1") -> TeammateInfo:
    return TeammateInfo(
        name=name,
        agent_id=agent_id,
        agent_type="general-purpose",
        model="inherit",
        worktree_path="C:/repo/wt",
        backend_type=BackendType.IN_PROCESS,
        is_active=True,
    )


def test_team_model_roundtrip_and_lookup(tmp_path: Path) -> None:
    team = AgentTeam("demo", "lead-1", [], tmp_path / "demo" / "config.json")
    team.save()
    team.add_member(_member())
    loaded = AgentTeam.load(team.config_path)
    assert loaded.get_member("worker") == loaded.get_member("agent-1")
    assert loaded.active_members()[0].backend_type is BackendType.IN_PROCESS
    assert loaded.set_member_active("worker", False)
    assert loaded.all_idle()


@pytest.mark.parametrize("name", ["", ".", "..", "../escape", "with space", "x" * 64])
def test_team_name_rejects_unsafe_values(name: str) -> None:
    with pytest.raises(TeamModelError):
        sanitize_name(name)


def test_unique_team_name_and_root(tmp_path: Path) -> None:
    assert resolve_team_dir("Demo", tmp_path) == tmp_path.resolve() / "demo"
    (tmp_path / "demo").mkdir()
    assert unique_team_name("demo", tmp_path) == "demo-2"


def test_mailbox_fifo_read_consume_and_broadcast(tmp_path: Path) -> None:
    mailbox = Mailbox(tmp_path / "mail")
    first = create_message("a", "b", "one", "first")
    second = create_message("a", "b", "two", "second")
    mailbox.write("b", first)
    mailbox.write("b", second)
    assert [item.content for item in mailbox.read("b")] == ["one", "two"]
    assert [item.content for item in mailbox.consume("b")] == ["one", "two"]
    assert mailbox.read("b") == []
    paths = mailbox.broadcast(["a", "b", "c"], first, exclude="a")
    assert len(paths) == 2
    assert mailbox.read("c")[0].to_agent == "c"


def test_shared_task_store_persists_filters_and_dependencies(tmp_path: Path) -> None:
    path = tmp_path / "tasks.json"
    store = SharedTaskStore(path)
    store.init_empty()
    first = store.create("API contract", assignee="worker", created_by="lead")
    second = store.create("Implement", blocked_by=[first.id])
    store.update(second.id, status="blocked", add_blocks=[3], add_blocked_by=[first.id])
    restored = SharedTaskStore(path)
    assert restored.get("1") is not None
    assert restored.list_tasks(assignee="worker")[0].created_by == "lead"
    assert restored.list_tasks(status="blocked")[0].blocked_by == [1]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["next_id"] == 3


def test_name_registry_resolves_names_and_ids() -> None:
    AgentNameRegistry.reset()
    registry = AgentNameRegistry.instance()
    registry.register("worker", "abc")
    assert registry.resolve("worker") == "abc"
    assert registry.resolve("abc") == "abc"
    with pytest.raises(ValueError):
        registry.register("worker", "other")
    registry.unregister("worker")
    assert registry.list_all() == {}


def test_backend_detection_explicit_and_noninteractive(monkeypatch: pytest.MonkeyPatch) -> None:
    assert detect_backend("in-process") is BackendType.IN_PROCESS
    assert detect_backend(is_interactive=False) is BackendType.IN_PROCESS
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TERM_PROGRAM", raising=False)
    monkeypatch.setattr("mewcode.teams.backend_detect.shutil.which", lambda _name: None)
    with pytest.raises(BackendDetectionError, match="No isolated pane backend"):
        detect_backend()


def test_backend_detection_tmux_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TMUX", "session")
    assert detect_backend() is BackendType.TMUX


def test_coordinator_requires_double_lock_and_has_four_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEWCODE_COORDINATOR_MODE", "1")
    assert not is_coordinator_mode(False)
    assert is_coordinator_mode(True)
    prompt = get_coordinator_system_prompt()
    for stage in ("Research", "Synthesis", "Implementation", "Verification"):
        assert stage in prompt


def test_coordinator_filter_removes_code_and_shell_tools() -> None:
    from mewcode.tools import create_default_registry

    registry = create_default_registry()
    registry.register(SyntheticOutputTool())
    filtered = apply_coordinator_filter(registry)
    assert {tool.name for tool in filtered.list_tools()} == {"SyntheticOutput"}


def test_transcript_roundtrip_preserves_structured_blocks(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_assistant_message(
        "working",
        [ToolUseBlock("u1", "ReadFile", {"path": "a.py"})],
        [ThinkingBlock("thought", "sig")],
    )
    conversation.add_tool_results_message([ToolResultBlock("u1", "ok")])
    save_transcript("demo", "agent", conversation, tmp_path)
    loaded = load_transcript("demo", "agent", tmp_path)
    assert loaded is not None
    assert loaded.history[0].tool_uses[0].input == {"path": "a.py"}
    assert loaded.history[1].tool_results[0].content == "ok"
    assert loaded.env_injected and loaded.ltm_injected


def test_build_cli_command_quotes_prompt_and_carries_team_env(tmp_path: Path) -> None:
    command = build_cli_command(
        team_name="demo",
        teammate_name="worker",
        mailbox_dir=tmp_path / "mail",
        work_dir=tmp_path / "wt",
        prompt="don't guess",
        agent_type="Explore",
        model="haiku",
    )
    assert "MEWCODE_TEAM_NAME=demo" in command
    assert "--agent-type Explore" in command
    assert "'don'\"'\"'t guess'" in command


def test_team_manager_create_load_idle_notification_and_delete(tmp_path: Path) -> None:
    AgentNameRegistry.reset()
    manager = TeamManager(teams_root=tmp_path)
    team = manager.create_team("demo", "lead", teammate_mode="in-process", is_interactive=False)
    member = _member()
    member.worktree_path = ""
    manager.register_member(team.name, member)
    assert manager.set_member_idle(team.name, member.agent_id)
    notice = manager.get_mailbox(team.name).consume("lead")
    assert notice and "now idle" in notice[0].content
    restored = TeamManager(teams_root=tmp_path).get_team("demo")
    assert restored is not None and restored.get_member("worker") is not None
    asyncio.run(manager.delete_team(team.name))
    assert not team.directory.exists()


def test_team_manager_refuses_delete_while_member_active(tmp_path: Path) -> None:
    manager = TeamManager(teams_root=tmp_path)
    team = manager.create_team("demo", "lead", teammate_mode="in-process", is_interactive=False)
    manager.register_member(team.name, _member())
    with pytest.raises(TeamError, match="active members"):
        asyncio.run(manager.delete_team(team.name))


class _FakeAgent:
    def __init__(self) -> None:
        self.conversation = ConversationManager()
        self.prompts: list[str] = []

    async def run_to_completion(
        self,
        prompt: str,
        conversation: ConversationManager | None = None,
    ) -> str:
        self.prompts.append(prompt)
        await asyncio.sleep(0)
        return "done"


@pytest.mark.asyncio
async def test_inprocess_handle_runs_and_reports_result() -> None:
    agent = _FakeAgent()
    handle = spawn_inprocess_teammate(agent, "task", "worker")  # type: ignore[arg-type]
    await handle.task
    assert handle.done and handle.result == "done"
    assert agent.prompts == ["task"]


class _ResumeManager:
    def __init__(self, team: AgentTeam, mailbox: Mailbox) -> None:
        self.team = team
        self.mailbox = mailbox
        self.resumed: list[str] = []

    def get_team(self, _name: str) -> AgentTeam:
        return self.team

    def get_team_for_teammate(self, _agent_id: str) -> None:
        return None

    def get_mailbox(self, _name: str) -> Mailbox:
        return self.mailbox

    async def resume_member(self, _team: str, agent_id: str) -> bool:
        self.resumed.append(agent_id)
        return True

    def get_pane_id(self, _agent_id: str) -> None:
        return None


@pytest.mark.asyncio
async def test_send_message_resumes_idle_member(tmp_path: Path) -> None:
    team = AgentTeam("demo", "lead", [], tmp_path / "config.json")
    team.save()
    member = _member()
    member.is_active = False
    team.add_member(member)
    mailbox = Mailbox(tmp_path / "mail")
    manager = _ResumeManager(team, mailbox)
    sender = SimpleNamespace(agent_id="lead", team_name="demo")
    tool = SendMessageTool(manager, sender)
    result = await tool.execute(
        SendMessageParams(to="worker", content="continue", summary="new task")
    )
    assert isinstance(result, ToolResult) and not result.is_error
    assert manager.resumed == ["agent-1"]
    assert mailbox.read("agent-1")[0].content == "continue"


@pytest.mark.asyncio
async def test_send_message_requires_text_summary(tmp_path: Path) -> None:
    team = AgentTeam("demo", "lead", [], tmp_path / "config.json")
    team.save()
    tool = SendMessageTool(
        _ResumeManager(team, Mailbox(tmp_path / "mail")),
        SimpleNamespace(agent_id="lead", team_name="demo"),
    )
    result = await tool.execute(SendMessageParams(to="lead", content="hello"))
    assert result.is_error and "summary" in result.output


def test_team_manager_keeps_legacy_task_board_in_memory() -> None:
    manager = TeamManager()
    task = manager.get_task_store("legacy").create("old API")
    assert task.as_dict()["id"] == "1"


class _TeamClient(LLMClient):
    def __init__(self) -> None:
        super().__init__(
            ProviderConfig.model_validate(
                {
                    "protocol": "anthropic",
                    "model": "test-model",
                    "base_url": "https://example.com",
                    "api_key": "test-key",
                }
            )
        )
        self.calls = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del conversation, system, tools
        self.calls += 1
        yield TextDelta(f"completed-{self.calls}")
        yield StreamEnd("end_turn", input_tokens=5, output_tokens=2)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.mark.asyncio
async def test_agent_tool_team_spawn_idle_resume_and_persist(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("demo\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")

    client = _TeamClient()
    parent = Agent(client, registry=create_default_registry(work_dir=repo), work_dir=repo)
    worktrees = WorktreeManager(repo)
    manager = TeamManager(worktrees, teams_root=tmp_path / "teams")
    team = manager.create_team(
        "demo", parent.agent_id, teammate_mode="in-process", is_interactive=False
    )
    parent.team_name = team.name
    parent._team_manager = manager
    tool = AgentTool(
        parent_agent=parent,
        client=client,
        worktree_manager=worktrees,
        team_manager=manager,
    )
    started = await tool.execute(
        AgentToolParams(
            prompt="inspect the repo",
            description="explore",
            subagent_type="general-purpose",
            team_name=team.name,
            name="worker",
        )
    )
    assert not started.is_error and "started via in-process" in started.output
    member = team.get_member("worker")
    assert member is not None
    for _ in range(100):
        if member.is_active is False:
            break
        await asyncio.sleep(0.01)
    assert member.is_active is False
    assert load_transcript(team.name, member.agent_id, manager.teams_root) is not None

    resumed = await SendMessageTool(manager, parent).execute(
        SendMessageParams(to="worker", content="verify again", summary="second assignment")
    )
    assert not resumed.is_error
    for _ in range(100):
        if member.is_active is False and client.calls >= 2:
            break
        await asyncio.sleep(0.01)
    assert client.calls == 2 and member.is_active is False
    transcript = load_transcript(team.name, member.agent_id, manager.teams_root)
    assert transcript is not None
    assert any("verify again" in message.content for message in transcript.history)
    await manager.delete_team(team.name, discard=True)
