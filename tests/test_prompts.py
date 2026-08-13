"""CH5 prompt composition and Agent integration tests."""

from __future__ import annotations

import platform
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from simplecode.agent import Agent
from simplecode.client import LLMClient
from simplecode.config import ProviderConfig
from simplecode.conversation import ConversationManager
from simplecode.prompts import (
    PromptBuilder,
    PromptSection,
    build_environment_context,
    build_plan_mode_reminder,
    build_system_prompt,
    environment_section,
)
from simplecode.tools.base import StreamEnd, StreamEvent, TextDelta


def provider() -> ProviderConfig:
    return ProviderConfig.model_validate(
        {
            "protocol": "anthropic",
            "model": "claude-sonnet-4-6",
            "base_url": "https://example.com",
            "api_key": "test-key",
        }
    )


class RecordingClient(LLMClient):
    def __init__(self) -> None:
        super().__init__(provider())
        self.system_prompts: list[str] = []
        self.snapshots: list[list[str]] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del tools
        self.system_prompts.append(system or "")
        self.snapshots.append([message.content for message in conversation.get_messages()])
        yield TextDelta("done")
        yield StreamEnd("end_turn", 4, 1)


def test_prompt_builder_sorts_ignores_empty_and_chains() -> None:
    builder = PromptBuilder()
    returned = builder.add(PromptSection("late", 20, "  late  "))
    builder.add(PromptSection("empty", 0, " \n ")).add(PromptSection("early", 10, "early"))

    assert returned is builder
    assert builder.build() == "early\n\nlate"


def test_environment_section_contains_stable_fields(tmp_path: Path) -> None:
    section = environment_section(tmp_path)

    assert section.name == "Environment"
    assert section.priority == 70
    assert section.content.splitlines() == [
        "# Environment",
        f"Working directory: {tmp_path.resolve()}",
        f"Platform: {platform.system()} {platform.release()}".rstrip(),
        f"Date: {datetime.now().strftime('%Y-%m-%d')}",
    ]


def test_build_system_prompt_orders_optional_sections_and_hooks(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        hook_prompts=["first hook", " second hook "],
        custom_instructions="project rules",
        skill_section="available skills",
        memory_section="remember this",
        work_dir=tmp_path,
    )

    headings = [
        "# Identity",
        "# System",
        "# Doing Tasks",
        "# Executing Actions",
        "# Using Tools",
        "# Tone and Style",
        "# Text Output",
        "# Environment",
        "# Custom Instructions",
        "# Skills",
        "# Memory",
        "# Hook Injected Context",
    ]
    positions = [prompt.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert prompt.endswith("# Hook Injected Context\nfirst hook\nsecond hook")
    assert "IMPORTANT: Be careful not to introduce security" in prompt
    assert "<system-reminder>" in prompt
    assert "Only use emojis if the user explicitly requests it" in prompt
    assert "file_path:line_number" in prompt
    assert "Do not use a colon before tool calls" in prompt


def test_build_system_prompt_omits_empty_optional_sections(tmp_path: Path) -> None:
    prompt = build_system_prompt(work_dir=tmp_path)

    assert "# Custom Instructions" not in prompt
    assert "# Skills" not in prompt
    assert "# Memory" not in prompt
    assert "# Hook Injected Context" not in prompt


def test_coordinator_prompt_delegates_identity_and_keeps_extensions(tmp_path: Path) -> None:
    prompt = build_system_prompt(
        coordinator_mode=True,
        agent_catalog="reviewer: reviews code",
        custom_instructions="project rules",
        hook_prompts=["runtime note"],
        work_dir=tmp_path,
    )

    assert "You are the coordinator" in prompt
    assert "reviewer: reviews code" in prompt
    assert f"Working directory: {tmp_path.resolve()}" in prompt
    assert "# Custom Instructions\nproject rules" in prompt
    assert prompt.endswith("# Hook Injected Context\nruntime note")


def test_environment_context_includes_catalogs_and_active_skills(tmp_path: Path) -> None:
    context = build_environment_context(
        tmp_path,
        active_skills={"review": "Read the diff and report risks."},
        skill_catalog="## Available Skills\n- review",
        agent_catalog="## Available Agents\n- reviewer",
    )

    assert context.startswith("<environment>\n")
    assert f"Current working directory: {tmp_path.resolve()}" in context
    assert "Operating system:" in context
    assert "Current time:" in context
    assert context.index("## Available Agents") < context.index("## Available Skills")
    assert "## Active Skills\n### Skill: review\nRead the diff and report risks." in context
    assert context.endswith("</environment>")


def test_plan_mode_reminders_are_dynamic_and_writeplan_only(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan" / "demo.md"
    first = build_plan_mode_reminder(plan_path, False, 1)
    periodic = build_plan_mode_reminder(plan_path, True, 5)
    sparse = build_plan_mode_reminder(plan_path, True, 8)

    assert "Plan Mode is active" in first
    assert "MUST NOT" in first
    assert "five-stage workflow" in first
    assert "WritePlan" in first and "does not exist" in first
    assert "WritePlan" in periodic and "currently exists" in periodic
    assert "Plan mode still active" in sparse
    assert str(plan_path.resolve()) in sparse


@pytest.mark.asyncio
async def test_agent_uses_ch5_prompt_and_environment_in_run_to_completion(
    tmp_path: Path,
) -> None:
    client = RecordingClient()
    agent = Agent(
        client,
        work_dir=tmp_path,
        system="project system rule",
        active_skills={"review": "Review before editing."},
        skill_catalog="## Available Skills\n- review",
        agent_catalog="## Available Agents\n- reviewer",
        hook_prompts=["hook context"],
    )

    result = await agent.run_to_completion("hello")

    assert result == "done"
    assert "# Identity" in client.system_prompts[0]
    assert "# Custom Instructions\nproject system rule" in client.system_prompts[0]
    assert client.system_prompts[0].endswith("# Hook Injected Context\nhook context")
    assert client.snapshots[0][0].startswith("<environment>")
    assert "## Active Skills\n### Skill: review\nReview before editing." in client.snapshots[0][0]


@pytest.mark.asyncio
async def test_agent_reinjects_environment_and_memory_after_compact(tmp_path: Path) -> None:
    class MemoryManager:
        def get_memories(self) -> list[str]:
            return ["remembered fact"]

    conversation = ConversationManager()
    for index in range(8):
        conversation.add_user_message(f"old message {index}")
    # Force Layer 2 auto_compact: threshold for window 50k is 17k.
    conversation.last_input_tokens = 40_000
    client = RecordingClient()
    agent = Agent(
        client,
        work_dir=tmp_path,
        context_window=50_000,
        instructions_content="project instructions",
        memory_manager=MemoryManager(),
    )

    result = await agent.run_to_completion("continue", conversation)

    assert result == "done"
    # First stream call is the summarizer; the post-compact agent turn is last.
    assert len(client.snapshots) >= 2
    post_compact = client.snapshots[-1]
    assert any(content.startswith("<environment>") for content in post_compact)
    assert "## 项目指令\nproject instructions" in post_compact
    assert "## 自动记忆\nremembered fact" in post_compact
    assert "好的，我已了解项目背景和记忆。" in post_compact
    assert any(content.startswith("[摘要]") for content in post_compact)


@pytest.mark.asyncio
async def test_agent_auto_compacts_from_local_token_estimate(tmp_path: Path) -> None:
    conversation = ConversationManager()
    conversation.add_user_message("A" * 4_500)
    assert conversation.last_input_tokens == 0
    client = RecordingClient()
    agent = Agent(
        client,
        work_dir=tmp_path,
        context_window=34_000,  # automatic threshold is 1,000 tokens
    )

    result = await agent.run_to_completion("", conversation)

    assert result == "done"
    assert len(client.snapshots) == 2
    assert any(content.startswith("[摘要]") for content in client.snapshots[-1])
    assert conversation.last_input_tokens > 0
