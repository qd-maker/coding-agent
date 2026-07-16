"""YAML configuration loading and validation."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigurationError(ValueError):
    """Raised when the YAML configuration cannot be loaded or validated."""


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


class AppConfig(BaseModel):
    """Top-level MewCode configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: ProviderConfig
    system_prompt: str = "You are MewCode, a concise and helpful coding assistant."


def _default_config_candidates() -> list[Path]:
    return [Path.cwd() / "mewcode.yaml", Path.home() / ".mewcode" / "config.yaml"]


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
    if "provider" not in raw:
        provider_keys = {
            "name",
            "protocol",
            "model",
            "base_url",
            "api_key",
            "thinking",
            "max_output_tokens",
        }
        provider = {key: raw.pop(key) for key in tuple(raw) if key in provider_keys}
        raw["provider"] = provider
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc
