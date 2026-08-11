"""tmux teammate process backend."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TmuxPaneInfo:
    pane_id: str


class TmuxSpawnError(RuntimeError):
    pass


def _run_tmux(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )


def _shell_quote(value: str) -> str:
    return shlex.quote(value)


def build_cli_command(
    *,
    team_name: str,
    teammate_name: str,
    mailbox_dir: str | Path,
    work_dir: str | Path,
    prompt: str,
    agent_type: str = "",
    model: str = "",
) -> str:
    environment = {
        "MEWCODE_TEAM_NAME": team_name,
        "MEWCODE_TEAMMATE_NAME": teammate_name,
        "MEWCODE_MAILBOX_DIR": str(mailbox_dir),
    }
    prefix = " ".join(f"{key}={_shell_quote(value)}" for key, value in environment.items())
    command = ["mewcode", "-p", "--work-dir", str(work_dir)]
    if agent_type:
        command.extend(["--agent-type", agent_type])
    if model:
        command.extend(["--model", model])
    command.append(prompt)
    return f"{prefix} {' '.join(_shell_quote(item) for item in command)}"


def spawn_tmux_teammate(
    *,
    team_name: str,
    teammate_name: str,
    mailbox_dir: str | Path,
    work_dir: str | Path,
    prompt: str,
    agent_type: str = "",
    model: str = "",
) -> TmuxPaneInfo:
    pane = _run_tmux("split-window", "-h", "-P", "-F", "#{pane_id}", "-t", team_name)
    if pane.returncode != 0:
        window = _run_tmux("new-window", "-P", "-F", "#{window_id}", "-n", team_name)
        if window.returncode == 0:
            pane = _run_tmux(
                "split-window", "-h", "-P", "-F", "#{pane_id}", "-t", window.stdout.strip()
            )
    if pane.returncode != 0:
        session = _run_tmux("new-session", "-d", "-s", team_name)
        if session.returncode == 0:
            pane = _run_tmux("list-panes", "-t", team_name, "-F", "#{pane_id}")
    pane_id = pane.stdout.splitlines()[0].strip() if pane.returncode == 0 and pane.stdout else ""
    if not pane_id:
        raise TmuxSpawnError(pane.stderr.strip() or "tmux could not allocate a pane")
    command = build_cli_command(
        team_name=team_name,
        teammate_name=teammate_name,
        mailbox_dir=mailbox_dir,
        work_dir=work_dir,
        prompt=prompt,
        agent_type=agent_type,
        model=model,
    )
    sent = _run_tmux("send-keys", "-t", pane_id, command, "Enter")
    if sent.returncode != 0:
        kill_pane(pane_id)
        raise TmuxSpawnError(sent.stderr.strip() or "tmux send-keys failed")
    return TmuxPaneInfo(pane_id=pane_id)


def send_keys_to_pane(pane_id: str, keys: str = "Enter") -> bool:
    if not pane_id or os.name == "nt":
        return False
    return _run_tmux("send-keys", "-t", pane_id, keys).returncode == 0


def kill_pane(pane_id: str) -> None:
    if pane_id:
        try:
            _run_tmux("kill-pane", "-t", pane_id)
        except (OSError, subprocess.SubprocessError):
            pass


__all__ = [
    "TmuxPaneInfo",
    "TmuxSpawnError",
    "build_cli_command",
    "kill_pane",
    "send_keys_to_pane",
    "spawn_tmux_teammate",
]
