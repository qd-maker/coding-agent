"""CH11 Skill parsing, loading, execution, tools, and command integration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mewcode.agent import Agent, LoopComplete
from mewcode.client import LLMClient
from mewcode.commands import CommandContext, CommandRegistry
from mewcode.commands.handlers import register_all_commands
from mewcode.commands.handlers.skill import SKILL_COMMAND
from mewcode.commands.handlers.skill_register import register_skill_commands
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.skills import (
    SkillDef,
    SkillDependencyError,
    SkillExecutor,
    SkillLoader,
    SkillParseError,
    filter_tool_registry,
    parse_frontmatter,
    parse_skill_file,
    register_skill_tools,
    substitute_arguments,
    validate_skill_dependencies,
)
from mewcode.tools import ToolRegistry, create_default_registry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete, ToolResult
from mewcode.tools.load_skill import LoadSkill


def provider() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "name": "test",
            "protocol": "anthropic",
            "model": "test-model",
            "base_url": "https://example.test",
            "api_key": "test",
        }
    )


class ScriptedClient(LLMClient):
    def __init__(self, scripts: list[list[StreamEvent]]) -> None:
        super().__init__(provider())
        self.scripts = scripts
        self.snapshots: list[list[Any]] = []
        self.tool_schemas: list[list[dict[str, Any]] | None] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del system
        self.snapshots.append(conversation.get_messages())
        self.tool_schemas.append(tools)
        script = self.scripts.pop(0)
        for event in script:
            yield event


class FakeUI:
    def __init__(self) -> None:
        self.messages: list[str] = []
        self.prompts: list[str] = []

    async def add_system_message(self, text: str) -> None:
        self.messages.append(text)

    async def send_user_message(self, text: str) -> None:
        self.prompts.append(text)

    def set_plan_mode(self, enabled: bool) -> None:
        del enabled

    def get_token_count(self) -> int:
        return 0

    def refresh_status(self) -> None:
        return


def write_skill(
    path: Path,
    *,
    name: str,
    description: str = "Example skill",
    mode: str = "inline",
    context: str = "full",
    allowed: str = "[]",
    body: str = "Run $ARGUMENTS",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                f"description: {description}",
                f"allowedTools: {allowed}",
                f"mode: {mode}",
                "model: inherit",
                f"context: {context}",
                "---",
                body,
            ]
        ),
        encoding="utf-8",
    )


def isolate_user_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    user_dir = tmp_path / "user-skills"
    monkeypatch.setattr("mewcode.skills.loader.USER_SKILLS_DIR", user_dir)
    return user_dir


def test_parse_frontmatter_and_skill_definition(tmp_path: Path) -> None:
    path = tmp_path / "deploy.md"
    write_skill(
        path,
        name="deploy-app",
        description="Deploy safely",
        mode="fork",
        context="recent",
        allowed="[Bash, ReadFile]",
    )
    meta, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    skill = parse_skill_file(path)

    assert meta["name"] == "deploy-app"
    assert body == "Run $ARGUMENTS"
    assert skill == SkillDef(
        name="deploy-app",
        description="Deploy safely",
        prompt_body="Run $ARGUMENTS",
        allowed_tools=("Bash", "ReadFile"),
        mode="fork",
        model="inherit",
        context="recent",
        source_path=path.resolve(),
        is_directory=False,
    )


@pytest.mark.parametrize(
    "raw",
    [
        "name: missing-delimiter",
        "---\nname: broken",
        "---\nname: Bad_Name\ndescription: bad\n---\nbody",
        "---\nname: ok\ndescription: bad\nmode: other\n---\nbody",
        "---\nname: ok\ndescription: bad\ncontext: latest\n---\nbody",
        "---\nname: ok\ndescription: bad\nallowedTools: Bash\n---\nbody",
        "---\nname: ok\ndescription: bad\n---\n",
    ],
)
def test_invalid_skill_documents_raise(raw: str, tmp_path: Path) -> None:
    path = tmp_path / "bad.md"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(SkillParseError):
        parse_skill_file(path)


def test_substitute_arguments_only_replaces_placeholder() -> None:
    assert substitute_arguments("Run $ARGUMENTS now", "unit tests") == "Run unit tests now"
    assert substitute_arguments("No placeholder", "ignored") == "No placeholder"


def test_loader_has_exact_three_builtins(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    isolate_user_skills(monkeypatch, tmp_path)
    loader = SkillLoader(tmp_path / "project")
    skills = loader.load_all()

    assert set(skills) == {"commit", "review", "test"}
    assert skills["commit"].mode == "inline"
    assert skills["review"].mode == "fork"
    assert skills["review"].context == "none"
    assert loader.get_source_label("test") == "builtin"
    assert "- commit:" in loader.build_catalog_prompt()
    assert "# Commit SOP" not in loader.build_catalog_prompt()


def test_loader_precedence_directory_layout_and_source_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user = isolate_user_skills(monkeypatch, tmp_path)
    project = tmp_path / "project"
    write_skill(user / "review.md", name="review", description="user review")
    write_skill(
        project / ".mewcode" / "skills" / "review" / "SKILL.md",
        name="review",
        description="project review",
    )
    loader = SkillLoader(project)
    skills = loader.load_all()

    assert skills["review"].description == "project review"
    assert skills["review"].is_directory
    assert loader.get_source_label("review") == "project"


def test_loader_skips_bad_file_and_hot_reload_falls_back_to_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    isolate_user_skills(monkeypatch, tmp_path)
    root = tmp_path / "project" / ".mewcode" / "skills"
    good = root / "custom.md"
    write_skill(good, name="custom", body="version one")
    (root / "bad.md").write_text("not frontmatter", encoding="utf-8")
    loader = SkillLoader(tmp_path / "project")
    assert "custom" in loader.load_all()
    assert "Skipping project skill" in caplog.text

    write_skill(good, name="custom", body="version two")
    assert loader.get("custom").prompt_body == "version two"  # type: ignore[union-attr]
    good.write_text("broken", encoding="utf-8")
    assert loader.get("custom").prompt_body == "version two"  # type: ignore[union-attr]
    assert "Hot reload failed" in caplog.text


@pytest.mark.asyncio
async def test_directory_skill_registers_and_executes_custom_tool(tmp_path: Path) -> None:
    root = tmp_path / "profile"
    write_skill(root / "SKILL.md", name="profile", allowed="[parse_profile]")
    (root / "tool.json").write_text(
        '[{"name":"parse_profile","description":"Parse profile",'
        '"parameters":{"type":"object","properties":{"value":{"type":"string"}}}},'
        '{"name":"broken_profile","description":"Fail predictably"}]',
        encoding="utf-8",
    )
    references = root / "references"
    references.mkdir()
    (references / "parse_profile.py").write_text(
        "async def execute(value='', **kwargs):\n    return 'parsed:' + value\n",
        encoding="utf-8",
    )
    (references / "broken_profile.py").write_text(
        "def execute(**kwargs):\n    raise RuntimeError('broken')\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()

    assert register_skill_tools(root, registry) == 2
    assert register_skill_tools(root, registry) == 0
    schema = next(item for item in registry.get_all_schemas() if item["name"] == "parse_profile")
    assert schema["name"] == "parse_profile"
    assert schema["input_schema"]["properties"]["value"]["type"] == "string"
    result = await registry.execute("parse_profile", {"value": "Ada"})
    assert result == ToolResult("parsed:Ada")
    failed = await registry.execute("broken_profile", {})
    assert failed.is_error and "RuntimeError: broken" in failed.output


def test_dependency_validation_counts_directory_declarations(tmp_path: Path) -> None:
    root = tmp_path / "bundle"
    write_skill(root / "SKILL.md", name="bundle", allowed="[custom_tool]")
    (root / "tool.json").write_text('{"name":"custom_tool"}', encoding="utf-8")
    skill = parse_skill_file(root / "SKILL.md", is_directory=True)
    validate_skill_dependencies([skill], ToolRegistry())
    with pytest.raises(SkillDependencyError, match="custom_tool"):
        validate_skill_dependencies(
            [skill],
            ToolRegistry(),
            allow_declared_directory_tools=False,
        )


def test_filter_registry_is_fail_fast_and_preserves_system_tool() -> None:
    registry = create_default_registry()
    registry.register(LoadSkill())
    filtered = filter_tool_registry(registry, ["ReadFile"])

    assert {tool.name for tool in filtered.list_tools()} == {"ReadFile", "LoadSkill"}
    assert filter_tool_registry(registry, []) is registry
    with pytest.raises(SkillDependencyError, match="MissingTool"):
        filter_tool_registry(registry, ["MissingTool"])


def test_inline_executor_pins_rendered_sop_and_whitelist(tmp_path: Path) -> None:
    client = ScriptedClient([])
    registry = create_default_registry()
    agent = Agent(client, registry=registry, work_dir=tmp_path)
    executor = SkillExecutor(agent)
    skill = SkillDef(
        "custom",
        "custom",
        "Do $ARGUMENTS",
        ("ReadFile",),
    )

    assert executor.execute_inline(skill, "this") == "Do this"
    assert agent.active_skills == {"custom": "Do this"}
    assert agent._active_allowed_tool_names() == {"ReadFile"}


def test_fork_context_modes_full_recent_and_none(tmp_path: Path) -> None:
    agent = Agent(ScriptedClient([]), registry=create_default_registry(), work_dir=tmp_path)
    executor = SkillExecutor(agent)
    parent = ConversationManager()
    for index in range(7):
        parent.add_user_message(f"message-{index}")

    full = executor._build_fork_conversation(
        SkillDef("full", "full", "run", context="full"),
        "rendered-full",
        parent,
    )
    recent = executor._build_fork_conversation(
        SkillDef("recent", "recent", "run", context="recent"),
        "rendered-recent",
        parent,
    )
    none = executor._build_fork_conversation(
        SkillDef("none", "none", "run", context="none"),
        "rendered-none",
        parent,
    )

    assert "message-0" in full.history[0].content
    assert full.history[-1].content == "rendered-full"
    assert [message.content for message in recent.history[:-1]] == [
        f"message-{index}" for index in range(2, 7)
    ]
    assert [message.content for message in none.history] == ["rendered-none"]


@pytest.mark.asyncio
async def test_fork_executor_isolates_history_and_filters_tools(tmp_path: Path) -> None:
    client = ScriptedClient([[TextDelta("forked"), StreamEnd("end_turn", 2, 1)]])
    registry = create_default_registry()
    registry.register(LoadSkill())
    parent = ConversationManager()
    parent.add_user_message("private parent context")
    agent = Agent(client, registry=registry, work_dir=tmp_path, conversation=parent)
    executor = SkillExecutor(agent)
    skill = SkillDef(
        "review",
        "review",
        "Review $ARGUMENTS",
        ("ReadFile",),
        mode="fork",
        context="none",
    )

    result = await executor.execute_fork(skill, "src", parent)

    assert result == "forked"
    assert [message.content for message in parent.history] == ["private parent context"]
    fork_text = "\n".join(message.content for message in client.snapshots[0])
    assert "private parent context" not in fork_text
    assert "Review src" in fork_text
    assert {schema["name"] for schema in client.tool_schemas[0] or []} == {
        "ReadFile",
        "LoadSkill",
    }


@pytest.mark.asyncio
async def test_load_skill_progressive_disclosure_and_agent_whitelist(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    isolate_user_skills(monkeypatch, tmp_path)
    client = ScriptedClient(
        [
            [
                ToolCallComplete("load-1", "LoadSkill", {"name": "commit"}),
                StreamEnd("tool_use", 2, 1),
            ],
            [TextDelta("done"), StreamEnd("end_turn", 3, 1)],
        ]
    )
    registry = create_default_registry()
    load_tool = LoadSkill()
    registry.register(load_tool)
    loader = SkillLoader(tmp_path)
    loader.load_all()
    conversation = ConversationManager()
    conversation.add_user_message("activate a reusable skill")
    agent = Agent(client, registry=registry, work_dir=tmp_path, conversation=conversation)
    agent.set_skill_catalog(loader.build_catalog_prompt())
    load_tool.set_loader(loader)
    load_tool.set_agent(agent)

    events = [event async for event in agent.run(conversation)]

    assert any(isinstance(event, LoopComplete) for event in events)
    first = "\n".join(message.content for message in client.snapshots[0])
    second = "\n".join(message.content for message in client.snapshots[1])
    assert "## Available Skills" in first
    assert "# Commit SOP" not in first
    assert "## Active Skills" in second
    assert "# Commit SOP" in second
    second_tools = {schema["name"] for schema in client.tool_schemas[1] or []}
    assert {"Bash", "ReadFile", "Grep", "LoadSkill"} <= second_tools
    assert "WriteFile" not in second_tools


@pytest.mark.asyncio
async def test_load_directory_skill_registers_specialized_tool(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    isolate_user_skills(monkeypatch, tmp_path)
    root = tmp_path / ".mewcode" / "skills" / "bundle"
    write_skill(root / "SKILL.md", name="bundle", allowed="[bundle_echo]")
    (root / "tool.json").write_text(
        '{"name":"bundle_echo","description":"Echo",'
        '"parameters":{"type":"object","properties":{"text":{"type":"string"}}}}',
        encoding="utf-8",
    )
    references = root / "references"
    references.mkdir()
    (references / "bundle_echo.py").write_text(
        "def execute(text='', **kwargs):\n    return text.upper()\n",
        encoding="utf-8",
    )
    loader = SkillLoader(tmp_path)
    loader.load_all()
    registry = create_default_registry()
    tool = LoadSkill()
    registry.register(tool)
    agent = Agent(ScriptedClient([]), registry=registry, work_dir=tmp_path)
    tool.set_loader(loader)
    tool.set_agent(agent)

    result = await tool.execute(SimpleNamespace(name="bundle"))

    assert not result.is_error
    assert "1 specialized tool(s) registered" in result.output
    assert "bundle" in agent.active_skills
    assert await registry.execute("bundle_echo", {"text": "hello"}) == ToolResult("HELLO")


def test_nested_skill_whitelists_intersect_but_keep_load_skill(tmp_path: Path) -> None:
    registry = create_default_registry()
    registry.register(LoadSkill())
    agent = Agent(ScriptedClient([]), registry=registry, work_dir=tmp_path)
    agent.activate_skill("one", "one", ("ReadFile", "Grep"))
    agent.activate_skill("two", "two", ("ReadFile", "Glob"))

    assert agent._active_allowed_tool_names() == {"ReadFile"}
    assert {schema["name"] for schema in agent._skill_filtered_tool_schemas()} == {
        "ReadFile",
        "LoadSkill",
    }


@pytest.mark.asyncio
async def test_agent_rejects_forged_tool_call_outside_active_skill(tmp_path: Path) -> None:
    client = ScriptedClient(
        [
            [
                ToolCallComplete(
                    "write-1",
                    "WriteFile",
                    {"file_path": str(tmp_path / "blocked.txt"), "content": "no"},
                ),
                StreamEnd("tool_use", 2, 1),
            ],
            [TextDelta("handled"), StreamEnd("end_turn", 2, 1)],
        ]
    )
    agent = Agent(client, registry=create_default_registry(), work_dir=tmp_path)
    agent.activate_skill("read-only", "Only inspect", ("ReadFile",))
    conversation = ConversationManager()
    conversation.add_user_message("try")
    await agent.run_to_completion("", conversation)

    assert not (tmp_path / "blocked.txt").exists()
    result_messages = [message for message in conversation.history if message.tool_results]
    assert "whitelist excludes WriteFile" in result_messages[0].tool_results[0].content


@pytest.mark.asyncio
async def test_skill_commands_register_and_inline_command_executes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    isolate_user_skills(monkeypatch, tmp_path)
    loader = SkillLoader(tmp_path)
    loader.load_all()
    agent = Agent(ScriptedClient([]), registry=create_default_registry(), work_dir=tmp_path)
    executor = SkillExecutor(agent)
    registry = CommandRegistry()
    register_all_commands(registry)
    registry.register_sync(SKILL_COMMAND)

    assert register_skill_commands(registry, loader, executor) == 3
    assert registry.find("review") is not None
    assert "[skill]" in registry.find("review").description  # type: ignore[union-attr]
    assert registry.find("skill") is not None
    ui = FakeUI()
    command = registry.find("commit")
    assert command is not None
    ctx = CommandContext(
        args="focus docs",
        agent=agent,
        conversation=agent.conversation,
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=ui,
        config={"skill_loader": loader},
    )
    await command.handler(ctx)

    assert "# Commit SOP" in agent.active_skills["commit"]
    assert "focus docs" in agent.active_skills["commit"]
    assert ui.prompts == ["focus docs"]


@pytest.mark.asyncio
async def test_skill_management_list_info_and_reload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    isolate_user_skills(monkeypatch, tmp_path)
    loader = SkillLoader(tmp_path)
    loader.load_all()
    registry = CommandRegistry()
    register_all_commands(registry)
    registry.register_sync(SKILL_COMMAND)
    tools = create_default_registry()
    agent = Agent(ScriptedClient([]), registry=tools, work_dir=tmp_path)
    executor = SkillExecutor(agent)
    register_skill_commands(registry, loader, executor)
    ui = FakeUI()
    ctx = CommandContext(
        args="list",
        agent=agent,
        conversation=agent.conversation,
        session=None,
        session_manager=None,
        memory_manager=None,
        ui=ui,
        config={
            "skill_loader": loader,
            "skill_executor": executor,
            "registry": registry,
            "tool_registry": tools,
        },
    )

    await SKILL_COMMAND.handler(ctx)
    assert "/commit" in ui.messages[-1]
    ctx.args = "info test"
    await SKILL_COMMAND.handler(ctx)
    assert "AllowedTools: Bash, ReadFile, Grep, Glob" in ui.messages[-1]
    ctx.args = "reload"
    await SKILL_COMMAND.handler(ctx)
    assert "3 个" in ui.messages[-1]


def test_clear_active_skills_removes_whitelist_state(tmp_path: Path) -> None:
    agent = Agent(ScriptedClient([]), registry=create_default_registry(), work_dir=tmp_path)
    agent.activate_skill("one", "body", ("ReadFile",))
    agent.clear_active_skills()
    assert agent.active_skills == {}
    assert agent._active_allowed_tool_names() is None
