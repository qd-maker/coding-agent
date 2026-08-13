"""Markdown + YAML-frontmatter definitions for reusable sub-agents."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

VALID_MODELS = {"inherit", "sonnet", "opus", "haiku", ""}
VALID_PERMISSION_MODES = {"default", "acceptEdits", "dontAsk", ""}
VALID_ISOLATION_MODES = {"", "worktree"}
_AGENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*$")


class AgentParseError(ValueError):
    """Raised when an Agent definition cannot be parsed safely."""


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Validated sub-agent definition with unknown metadata preserved."""

    agent_type: str
    when_to_use: str
    system_prompt: str
    tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    model: str = "inherit"
    max_turns: int = 50
    permission_mode: str = "default"
    background: bool = False
    isolation: str = ""
    file_path: Path | None = None
    source: str = "project"
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.agent_type

    @property
    def description(self) -> str:
        return self.when_to_use


# Course material uses AgentDef while the public API names it AgentDefinition.
AgentDef = AgentDefinition


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        raise AgentParseError("Agent definition must start with YAML frontmatter '---'")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise AgentParseError("Agent frontmatter is missing its closing '---'")
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError as exc:
        raise AgentParseError(f"Invalid Agent YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise AgentParseError("Agent frontmatter must be a YAML mapping")
    return dict(metadata), "\n".join(lines[closing + 1 :]).strip()


def _string_list(meta: dict[str, Any], *keys: str) -> tuple[str, ...]:
    value: Any = []
    for key in keys:
        if key in meta:
            value = meta[key]
            break
    if value is None:
        return ()
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise AgentParseError(f"{keys[0]} must be a list of non-empty tool names")
    return tuple(dict.fromkeys(item.strip() for item in value))


def _validate_agent_meta(meta: dict[str, Any], body: str) -> dict[str, Any]:
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not _AGENT_NAME_RE.fullmatch(name.strip()):
        raise AgentParseError("Agent name must match ^[A-Za-z][A-Za-z0-9-]*$")
    if not isinstance(description, str) or not description.strip():
        raise AgentParseError("Agent description must be a non-empty string")
    if not body:
        raise AgentParseError("Agent Markdown body cannot be empty")
    model = meta.get("model", "inherit")
    permission_mode = meta.get("permissionMode", meta.get("permission_mode", "default"))
    isolation = meta.get("isolation", "")
    max_turns = meta.get("maxTurns", meta.get("max_turns", 50))
    background = meta.get("background", False)
    if model not in VALID_MODELS:
        raise AgentParseError(f"Agent model must be one of {sorted(VALID_MODELS)!r}")
    if permission_mode not in VALID_PERMISSION_MODES:
        raise AgentParseError(
            f"Agent permissionMode must be one of {sorted(VALID_PERMISSION_MODES)!r}"
        )
    if isolation not in VALID_ISOLATION_MODES:
        raise AgentParseError(f"Agent isolation must be one of {sorted(VALID_ISOLATION_MODES)!r}")
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns <= 0:
        raise AgentParseError("Agent maxTurns must be a positive integer")
    if not isinstance(background, bool):
        raise AgentParseError("Agent background must be a boolean")
    return {
        "name": name.strip(),
        "description": description.strip(),
        "model": model or "inherit",
        "permission_mode": permission_mode or "default",
        "isolation": isolation,
        "max_turns": max_turns,
        "background": background,
        "tools": _string_list(meta, "tools"),
        "disallowed_tools": _string_list(meta, "disallowedTools", "disallowed_tools"),
    }


def parse_agent_file(path: str | Path, *, source: str = "project") -> AgentDefinition:
    document = Path(path).expanduser().resolve()
    try:
        raw = document.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise AgentParseError(f"Cannot read Agent definition {document}: {exc}") from exc
    meta, body = parse_frontmatter(raw)
    valid = _validate_agent_meta(meta, body)
    return AgentDefinition(
        agent_type=str(valid["name"]),
        when_to_use=str(valid["description"]),
        system_prompt=body,
        tools=cast(tuple[str, ...], valid["tools"]),
        disallowed_tools=cast(tuple[str, ...], valid["disallowed_tools"]),
        model=str(valid["model"]),
        max_turns=int(valid["max_turns"]),
        permission_mode=str(valid["permission_mode"]),
        background=bool(valid["background"]),
        isolation=str(valid["isolation"]),
        file_path=document,
        source=source,
        metadata=meta,
    )


__all__ = [
    "AgentDef",
    "AgentDefinition",
    "AgentParseError",
    "VALID_ISOLATION_MODES",
    "VALID_MODELS",
    "VALID_PERMISSION_MODES",
    "parse_agent_file",
    "parse_frontmatter",
]
