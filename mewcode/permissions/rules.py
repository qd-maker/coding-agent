"""Tiered YAML permission rules."""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

Effect = Literal["allow", "deny"]
_RULE_RE = re.compile(r"^(\w+)\((.+)\)$")
_CONTENT_FIELDS = {
    "Bash": "command",
    "ReadFile": "file_path",
    "WriteFile": "file_path",
    "EditFile": "file_path",
    "Glob": "pattern",
    "Grep": "pattern",
}


@dataclass(frozen=True, slots=True)
class Rule:
    tool_name: str
    pattern: str
    effect: Effect = "allow"

    @property
    def tool(self) -> str:
        """Compatibility alias retained for CH4 callers."""

        return self.tool_name

    @property
    def decision(self) -> str:
        return self.effect

    def matches(self, tool_name: str, content: str) -> bool:
        return self.tool_name == tool_name and fnmatch.fnmatch(content, self.pattern)

    def as_yaml(self) -> dict[str, str]:
        return {"rule": f"{self.tool_name}({self.pattern})", "effect": self.effect}


def parse_rule(raw: str, effect: str) -> Rule:
    if effect not in {"allow", "deny"}:
        raise ValueError(f"Invalid rule effect: {effect}")
    match = _RULE_RE.fullmatch(raw.strip())
    if match is None:
        raise ValueError(f"Invalid permission rule: {raw}")
    return Rule(match.group(1), match.group(2), cast(Effect, effect))


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
            rules.append(parse_rule(rule_text, effect))
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

    def evaluate(self, tool_name: str, content: str) -> Effect | None:
        for rules in reversed(self._load_tiers()):
            for rule in reversed(rules):
                if rule.matches(tool_name, content):
                    return rule.effect
        return None

    def allows(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        return self.evaluate(tool_name, extract_content(tool_name, arguments)) == "allow"

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


__all__ = [
    "Effect",
    "Rule",
    "RuleEngine",
    "_CONTENT_FIELDS",
    "_RULE_RE",
    "_load_rules_file",
    "extract_content",
    "parse_rule",
]
