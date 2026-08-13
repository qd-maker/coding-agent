"""YAML configuration loading and validation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)


class ConfigurationError(ValueError):
    """Raised when the YAML configuration cannot be loaded or validated."""


# ---------------------------------------------------------------------------
# ENV helpers
# ---------------------------------------------------------------------------

_ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_WINDOWS_CHILD_ENV_ALLOWLIST = ("SYSTEMROOT", "COMSPEC", "PATHEXT")


def resolve_env_vars(text: str) -> str:
    """Replace every ``${VAR}`` in *text* with its env value; keep missing ones."""

    def _replace(match: re.Match[str]) -> str:
        value = os.environ.get(match.group(1))
        return value if value is not None else match.group(0)

    return _ENV_PLACEHOLDER.sub(_replace, text)


def build_child_env(explicit_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a minimal, non-secret environment for an MCP subprocess."""
    allowed_names = ["PATH"]
    if os.name == "nt":
        allowed_names.extend(_WINDOWS_CHILD_ENV_ALLOWLIST)
    env = {name: os.environ[name] for name in allowed_names if name in os.environ}
    for key, value in (explicit_env or {}).items():
        env[key] = resolve_env_vars(value)
    return env


# ---------------------------------------------------------------------------
# MCPServerConfig
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PROVIDER_KEYS = {
    "name",
    "protocol",
    "model",
    "base_url",
    "api_key",
    "thinking",
    "max_output_tokens",
}


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server (stdio or HTTP)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)
    startup_timeout: float = Field(default=20.0, gt=0, le=300)
    tool_timeout: float = Field(default=120.0, gt=0, le=3600)

    @field_validator("name")
    @classmethod
    def name_ascii_alnum_underscore(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise ValueError(
                f"MCP server name {value!r} must contain only ASCII letters, "
                "digits, and underscores"
            )
        return value

    @model_validator(mode="after")
    def command_xor_url(self) -> MCPServerConfig:
        has_command = self.command is not None
        has_url = self.url is not None
        if has_command and has_url:
            raise ValueError(f"MCP server {self.name!r}: cannot have both 'command' and 'url'")
        if not has_command and not has_url:
            raise ValueError(
                f"MCP server {self.name!r}: must have either 'command' (stdio) or 'url' (HTTP)"
            )
        return self

    @property
    def is_stdio(self) -> bool:
        """True when this server uses stdio transport."""
        return self.command is not None


# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------


class ProviderConfig(BaseModel):
    """Validated configuration required to construct one LLM provider."""

    model_config = ConfigDict(extra="forbid")

    name: str = "default"
    protocol: Literal["anthropic", "openai"]
    model: str = Field(min_length=1)
    base_url: AnyHttpUrl
    api_key: str = Field(min_length=1, repr=False)
    thinking: bool = False
    max_output_tokens: int | None = Field(default=None, ge=1, le=200_000)

    @field_validator("model", "api_key")
    @classmethod
    def strip_non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    def resolve_api_key(self) -> str | None:
        """Resolve supported environment variable references or return a literal key."""
        raw = self.api_key.strip()
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw)
        if match:
            return os.getenv(match.group(1)) or None
        if raw.startswith("env:"):
            name = raw[4:].strip()
            return os.getenv(name) or None if name else None
        if re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", raw):
            return os.getenv(raw[1:]) or None
        return raw

    def get_max_output_tokens(self) -> int:
        """Return the explicit limit or the ch02 thinking-aware default."""
        if self.max_output_tokens is not None:
            return self.max_output_tokens
        return 64_000 if self.thinking else 8_192

    def base_url_string(self) -> str:
        """Return a normalized URL without Pydantic's URL wrapper type."""
        return str(self.base_url).rstrip("/")


class WorktreeConfig(BaseModel):
    """Git worktree isolation and conservative stale-cleanup settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    symlink_directories: list[str] = Field(default_factory=list)
    stale_cleanup_interval: float = Field(default=3600.0, ge=10.0)
    stale_cutoff_hours: float = Field(default=24.0, ge=1.0)


# ---------------------------------------------------------------------------
# AppConfig
# ---------------------------------------------------------------------------


class AppConfig(BaseModel):
    """Top-level Simple Code configuration."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    providers: list[ProviderConfig] = Field(min_length=1)
    system_prompt: str = "You are Simple Code, a concise and helpful coding assistant."
    mcp_servers: list[MCPServerConfig] = Field(default_factory=list)
    raw_hooks: list[dict[str, Any]] = Field(default_factory=list, alias="hooks")
    worktree: WorktreeConfig = Field(default_factory=WorktreeConfig)
    teammate_mode: Literal["", "in-process", "tmux", "iterm2", "auto"] = ""
    enable_coordinator_mode: bool = False

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_provider_shape(cls, value: Any) -> Any:
        """Normalize CH2-CH6 provider shapes into the canonical providers list."""
        if not isinstance(value, dict):
            return value

        raw = dict(value)
        if "providers" in raw:
            if "provider" in raw:
                raise ValueError("cannot configure both 'providers' and legacy 'provider'")
            return raw

        if "provider" in raw:
            raw["providers"] = [raw.pop("provider")]
            return raw

        provider = {key: raw.pop(key) for key in tuple(raw) if key in _PROVIDER_KEYS}
        if provider:
            raw["providers"] = [provider]
        return raw

    @model_validator(mode="after")
    def unique_config_names(self) -> AppConfig:
        provider_names = [provider.name for provider in self.providers]
        if len(provider_names) != len(set(provider_names)):
            raise ValueError("provider names must be unique")

        server_names = [server.name for server in self.mcp_servers]
        if len(server_names) != len(set(server_names)):
            raise ValueError("MCP server names must be unique")
        return self

    @property
    def provider(self) -> ProviderConfig:
        """Return the active provider (the first entry in canonical order)."""
        return self.providers[0]


# ---------------------------------------------------------------------------
# Internal loaders
# ---------------------------------------------------------------------------


def _default_config_candidates() -> list[Path]:
    return [Path.cwd() / "simplecode.yaml", Path.home() / ".simplecode" / "config.yaml"]


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Config file not found: {path}") from exc
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"Cannot read config file {path}: {exc}") from exc
    if raw is None:
        raise ConfigurationError(f"Config file is empty: {path}")
    if not isinstance(raw, dict):
        raise ConfigurationError("Config root must be a YAML mapping")
    return raw


def _parse_mcp_servers(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert YAML map form ``mcp_servers`` into a list."""
    mcp_raw = raw.get("mcp_servers")
    if mcp_raw is None:
        return []
    if isinstance(mcp_raw, list):
        return list(mcp_raw)
    if isinstance(mcp_raw, dict):
        # map form: key is the server name
        result: list[dict[str, Any]] = []
        for name, cfg in mcp_raw.items():
            if cfg is None:
                entry: dict[str, Any] = {}
            elif isinstance(cfg, dict):
                entry = dict(cfg)
            else:
                raise ConfigurationError(f"MCP server {name!r} config must be a YAML mapping")
            entry.setdefault("name", name)
            result.append(entry)
        return result
    raise ConfigurationError(f"'mcp_servers' must be a list or map, got {type(mcp_raw).__name__}")


def load_config(path: str | Path | None = None) -> AppConfig:
    """Load a config path, or discover one in the project/home defaults."""
    if path is None:
        selected = next(
            (candidate for candidate in _default_config_candidates() if candidate.is_file()),
            None,
        )
        if selected is None:
            searched = ", ".join(str(item) for item in _default_config_candidates())
            raise ConfigurationError(f"No config file found; searched: {searched}")
    else:
        selected = Path(path).expanduser().resolve()

    raw = _read_yaml(selected)

    # Normalize mcp_servers from map to list before validation
    mcp_list = _parse_mcp_servers(raw)
    raw["mcp_servers"] = mcp_list

    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc
