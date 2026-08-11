"""Skill definition model and YAML-frontmatter parser."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

import yaml

SkillMode = Literal["inline", "fork"]
SkillContext = Literal["full", "recent", "none"]
_SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


class SkillParseError(ValueError):
    """Raised when one Skill document is structurally invalid."""


@dataclass(frozen=True, slots=True)
class SkillDef:
    name: str
    description: str
    prompt_body: str
    allowed_tools: tuple[str, ...] = ()
    mode: SkillMode = "inline"
    model: str = "inherit"
    context: SkillContext = "full"
    source_path: Path | None = None
    is_directory: bool = False


def parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    """Split a ``---`` delimited YAML header from its Markdown body."""

    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillParseError("Skill must start with YAML frontmatter delimiter '---'")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise SkillParseError("Skill frontmatter is missing its closing '---' delimiter")
    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing])) or {}
    except yaml.YAMLError as exc:
        raise SkillParseError(f"Invalid YAML frontmatter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SkillParseError("Skill frontmatter must be a YAML mapping")
    body = "\n".join(lines[closing + 1 :]).strip()
    return dict(metadata), body


def _validate_meta(meta: dict[str, Any], body: str) -> dict[str, Any]:
    name = meta.get("name")
    description = meta.get("description")
    if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name):
        raise SkillParseError("Skill name must match ^[a-z][a-z0-9-]*$")
    if not isinstance(description, str) or not description.strip():
        raise SkillParseError("Skill description must be a non-empty string")
    if not body:
        raise SkillParseError("Skill Markdown body cannot be empty")

    mode = meta.get("mode", "inline")
    context = meta.get("context", "full")
    model = meta.get("model", "inherit")
    allowed = meta.get("allowedTools", meta.get("allowed_tools", []))
    if mode not in {"inline", "fork"}:
        raise SkillParseError("Skill mode must be 'inline' or 'fork'")
    if context not in {"full", "recent", "none"}:
        raise SkillParseError("Skill context must be 'full', 'recent', or 'none'")
    if not isinstance(model, str) or not model.strip():
        raise SkillParseError("Skill model must be a non-empty string")
    if not isinstance(allowed, list) or any(
        not isinstance(tool, str) or not tool.strip() for tool in allowed
    ):
        raise SkillParseError("Skill allowedTools must be a list of non-empty strings")
    return {
        "name": name,
        "description": description.strip(),
        "mode": mode,
        "context": context,
        "model": model.strip(),
        "allowed_tools": tuple(dict.fromkeys(tool.strip() for tool in allowed)),
    }


def parse_skill_file(
    path: str | Path,
    *,
    is_directory: bool | None = None,
) -> SkillDef:
    """Parse one single-file or directory-entry Skill document."""

    source = Path(path).expanduser().resolve()
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SkillParseError(f"Cannot read Skill file {source}: {exc}") from exc
    meta, body = parse_frontmatter(raw)
    valid = _validate_meta(meta, body)
    directory = source.name.casefold() == "skill.md" if is_directory is None else is_directory
    return SkillDef(
        name=str(valid["name"]),
        description=str(valid["description"]),
        prompt_body=body,
        allowed_tools=cast(tuple[str, ...], valid["allowed_tools"]),
        mode=cast(SkillMode, valid["mode"]),
        model=str(valid["model"]),
        context=cast(SkillContext, valid["context"]),
        source_path=source,
        is_directory=directory,
    )


def substitute_arguments(prompt_body: str, args: str) -> str:
    """Render user arguments without inventing text when no placeholder exists."""

    return prompt_body.replace("$ARGUMENTS", args)


__all__ = [
    "SkillContext",
    "SkillDef",
    "SkillMode",
    "SkillParseError",
    "parse_frontmatter",
    "parse_skill_file",
    "substitute_arguments",
]
