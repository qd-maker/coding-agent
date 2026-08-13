"""Persistent Agent-team lifecycle, task board, mailbox, resume and merge orchestration."""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from simplecode.teams.backend_detect import detect_backend
from simplecode.teams.mailbox import Mailbox, create_message
from simplecode.teams.models import (
    AgentTeam,
    BackendType,
    TeammateInfo,
    resolve_team_dir,
    unique_team_name,
)
from simplecode.teams.registry import AgentNameRegistry
from simplecode.teams.shared_task import SharedTask, SharedTaskStore
from simplecode.teams.spawn_inprocess import InProcessTeammateHandle, spawn_inprocess_teammate
from simplecode.teams.spawn_tmux import kill_pane
from simplecode.teams.transcript import load_transcript, save_transcript
from simplecode.worktree.changes import run_git

VALID_STATUSES = {"pending", "in_progress", "completed", "blocked"}


class TeamError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MergeResult:
    team_name: str
    merged_branches: tuple[str, ...]
    original_head: str
    final_head: str


class TeamManager:
    """Own all persistent resources for teams created by one Simple Code runtime."""

    def __init__(
        self,
        worktree_manager: Any | None = None,
        trace_manager: Any | None = None,
        *,
        teams_root: str | Path | None = None,
    ) -> None:
        self.worktree_manager = worktree_manager
        self.trace_manager = trace_manager
        self.teams_root = (
            Path(teams_root).expanduser().resolve()
            if teams_root is not None
            else (Path.home() / ".simplecode" / "teams").resolve()
        )
        self._teams: dict[str, AgentTeam] = {}
        self._task_stores: dict[str, SharedTaskStore] = {}
        self._mailboxes: dict[str, Mailbox] = {}
        self._inprocess_handles: dict[str, InProcessTeammateHandle] = {}
        self._pane_ids: dict[str, str] = {}
        self._detected_backend: BackendType | None = None
        self._teammate_team_map: dict[str, str] = {}
        self._legacy_stores: dict[str, SharedTaskStore] = {}
        self._legacy_mailboxes: dict[str, list[str]] = {}
        self._current_team_name = ""
        self._lock = RLock()

    def detect_backend(
        self,
        teammate_mode: str = "",
        *,
        is_interactive: bool = True,
    ) -> BackendType:
        if self._detected_backend is None or teammate_mode:
            selected = detect_backend(teammate_mode, is_interactive=is_interactive)
            self._detected_backend = selected
            return selected
        return self._detected_backend

    def create_team(
        self,
        name: str,
        lead_agent_id: str,
        *,
        description: str = "",
        teammate_mode: str = "",
        is_interactive: bool = True,
    ) -> AgentTeam:
        self.detect_backend(teammate_mode, is_interactive=is_interactive)
        actual_name = unique_team_name(name, self.teams_root)
        directory = resolve_team_dir(actual_name, self.teams_root)
        directory.mkdir(parents=True, exist_ok=False)
        team = AgentTeam(
            name=actual_name,
            lead_agent_id=lead_agent_id,
            members=[],
            config_path=directory / "config.json",
            description=description,
        )
        task_store = SharedTaskStore(directory / "tasks.json")
        task_store.init_empty()
        mailbox = Mailbox(directory / "mailboxes")
        team.save()
        with self._lock:
            self._teams[actual_name] = team
            self._task_stores[actual_name] = task_store
            self._mailboxes[actual_name] = mailbox
            self._current_team_name = actual_name
        return team

    def get_team(self, team_name: str) -> AgentTeam | None:
        if team_name == "__active__":
            team_name = self._current_team_name
        if not team_name:
            return None
        cached = self._teams.get(team_name)
        if cached is not None:
            return cached
        config = resolve_team_dir(team_name, self.teams_root) / "config.json"
        if not config.exists():
            return None
        try:
            team = AgentTeam.load(config)
        except (OSError, ValueError, KeyError, TypeError):
            return None
        with self._lock:
            self._teams[team.name] = team
            self._current_team_name = team.name
            for member in team.members:
                self._teammate_team_map[member.agent_id] = team.name
                AgentNameRegistry.instance().register(member.name, member.agent_id)
                pane_id = str(member.metadata.get("pane_id", ""))
                if pane_id:
                    self._pane_ids[member.agent_id] = pane_id
        return team

    def list_teams(self) -> list[AgentTeam]:
        if self.teams_root.is_dir():
            for config in self.teams_root.glob("*/config.json"):
                self.get_team(config.parent.name)
        return sorted(self._teams.values(), key=lambda team: team.name.casefold())

    def get_task_store(self, team_name: str) -> SharedTaskStore:
        if team_name == "__active__":
            team_name = self._current_team_name
        team = self.get_team(team_name)
        if team is None:
            # CH4 compatibility: an arbitrary team name remains an ephemeral board.
            return self._legacy_stores.setdefault(team_name, SharedTaskStore())
        return self._task_stores.setdefault(
            team.name,
            SharedTaskStore(team.directory / "tasks.json"),
        )

    def get_mailbox(self, team_name: str) -> Mailbox:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"unknown team: {team_name}")
        return self._mailboxes.setdefault(team.name, Mailbox(team.directory / "mailboxes"))

    def register_member(self, team_name: str, member: TeammateInfo) -> None:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"unknown team: {team_name}")
        team.add_member(member)
        AgentNameRegistry.instance().register(member.name, member.agent_id)
        self._teammate_team_map[member.agent_id] = team.name

    def set_member_idle(self, team_name: str, name_or_id: str) -> bool:
        team = self.get_team(team_name)
        if team is None:
            return False
        member = team.get_member(name_or_id)
        if member is None:
            return False
        team.set_member_active(member.agent_id, False)
        self.get_mailbox(team.name).write(
            team.lead_agent_id,
            create_message(
                member.agent_id,
                team.lead_agent_id,
                f"Teammate {member.name} finished and is now idle. "
                "Send it a new message to resume.",
                f"{member.name} is idle",
                metadata={"member": member.name, "event": "idle"},
            ),
        )
        return True

    def set_member_active(self, team_name: str, name_or_id: str, value: bool) -> bool:
        team = self.get_team(team_name)
        return bool(team and team.set_member_active(name_or_id, value))

    def register_inprocess_handle(
        self,
        team_name: str,
        member: TeammateInfo,
        handle: InProcessTeammateHandle,
    ) -> None:
        self._inprocess_handles[member.agent_id] = handle

        def completed(_task: asyncio.Task[str]) -> None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self.on_teammate_completed(member.agent_id))

        handle.task.add_done_callback(completed)

    def register_pane_id(self, agent_id: str, pane_id: str) -> None:
        self._pane_ids[agent_id] = pane_id

    def get_pane_id(self, agent_id: str) -> str | None:
        return self._pane_ids.get(agent_id)

    def get_team_for_teammate(self, agent_id: str) -> AgentTeam | None:
        team_name = self._teammate_team_map.get(agent_id)
        if team_name:
            return self.get_team(team_name)
        for team in self.list_teams():
            if team.get_member(agent_id) is not None:
                self._teammate_team_map[agent_id] = team.name
                return team
        return None

    async def on_teammate_completed(self, agent_id: str) -> None:
        team = self.get_team_for_teammate(agent_id)
        handle = self._inprocess_handles.get(agent_id)
        if team is None:
            return
        if handle is not None:
            save_transcript(
                team.name,
                agent_id,
                handle.agent.conversation,
                self.teams_root,
            )
            if self.trace_manager is not None:
                failed = handle.task.cancelled()
                if not failed:
                    try:
                        failed = handle.task.exception() is not None
                    except (asyncio.CancelledError, asyncio.InvalidStateError):
                        failed = True
                status = "failed" if failed else "completed"
                self.trace_manager.update(
                    agent_id,
                    input_tokens=handle.agent.total_input_tokens,
                    output_tokens=handle.agent.total_output_tokens,
                )
                self.trace_manager.complete(agent_id, status)
        self.set_member_idle(team.name, agent_id)

    async def resume_member(self, team_name: str, name_or_id: str) -> bool:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"unknown team: {team_name}")
        member = team.get_member(name_or_id)
        if member is None:
            raise TeamError(f"unknown team member: {name_or_id}")
        if member.is_active is not False:
            return False
        handle = self._inprocess_handles.get(member.agent_id)
        if member.backend_type is not BackendType.IN_PROCESS or handle is None:
            pane_id = self._pane_ids.get(member.agent_id)
            if pane_id:
                if member.backend_type is BackendType.TMUX:
                    from simplecode.teams.spawn_tmux import build_cli_command, send_keys_to_pane

                    command = build_cli_command(
                        team_name=team.name,
                        teammate_name=member.name,
                        mailbox_dir=team.directory / "mailboxes",
                        work_dir=member.worktree_path,
                        prompt="",
                        agent_type=(
                            member.agent_type if member.agent_type not in {"", "fork"} else ""
                        ),
                        model=member.model if member.model != "inherit" else "",
                    )
                    sent = send_keys_to_pane(pane_id, command) and send_keys_to_pane(pane_id)
                    if not sent:
                        raise TeamError("tmux pane is unavailable; teammate was not resumed")
                elif member.backend_type is BackendType.ITERM2:
                    from simplecode.teams.spawn_iterm2 import spawn_iterm2_teammate

                    pane = spawn_iterm2_teammate(
                        team_name=team.name,
                        teammate_name=member.name,
                        mailbox_dir=team.directory / "mailboxes",
                        work_dir=member.worktree_path,
                        prompt="",
                        agent_type=(
                            member.agent_type if member.agent_type not in {"", "fork"} else ""
                        ),
                        model=member.model if member.model != "inherit" else "",
                    )
                    self.register_pane_id(member.agent_id, pane.session_id)
                    member.metadata["pane_id"] = pane.session_id
                    team.save()
                team.set_member_active(member.agent_id, True)
                return True
            raise TeamError("pane teammate cannot be resumed because its pane is unavailable")
        conversation = (
            load_transcript(
                team.name,
                member.agent_id,
                self.teams_root,
            )
            or handle.agent.conversation
        )
        handle.agent.conversation = conversation
        resumed = spawn_inprocess_teammate(
            handle.agent,
            "",
            member.name,
            conversation,
        )
        team.set_member_active(member.agent_id, True)
        self.register_inprocess_handle(team.name, member, resumed)
        return True

    async def stop_member(self, team_name: str, name_or_id: str) -> bool:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"unknown team: {team_name}")
        member = team.get_member(name_or_id)
        if member is None:
            raise TeamError(f"unknown member: {name_or_id}")
        handle = self._inprocess_handles.get(member.agent_id)
        if handle is not None:
            handle.cancel()
        pane_id = self._pane_ids.get(member.agent_id)
        if pane_id:
            kill_pane(pane_id)
        team.set_member_active(member.agent_id, False)
        return True

    async def merge_team(self, team_name: str) -> MergeResult:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"unknown team: {team_name}")
        if not team.all_idle():
            raise TeamError("all teammates must be idle before merge")
        manager = self.worktree_manager
        repo_root = getattr(manager, "repo_root", None)
        if repo_root is None:
            raise TeamError("Git worktree manager is unavailable")
        status = await asyncio.to_thread(run_git, repo_root, "status", "--porcelain")
        if status.returncode != 0 or status.stdout.strip():
            raise TeamError("lead worktree must be clean before merging teammates")
        head = await asyncio.to_thread(run_git, repo_root, "rev-parse", "HEAD")
        if head.returncode != 0:
            raise TeamError(head.stderr.strip() or "cannot read lead HEAD")
        original_head = head.stdout.strip()
        merged: list[str] = []
        for member in team.members:
            branch = str(member.metadata.get("worktree_branch", ""))
            if not branch:
                continue
            result = await asyncio.to_thread(
                run_git,
                repo_root,
                "merge",
                "--no-ff",
                branch,
                "-m",
                f"Merge teammate {member.name} from team {team.name}",
            )
            if result.returncode != 0:
                await asyncio.to_thread(run_git, repo_root, "merge", "--abort")
                await asyncio.to_thread(run_git, repo_root, "reset", "--hard", original_head)
                raise TeamError(
                    f"merge conflict in {member.name}; all team merges rolled back: "
                    f"{result.stderr.strip()}"
                )
            merged.append(branch)
        final = await asyncio.to_thread(run_git, repo_root, "rev-parse", "HEAD")
        return MergeResult(team.name, tuple(merged), original_head, final.stdout.strip())

    async def delete_team(self, team_name: str, *, discard: bool = False) -> None:
        team = self.get_team(team_name)
        if team is None:
            raise TeamError(f"unknown team: {team_name}")
        active = team.active_members()
        if active:
            names = ", ".join(member.name for member in active)
            raise TeamError(f"team still has active members: {names}")
        for member in list(team.members):
            AgentNameRegistry.instance().unregister(member.name)
            handle = self._inprocess_handles.pop(member.agent_id, None)
            if handle is not None:
                handle.cancel()
            pane_id = self._pane_ids.pop(member.agent_id, "")
            if pane_id:
                kill_pane(pane_id)
            if self.trace_manager is not None:
                remove = getattr(self.trace_manager, "remove", None)
                if callable(remove):
                    remove(member.agent_id)
            if self.worktree_manager is not None:
                worktree_name = str(member.metadata.get("worktree_name", ""))
                if worktree_name:
                    try:
                        await self.worktree_manager.remove(worktree_name, discard=discard)
                    except Exception as exc:  # noqa: BLE001 - preserve state on unsafe cleanup
                        raise TeamError(f"cannot remove {member.name} worktree: {exc}") from exc
            self._teammate_team_map.pop(member.agent_id, None)
        self.get_mailbox(team.name).cleanup_all()
        shutil.rmtree(team.directory, ignore_errors=False)
        self._teams.pop(team.name, None)
        self._task_stores.pop(team.name, None)
        self._mailboxes.pop(team.name, None)
        if self._current_team_name == team.name:
            self._current_team_name = ""

    async def shutdown_runtime(self) -> None:
        """Stop local coroutines while retaining disk state for a later resume."""

        for agent_id, handle in tuple(self._inprocess_handles.items()):
            if not handle.done:
                team = self.get_team_for_teammate(agent_id)
                if team is not None:
                    save_transcript(
                        team.name,
                        agent_id,
                        handle.agent.conversation,
                        self.teams_root,
                    )
                    team.set_member_active(agent_id, False)
                handle.cancel()

    # CH4 compatibility methods.
    def send_message(self, agent_id: str, message: str) -> None:
        team = self.get_team_for_teammate(agent_id)
        if team is None:
            self._legacy_mailboxes.setdefault(agent_id, []).append(message)
            return
        self.get_mailbox(team.name).write(
            agent_id,
            create_message("system", agent_id, message, message[:80]),
        )

    def consume_mailbox(self, agent_id: str) -> list[str]:
        team = self.get_team_for_teammate(agent_id)
        if team is None:
            return self._legacy_mailboxes.pop(agent_id, [])
        return [message.content for message in self.get_mailbox(team.name).consume(agent_id)]


TaskRecord = SharedTask
TaskStore = SharedTaskStore

__all__ = [
    "MergeResult",
    "TaskRecord",
    "TaskStore",
    "TeamError",
    "TeamManager",
    "VALID_STATUSES",
]
