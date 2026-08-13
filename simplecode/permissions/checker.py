"""Layered permission checker for model-requested tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from simplecode.permissions.dangerous import DangerousCommandDetector, is_safe_command
from simplecode.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from simplecode.permissions.rules import RuleEngine, extract_content
from simplecode.permissions.sandbox import PathSandbox
from simplecode.tools.base import Tool

_PLAN_MODE_ALLOWED_TOOLS = frozenset({"Agent", "ToolSearch", "AskUserQuestion"})


DecisionSource = Literal["safety", "rule", "mode", "system"]


@dataclass(frozen=True, slots=True)
class Decision:
    effect: DecisionEffect
    reason: str
    source: DecisionSource = "system"


class PermissionChecker:
    def __init__(
        self,
        detector: DangerousCommandDetector | None = None,
        sandbox: PathSandbox | None = None,
        rule_engine: RuleEngine | None = None,
        mode: PermissionMode = PermissionMode.DEFAULT,
        *,
        ask_for_writes: bool | None = None,
    ) -> None:
        del ask_for_writes  # CH4 compatibility; DEFAULT now asks according to the mode matrix.
        self.detector = detector or DangerousCommandDetector()
        self.sandbox = sandbox or PathSandbox(Path.cwd())
        self.rule_engine = rule_engine or RuleEngine()
        self.mode = mode
        self.plan_file_path = ""

    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        content = extract_content(tool.name, arguments)

        if self.mode is PermissionMode.PLAN:
            if tool.name in _PLAN_MODE_ALLOWED_TOOLS or tool.is_plan_safe:
                return Decision("allow", "Plan 模式工具豁免", "mode")
            if tool.name in {"WriteFile", "EditFile"} and self._is_plan_file(content):
                return Decision("allow", "Plan 模式计划文件豁免", "mode")

        if tool.category == "command" and is_safe_command(content):
            return Decision("allow", "Safe read-only command", "safety")

        if tool.category == "command":
            dangerous, reason = self.detector.detect(content)
            if dangerous:
                return Decision("deny", f"危险命令拦截: {reason}", "safety")

        sandbox_target = content
        if tool.name in {"Glob", "Grep"}:
            path_value = arguments.get("path", ".")
            sandbox_target = path_value if isinstance(path_value, str) else str(path_value)
        if tool.category in {"read", "write"} and sandbox_target:
            allowed, reason = self.sandbox.check(sandbox_target)
            if not allowed:
                return Decision("deny", f"路径沙箱拦截: {reason}", "safety")

        # Plan restrictions cannot be relaxed by an allow rule. Exemptions above still
        # run first, while dangerous commands and sandbox escapes retain precise reasons.
        if self.mode is PermissionMode.PLAN and mode_decide(self.mode, tool.category) == "deny":
            return Decision("deny", f"权限模式 {self.mode.value} 拒绝", "mode")

        # YOLO bypasses prompts and configurable rules after the non-bypassable
        # dangerous-command and path-sandbox layers have passed.
        if self.mode is PermissionMode.BYPASS:
            return Decision("allow", f"权限模式 {self.mode.value} 放行", "mode")

        rule_effect = self.rule_engine.evaluate(tool.name, content, arguments)
        if rule_effect == "allow":
            return Decision("allow", "权限规则放行", "rule")
        if rule_effect == "deny":
            return Decision("deny", "权限规则拒绝", "rule")

        effect = mode_decide(self.mode, tool.category)
        if effect == "allow":
            return Decision("allow", f"权限模式 {self.mode.value} 放行", "mode")
        if effect == "deny":
            return Decision("deny", f"权限模式 {self.mode.value} 拒绝", "mode")
        return Decision("ask", "需要用户确认", "mode")

    def _is_plan_file(self, target_path: str) -> bool:
        if not target_path:
            return False
        normalized = Path(target_path).expanduser()
        if self.plan_file_path:
            configured = Path(self.plan_file_path).expanduser()
            if os.path.abspath(normalized) == os.path.abspath(configured):
                return True
            if normalized.name == configured.name:
                return True
        lowered_parts = {part.casefold() for part in normalized.parts}
        return "plan" in lowered_parts and normalized.suffix.casefold() == ".md"


__all__ = ["Decision", "PermissionChecker", "_PLAN_MODE_ALLOWED_TOOLS"]
