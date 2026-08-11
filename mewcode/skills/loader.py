"""Three-tier Skill discovery with same-name override and hot reload."""

from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path

from mewcode.skills.parser import SkillDef, SkillParseError, parse_skill_file

log = logging.getLogger(__name__)

PROJECT_SKILLS_DIR = Path(".mewcode/skills")
USER_SKILLS_DIR = Path("~/.mewcode/skills")


class SkillLoader:
    def __init__(self, work_dir: str | Path) -> None:
        self.work_dir = Path(work_dir).expanduser().resolve()
        self._project_dir = (self.work_dir / PROJECT_SKILLS_DIR).resolve()
        self._user_dir = USER_SKILLS_DIR.expanduser().resolve()
        self._builtin_dir = Path(str(resources.files("mewcode.skills.builtins"))).resolve()
        self._skills: dict[str, SkillDef] = {}
        self._cache: dict[str, SkillDef] = {}

    @property
    def skills(self) -> dict[str, SkillDef]:
        return dict(self._skills)

    def load_all(self) -> dict[str, SkillDef]:
        """Rescan all tiers; the first definition wins."""

        discovered: dict[str, SkillDef] = {}
        self._scan_directory(self._project_dir, "project", discovered)
        self._scan_directory(self._user_dir, "user", discovered)
        self._scan_directory(self._builtin_dir, "builtin", discovered)
        self._skills = discovered
        self._cache.update(discovered)
        return dict(discovered)

    def _scan_directory(
        self,
        root: Path,
        source: str,
        destination: dict[str, SkillDef],
    ) -> None:
        if not root.is_dir():
            return
        candidates: list[tuple[Path, bool]] = [
            (path, False) for path in sorted(root.glob("*.md"), key=lambda item: item.name)
        ]
        candidates.extend(
            (entry / "SKILL.md", True)
            for entry in sorted(root.iterdir(), key=lambda item: item.name)
            if entry.is_dir() and (entry / "SKILL.md").is_file()
        )
        for path, is_directory in candidates:
            try:
                skill = parse_skill_file(path, is_directory=is_directory)
            except SkillParseError as exc:
                log.warning("Skipping %s skill '%s': %s", source, path, exc)
                continue
            if skill.name in destination:
                continue
            destination[skill.name] = skill

    def get(self, name: str) -> SkillDef | None:
        """Reload one Skill from disk, falling back to the last valid value."""

        normalized = name.strip().casefold()
        current = self._skills.get(normalized)
        if current is None or current.source_path is None:
            return None
        try:
            refreshed = parse_skill_file(
                current.source_path,
                is_directory=current.is_directory,
            )
        except SkillParseError as exc:
            log.warning("Hot reload failed for skill '%s': %s", normalized, exc)
            return self._cache.get(normalized, current)
        if refreshed.name != normalized:
            log.warning(
                "Hot reload changed skill name '%s' to '%s'; keeping cached definition",
                normalized,
                refreshed.name,
            )
            return self._cache.get(normalized, current)
        self._skills[normalized] = refreshed
        self._cache[normalized] = refreshed
        return refreshed

    def get_catalog(self) -> list[tuple[str, str]]:
        return sorted(
            ((skill.name, skill.description) for skill in self._skills.values()),
            key=lambda item: item[0],
        )

    def build_catalog_prompt(self) -> str:
        lines = [
            "## Available Skills",
            "Only summaries are loaded. When user intent matches one, call LoadSkill "
            "with its exact name.",
        ]
        lines.extend(f"- {name}: {description}" for name, description in self.get_catalog())
        return "\n".join(lines)

    def get_source_label(self, name: str) -> str:
        skill = self._skills.get(name.strip().casefold())
        if skill is None or skill.source_path is None:
            return "unknown"
        source = skill.source_path.resolve()
        for label, root in (
            ("project", self._project_dir),
            ("user", self._user_dir),
            ("builtin", self._builtin_dir),
        ):
            try:
                source.relative_to(root)
            except ValueError:
                continue
            return label
        return "unknown"


__all__ = ["PROJECT_SKILLS_DIR", "USER_SKILLS_DIR", "SkillLoader"]
