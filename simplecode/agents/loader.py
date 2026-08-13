"""Priority-aware Agent definition discovery with hot reload."""

from __future__ import annotations

import logging
from importlib import resources
from pathlib import Path

from simplecode.agents.parser import AgentDefinition, AgentParseError, parse_agent_file

log = logging.getLogger(__name__)

PROJECT_AGENTS_DIR = Path(".simplecode/agents")
USER_AGENTS_DIR = Path("~/.simplecode/agents")


class AgentLoader:
    """Load project > user > builtin > plugin definitions; first name wins."""

    def __init__(
        self,
        work_dir: str | Path,
        *,
        plugin_dirs: list[str | Path] | None = None,
        enable_verification: bool = False,
    ) -> None:
        self.work_dir = Path(work_dir).expanduser().resolve()
        self._project_dir = (self.work_dir / PROJECT_AGENTS_DIR).resolve()
        self._user_dir = USER_AGENTS_DIR.expanduser().resolve()
        self._builtin_dir = Path(str(resources.files("simplecode.agents.builtins"))).resolve()
        discovered_plugins = [
            entry / "agents"
            for plugin_root in (
                self.work_dir / ".simplecode" / "plugins",
                Path.home() / ".simplecode" / "plugins",
            )
            if plugin_root.is_dir()
            for entry in sorted(plugin_root.iterdir(), key=lambda item: item.name.casefold())
            if entry.is_dir() and (entry / "agents").is_dir()
        ]
        self._plugin_dirs = [
            Path(path).expanduser().resolve()
            for path in [*(plugin_dirs or []), *discovered_plugins]
        ]
        self.enable_verification = enable_verification
        self._agents: dict[str, AgentDefinition] = {}
        self._cache: dict[str, AgentDefinition] = {}

    @property
    def agents(self) -> dict[str, AgentDefinition]:
        return {definition.agent_type: definition for definition in self._agents.values()}

    def register_plugin_source(self, path: str | Path) -> None:
        root = Path(path).expanduser().resolve()
        if root not in self._plugin_dirs:
            self._plugin_dirs.append(root)

    def _scan_directory(
        self,
        root: Path,
        source: str,
        destination: dict[str, AgentDefinition],
    ) -> None:
        if not root.is_dir():
            return
        for path in sorted(root.glob("*.md"), key=lambda item: item.name.casefold()):
            try:
                definition = parse_agent_file(path, source=source)
            except AgentParseError as exc:
                log.warning("Skipping %s Agent '%s': %s", source, path, exc)
                continue
            key = definition.agent_type.casefold()
            if key == "verification" and not self.enable_verification:
                continue
            destination.setdefault(key, definition)

    def load_all(self) -> dict[str, AgentDefinition]:
        discovered: dict[str, AgentDefinition] = {}
        self._scan_directory(self._project_dir, "project", discovered)
        self._scan_directory(self._user_dir, "user", discovered)
        self._scan_directory(self._builtin_dir, "builtin", discovered)
        for plugin_dir in self._plugin_dirs:
            self._scan_directory(plugin_dir, "plugin", discovered)
        self._agents = discovered
        self._cache.update(discovered)
        return self.agents

    def get(self, name: str) -> AgentDefinition | None:
        key = name.strip().casefold()
        current = self._agents.get(key)
        if current is None:
            return None
        if current.file_path is None or not current.file_path.is_file():
            return current
        try:
            refreshed = parse_agent_file(current.file_path, source=current.source)
        except AgentParseError as exc:
            log.warning("Hot reload failed for Agent '%s': %s", name, exc)
            return self._cache.get(key, current)
        if refreshed.agent_type.casefold() != key:
            log.warning("Hot reload changed Agent name '%s'; keeping cached definition", name)
            return self._cache.get(key, current)
        self._agents[key] = refreshed
        self._cache[key] = refreshed
        return refreshed

    def list_agents(self) -> list[AgentDefinition]:
        return sorted(self._agents.values(), key=lambda item: item.agent_type.casefold())

    def build_catalog_prompt(self) -> str:
        lines = [
            "## Available SubAgents",
            "Use the Agent tool for a bounded independent task. Pass subagent_type exactly; "
            "omit it only when a context-preserving fork is required.",
        ]
        lines.extend(f"- {item.agent_type}: {item.when_to_use}" for item in self.list_agents())
        return "\n".join(lines)


__all__ = ["AgentLoader", "PROJECT_AGENTS_DIR", "USER_AGENTS_DIR"]
