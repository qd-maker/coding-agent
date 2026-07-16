"""YAML contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.config import ConfigurationError, load_config


def test_load_top_level_yaml_and_resolve_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEWCODE_TEST_KEY", "secret")
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "protocol: openai",
                "model: gpt-5.5",
                "base_url: https://api.openai.com/v1",
                "api_key: ${MEWCODE_TEST_KEY}",
                "system_prompt: Be concise.",
            ]
        ),
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.provider.protocol == "openai"
    assert config.provider.resolve_api_key() == "secret"
    assert config.system_prompt == "Be concise."


def test_load_nested_provider_yaml(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """provider:
  protocol: anthropic
  model: claude-sonnet-4-6
  base_url: https://api.anthropic.com
  api_key: test-key
  thinking: true
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.provider.thinking is True
    assert config.provider.get_max_output_tokens() == 64_000


def test_invalid_yaml_configuration_is_observable(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("protocol: unsupported\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="validation error"):
        load_config(path)
