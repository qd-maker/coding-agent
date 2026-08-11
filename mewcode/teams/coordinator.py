"""Pure coordinator mode: dual lock, workflow prompt, and session matching."""

from __future__ import annotations

import os


def is_coordinator_mode(enable_flag: bool) -> bool:
    if not enable_flag:
        return False
    return os.getenv("MEWCODE_COORDINATOR_MODE", "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }


def match_session_mode(enabled: bool) -> None:
    """Keep child processes and resumed sessions on the same side of the dual lock."""

    if enabled:
        os.environ["MEWCODE_COORDINATOR_MODE"] = "1"
    else:
        os.environ.pop("MEWCODE_COORDINATOR_MODE", None)


def get_coordinator_system_prompt(agent_catalog: str = "") -> str:
    parts = [
        "# Identity: Team Lead / Pure Coordinator",
        "You are the coordinator for a persistent Agent team. Teammates inspect and modify code. "
        "Never read, write, edit, or run shell commands yourself. Preserve understanding and final "
        "synthesis in the Lead.",
        "",
        "## Mandatory four-stage workflow",
        "1. Research — delegate repository discovery and collect file/line evidence.",
        "2. Synthesis — reconcile findings, define API contracts, dependencies, "
        "and acceptance tests.",
        "3. Implementation — create shared tasks, assign independent worktrees, "
        "and monitor blockers.",
        "4. Verification — delegate tests/review, merge only idle clean members, "
        "then report evidence.",
        "",
        "Track dependencies explicitly. A message from an idle teammate is resumable context, "
        "not a reason to spawn a replacement. Do not claim completion until returned evidence "
        "is verified.",
        "Background completions arrive as <task-notification>...</task-notification>.",
        "Avoid the anti-pattern 'based on your findings' without citing the actual evidence "
        "and owner.",
    ]
    if agent_catalog.strip():
        parts.extend(("", "# Available Agents", agent_catalog.strip()))
    return "\n".join(parts)


def get_coordinator_user_context(team_name: str) -> str:
    return (
        f"Active team: {team_name}. Break the user goal into shared tasks, assign members, "
        "wait for idle notifications, verify, and merge deliberately."
    )


__all__ = [
    "get_coordinator_system_prompt",
    "get_coordinator_user_context",
    "is_coordinator_mode",
    "match_session_mode",
]
