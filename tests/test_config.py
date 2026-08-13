"""YAML contract tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from simplecode.__main__ import main
from simplecode.config import ConfigurationError, load_config


def test_print_mode_requires_prompt_before_creating_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    assert main(["-p"]) == 2

    assert "prompt is required" in capsys.readouterr().err.lower()
    assert not (tmp_path / ".simplecode").exists()


def test_load_top_level_yaml_and_resolve_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SIMPLECODE_TEST_KEY", "secret")
    path = tmp_path / "config.yaml"
    path.write_text(
        "\n".join(
            [
                "protocol: openai",
                "model: gpt-5.5",
                "base_url: https://api.openai.com/v1",
                "api_key: ${SIMPLECODE_TEST_KEY}",
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


def test_load_canonical_providers_list_uses_first_entry(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """providers:
  - name: anthropic-official
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: anthropic-key
    model: claude-sonnet-4-6
    thinking: true
  - name: openai-official
    protocol: openai
    base_url: https://api.openai.com/v1
    api_key: openai-key
    model: gpt-5.5
    thinking: false
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert [provider.name for provider in config.providers] == [
        "anthropic-official",
        "openai-official",
    ]
    assert config.provider is config.providers[0]
    assert config.provider.protocol == "anthropic"


def test_providers_list_must_not_be_empty(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("providers: []\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="at least 1 item"):
        load_config(path)


def test_provider_names_must_be_unique(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """providers:
  - &provider
    name: duplicate
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: test-key
    model: claude-sonnet-4-6
  - <<: *provider
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="provider names must be unique"):
        load_config(path)


def test_canonical_and_legacy_provider_shapes_cannot_be_mixed(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """providers:
  - name: canonical
    protocol: anthropic
    base_url: https://api.anthropic.com
    api_key: test-key
    model: claude-sonnet-4-6
provider:
  protocol: openai
  base_url: https://api.openai.com/v1
  api_key: test-key
  model: gpt-5.5
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="cannot configure both"):
        load_config(path)


def test_invalid_yaml_configuration_is_observable(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("protocol: unsupported\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="validation error"):
        load_config(path)


def test_hook_mappings_are_preserved_for_second_stage_validation(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """providers:
  - protocol: anthropic
    model: claude-sonnet-4-6
    base_url: https://api.anthropic.com
    api_key: test-key
hooks:
  - id: architecture
    event: session_start
    action:
      type: prompt
      message: Read ARCHITECTURE.md
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.raw_hooks[0]["id"] == "architecture"


def test_invalid_hook_configuration_exits_before_tui(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """providers:
  - protocol: anthropic
    model: claude-sonnet-4-6
    base_url: https://api.anthropic.com
    api_key: test-key
hooks:
  - event: pre_tool_use
    action:
      type: command
""",
        encoding="utf-8",
    )
    assert main(["--config", str(path)]) == 2
    assert "hook 'pre_tool_use_0'" in capsys.readouterr().err
