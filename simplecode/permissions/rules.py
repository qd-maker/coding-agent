"""Tiered YAML permission rules."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

Effect = Literal["allow", "deny"]
MatchMode = Literal["glob", "exact"]
_RULE_RE = re.compile(r"^(\w+)\((.+)\)$")
_CONTENT_FIELDS = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}


def normalize_permission_content(tool_name: str, content: str) -> str:
    """Create a stable approval signature without broadening its scope."""

    value = content.strip()
    if tool_name == "Bash":
        return " ".join(value.split())
    if tool_name in {"ReadFile", "WriteFile", "EditFile"} and value:
        return os.path.normcase(os.path.normpath(value))
    return value


def normalize_permission_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Normalize only permission-relevant fields while preserving the full request shape."""

    normalized = dict(arguments)
    field = _CONTENT_FIELDS.get(tool_name)
    if field is not None and field in normalized:
        normalized[field] = normalize_permission_content(tool_name, str(normalized[field]))
    return normalized


def permission_argument_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    payload = {
        "tool": tool_name,
        "arguments": normalize_permission_arguments(tool_name, arguments),
    }
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect = "allow"
    match_mode: MatchMode = "glob"
    argument_hash: str | None = None

    @property
    def tool(self) -> str:
        """Compatibility alias retained for CH4 callers."""

        return self.tool_name

    @property
    def decision(self) -> str:
        return self.effect

    def matches(
        self,
        tool_name: str,
        content: str,
        arguments: dict[str, Any] | None = None,
    ) -> bool:
        normalized_content = normalize_permission_content(tool_name, content)
        normalized_pattern = normalize_permission_content(tool_name, self.pattern)
        if self.tool_name != tool_name:
            return False
        if self.argument_hash is not None:
            if (
                arguments is None
                or permission_argument_hash(tool_name, arguments) != self.argument_hash
            ):
                return False
        if self.match_mode == "exact":
            return normalized_content == normalized_pattern
        return fnmatch.fnmatch(normalized_content, normalized_pattern)

    def as_yaml(self) -> dict[str, str]:
        payload = {"rule": f"{self.tool_name}({self.pattern})", "effect": self.effect}
        if self.match_mode == "exact":
            payload["match"] = "exact"
        if self.argument_hash is not None:
            payload["arguments_hash"] = self.argument_hash
        return payload


def parse_rule(
    raw: str,
    effect: str,
    match_mode: str = "glob",
    argument_hash: str | None = None,
) -> Rule:
    if effect not in {"allow", "deny"}:
        raise ValueError(f"Invalid rule effect: {effect}")
    if match_mode not in {"glob", "exact"}:
        raise ValueError(f"Invalid permission match mode: {match_mode}")
    match = _RULE_RE.fullmatch(raw.strip())
    if match is None:
        raise ValueError(f"Invalid permission rule: {raw}")
    return Rule(
        match.group(1),
        match.group(2),
        cast(Effect, effect),
        cast(MatchMode, match_mode),
        argument_hash,
    )


def extract_content(tool_name: str, arguments: dict[str, Any]) -> str:
    field = _CONTENT_FIELDS.get(tool_name)
    value = arguments.get(field, "") if field is not None else ""
    return value if isinstance(value, str) else str(value)


def _load_rules_file(path: Path | None) -> list[Rule]:
    if path is None or not path.is_file():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError, UnicodeError):
        return []
    if not isinstance(raw, list):
        return []
    rules: list[Rule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            rule_text = item["rule"]
            effect = item["effect"]
            if not isinstance(rule_text, str) or not isinstance(effect, str):
                continue
            match_mode = item.get("match", "glob")
            if not isinstance(match_mode, str):
                continue
            argument_hash = item.get("arguments_hash")
            if argument_hash is not None and not isinstance(argument_hash, str):
                continue
            rules.append(parse_rule(rule_text, effect, match_mode, argument_hash))
        except (KeyError, ValueError, TypeError):
            continue
    return rules


class RuleEngine:
    def __init__(
        self,
        local_rules_path: str | Path | None = None,
        *,
        user_rules_path: str | Path | None = None,
        project_rules_path: str | Path | None = None,
    ) -> None:
        self.user_rules_path = Path(user_rules_path) if user_rules_path is not None else None
        self.project_rules_path = (
            Path(project_rules_path) if project_rules_path is not None else None
        )
        self.local_rules_path = Path(local_rules_path) if local_rules_path is not None else None
        self._memory_local_rules: list[Rule] = []

    def _load_tiers(self) -> list[list[Rule]]:
        local = _load_rules_file(self.local_rules_path)
        if self.local_rules_path is None:
            local.extend(self._memory_local_rules)
        return [
            _load_rules_file(self.user_rules_path),
            _load_rules_file(self.project_rules_path),
            local,
        ]

    @property
    def rules(self) -> list[Rule]:
        return [rule for tier in self._load_tiers() for rule in tier]

    def evaluate(
        self,
        tool_name: str,
        content: str,
        arguments: dict[str, Any] | None = None,
    ) -> Effect | None:
        for rules in reversed(self._load_tiers()):
            for rule in reversed(rules):
                if rule.matches(tool_name, content, arguments):
                    return rule.effect
        return None

    def allows(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return self.evaluate(tool_name, extract_content(tool_name, arguments), arguments) == "allow"

    def append_local_rule(self, rule: Rule) -> None:
        if self.local_rules_path is None:
            if rule not in self._memory_local_rules:
                self._memory_local_rules.append(rule)
            return
        existing = _load_rules_file(self.local_rules_path)
        existing.append(rule)
        self.local_rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_rules_path.write_text(
            yaml.safe_dump(
                [item.as_yaml() for item in existing],
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )

    def clear_local_rules(self) -> None:
        """Remove only the highest-priority local rule tier."""

        self._memory_local_rules.clear()
        if self.local_rules_path is None:
            return
        self.local_rules_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_rules_path.write_text("[]\n", encoding="utf-8")


__all__ = [
    "Effect",
    "MatchMode",
    "Rule",
    "RuleEngine",
    "_CONTENT_FIELDS",
    "_RULE_RE",
    "_load_rules_file",
    "extract_content",
    "parse_rule",
    "normalize_permission_content",
    "normalize_permission_arguments",
    "permission_argument_hash",
]
