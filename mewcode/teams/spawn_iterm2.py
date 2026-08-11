"""iTerm2 teammate process backend via the official it2 CLI."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from mewcode.teams.spawn_tmux import build_cli_command


@dataclass(frozen=True, slots=True)
class ITermPaneInfo:
    session_id: str


class ITermSpawnError(RuntimeError):
    pass


def _run_it2(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["it2", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def spawn_iterm2_teammate(
    *,
    team_name: str,
    teammate_name: str,
    mailbox_dir: str | Path,
    work_dir: str | Path,
    prompt: str,
    agent_type: str = "",
    model: str = "",
) -> ITermPaneInfo:
    command = build_cli_command(
        team_name=team_name,
        teammate_name=teammate_name,
        mailbox_dir=mailbox_dir,
        work_dir=work_dir,
        prompt=prompt,
        agent_type=agent_type,
        model=model,
    )
    result = _run_it2("split-pane", "--command", f"/bin/zsh -lc {command!r}")
    if result.returncode != 0:
        raise ITermSpawnError(result.stderr.strip() or "it2 split-pane failed")
    session_id = result.stdout.strip()
    if not session_id:
        raise ITermSpawnError("it2 did not return a session id")
    return ITermPaneInfo(session_id=session_id)


__all__ = ["ITermPaneInfo", "ITermSpawnError", "spawn_iterm2_teammate"]
