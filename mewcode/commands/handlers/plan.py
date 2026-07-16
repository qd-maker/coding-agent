"""Enter read-only Plan Mode."""

from __future__ import annotations

from typing import Protocol


class PlanModeTarget(Protocol):
    def set_plan_mode(self, enabled: bool) -> None: ...


def handle_plan(target: PlanModeTarget) -> str:
    target.set_plan_mode(True)
    return "Plan on (shift+tab to cycle)"


__all__ = ["handle_plan"]
