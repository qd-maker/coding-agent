"""Persistent AgentTeam and member data contracts."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


class TeamModelError(ValueError):
    """Raised when a team/member name or persisted model is invalid."""


class BackendType(StrEnum):
    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


def sanitize_name(name: str) -> str:
    value = name.strip()
    if not _SAFE_NAME.fullmatch(value) or value in {".", ".."}:
        raise TeamModelError(
            "team and member names must be 1-63 portable ASCII letters, digits, '.', '_' or '-'"
        )
    return value


@dataclass(slots=True)
class TeammateInfo:
    name: str
    agent_id: str
    agent_type: str
    model: str
    worktree_path: str
    backend_type: BackendType
    is_active: bool | None = None
    requires_approval: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["backend_type"] = self.backend_type.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> TeammateInfo:
        return cls(
            name=sanitize_name(str(value["name"])),
            agent_id=str(value["agent_id"]),
            agent_type=str(value.get("agent_type", "general-purpose")),
            model=str(value.get("model", "inherit")),
            worktree_path=str(value.get("worktree_path", "")),
            backend_type=BackendType(str(value.get("backend_type", "in-process"))),
            is_active=value.get("is_active"),
            requires_approval=bool(value.get("requires_approval", False)),
            metadata=dict(value.get("metadata") or {}),
        )


@dataclass(slots=True)
class AgentTeam:
    name: str
    lead_agent_id: str
    members: list[TeammateInfo]
    config_path: Path
    description: str = ""

    def get_member(self, name_or_id: str) -> TeammateInfo | None:
        return next(
            (
                member
                for member in self.members
                if member.name == name_or_id or member.agent_id == name_or_id
            ),
            None,
        )

    def add_member(self, member: TeammateInfo) -> None:
        sanitize_name(member.name)
        if self.get_member(member.name) or self.get_member(member.agent_id):
            raise TeamModelError(f"duplicate team member: {member.name}")
        self.members.append(member)
        self.save()

    def remove_member(self, name_or_id: str) -> TeammateInfo | None:
        member = self.get_member(name_or_id)
        if member is None:
            return None
        self.members.remove(member)
        self.save()
        return member

    def set_member_active(self, name_or_id: str, is_active: bool | None) -> bool:
        member = self.get_member(name_or_id)
        if member is None:
            return False
        member.is_active = is_active
        self.save()
        return True

    def all_idle(self) -> bool:
        return all(member.is_active is False for member in self.members)

    def active_members(self) -> list[TeammateInfo]:
        return [member for member in self.members if member.is_active is not False]

    @property
    def directory(self) -> Path:
        return self.config_path.parent

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "lead_agent_id": self.lead_agent_id,
            "members": [member.to_dict() for member in self.members],
            "config_path": str(self.config_path),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any], path: Path | None = None) -> AgentTeam:
        config_path = path or Path(str(value["config_path"]))
        return cls(
            name=sanitize_name(str(value["name"])),
            lead_agent_id=str(value["lead_agent_id"]),
            members=[TeammateInfo.from_dict(dict(item)) for item in value.get("members", [])],
            config_path=config_path.resolve(),
            description=str(value.get("description", "")),
        )

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.config_path)

    @classmethod
    def load(cls, path: str | Path) -> AgentTeam:
        source = Path(path).resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TeamModelError("team config must be a JSON object")
        return cls.from_dict(raw, source)


def resolve_team_dir(name: str, teams_root: str | Path | None = None) -> Path:
    root = (
        Path(teams_root).expanduser().resolve()
        if teams_root is not None
        else Path.home() / ".simplecode" / "teams"
    )
    return root / sanitize_name(name).casefold()


def unique_team_name(name: str, teams_root: str | Path | None = None) -> str:
    base = sanitize_name(name)
    candidate = base
    index = 2
    while resolve_team_dir(candidate, teams_root).exists():
        suffix = f"-{index}"
        candidate = f"{base[: 63 - len(suffix)]}{suffix}"
        index += 1
    return candidate


__all__ = [
    "AgentTeam",
    "BackendType",
    "TeamModelError",
    "TeammateInfo",
    "resolve_team_dir",
    "sanitize_name",
    "unique_team_name",
]
