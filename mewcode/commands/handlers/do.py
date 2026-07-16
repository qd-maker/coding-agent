"""Leave Plan Mode and return to normal execution."""

from __future__ import annotations

from mewcode.commands.handlers.plan import PlanModeTarget


def handle_do(target: PlanModeTarget) -> str:
    target.set_plan_mode(False)
    return "Accept Edits on (shift+tab to cycle)"


__all__ = ["handle_do"]
