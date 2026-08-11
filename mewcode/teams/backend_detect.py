"""Select a teammate runtime backend without silent isolation downgrade."""

from __future__ import annotations

import os
import shutil

from mewcode.teams.models import BackendType


class BackendDetectionError(RuntimeError):
    pass


def detect_backend(teammate_mode: str = "", is_interactive: bool = True) -> BackendType:
    mode = teammate_mode.strip().casefold()
    if mode not in {"", "auto", "in-process", "tmux", "iterm2"}:
        raise BackendDetectionError(f"unsupported teammate_mode: {teammate_mode!r}")
    if mode == "in-process" or not is_interactive:
        return BackendType.IN_PROCESS
    if mode == "tmux":
        if shutil.which("tmux"):
            return BackendType.TMUX
        raise BackendDetectionError("teammate_mode=tmux but tmux is not installed")
    if mode == "iterm2":
        if os.getenv("TERM_PROGRAM") == "iTerm.app" and shutil.which("it2"):
            return BackendType.ITERM2
        raise BackendDetectionError("teammate_mode=iterm2 requires iTerm2 + it2 CLI")
    if os.getenv("TMUX"):
        return BackendType.TMUX
    if os.getenv("TERM_PROGRAM") == "iTerm.app" and shutil.which("it2"):
        return BackendType.ITERM2
    if shutil.which("tmux"):
        return BackendType.TMUX
    raise BackendDetectionError(
        "No isolated pane backend is available. Install tmux (brew install tmux), use iTerm2 + "
        "it2 CLI, or explicitly set teammate_mode: in-process."
    )


__all__ = ["BackendDetectionError", "detect_backend"]
