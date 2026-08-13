"""Public permission-system API."""

from __future__ import annotations

from enum import StrEnum

from simplecode.permissions.checker import Decision, DecisionSource, PermissionChecker
from simplecode.permissions.dangerous import DangerousCommandDetector, is_safe_command
from simplecode.permissions.modes import DecisionEffect, PermissionMode, mode_decide
from simplecode.permissions.rules import (
    MatchMode,
    Rule,
    RuleEngine,
    extract_content,
    normalize_permission_arguments,
    normalize_permission_content,
    parse_rule,
    permission_argument_hash,
)
from simplecode.permissions.sandbox import PathSandbox


class PermissionDecision(StrEnum):
    """Legacy enum retained for callers written against CH4."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


__all__ = [
    "DangerousCommandDetector",
    "Decision",
    "DecisionEffect",
    "DecisionSource",
    "PathSandbox",
    "MatchMode",
    "PermissionChecker",
    "PermissionDecision",
    "PermissionMode",
    "Rule",
    "RuleEngine",
    "extract_content",
    "is_safe_command",
    "mode_decide",
    "normalize_permission_content",
    "normalize_permission_arguments",
    "permission_argument_hash",
    "parse_rule",
]
