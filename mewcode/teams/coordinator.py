"""Coordinator-specific prompt text used by the CH5 prompt pipeline."""

from __future__ import annotations


def get_coordinator_system_prompt(agent_catalog: str = "") -> str:
    """Return the coordinator identity while leaving orchestration details extensible."""

    parts = [
        "# Identity",
        "You are MewCode. You are the coordinator for a team of coding agents.",
        "Delegate independent bounded work when team tools are available, track dependencies, "
        "verify returned evidence, and synthesize one coherent result for the user.",
        "Do not claim delegated work is complete until its result has been checked.",
    ]
    if agent_catalog.strip():
        parts.extend(("# Available Agents", agent_catalog.strip()))
    return "\n".join(parts)


__all__ = ["get_coordinator_system_prompt"]
