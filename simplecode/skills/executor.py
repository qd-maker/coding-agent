"""Skill execution, dependency validation, and tool-whitelist filtering."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING

from simplecode.client import LLMClient
from simplecode.conversation import ConversationManager, Message
from simplecode.skills.directory import declared_skill_tool_names, register_skill_tools
from simplecode.skills.parser import SkillDef, substitute_arguments
from simplecode.tools import ToolRegistry

if TYPE_CHECKING:
    from simplecode.agent import Agent

SYSTEM_TOOL_NAMES = frozenset({"LoadSkill"})


class SkillDependencyError(RuntimeError):
    """Raised before execution when a Skill names unavailable tools."""


def _system_tool_names(registry: ToolRegistry) -> set[str]:
    return {
        tool.name
        for tool in registry.list_tools()
        if tool.is_system_tool or tool.name in SYSTEM_TOOL_NAMES
    }


def filter_tool_registry(
    registry: ToolRegistry,
    allowed: Iterable[str],
) -> ToolRegistry:
    """Create a least-privilege registry while always preserving system tools."""

    requested = tuple(dict.fromkeys(name.strip() for name in allowed if name.strip()))
    if not requested:
        return registry
    available = {tool.name: tool for tool in registry.list_tools()}
    missing = sorted(set(requested) - available.keys())
    if missing:
        raise SkillDependencyError("Missing Skill tool dependencies: " + ", ".join(missing))

    visible = set(requested) | _system_tool_names(registry)
    filtered = ToolRegistry()
    for name, tool in available.items():
        if name not in visible:
            continue
        filtered.register(tool)
        if not registry.is_enabled(name):
            filtered.disable(name)
        if registry.is_discovered(name):
            filtered.mark_discovered(name)
    return filtered


def validate_skill_dependencies(
    skills: Iterable[SkillDef],
    registry: ToolRegistry,
    *,
    allow_declared_directory_tools: bool = True,
) -> None:
    """Validate every non-empty whitelist, optionally counting tool.json declarations."""

    registered = {tool.name for tool in registry.list_tools()}
    for skill in skills:
        provided: set[str] = set()
        if allow_declared_directory_tools and skill.is_directory and skill.source_path is not None:
            provided = declared_skill_tool_names(skill.source_path.parent)
        missing = sorted(set(skill.allowed_tools) - registered - provided)
        if missing:
            raise SkillDependencyError(
                f"Skill '{skill.name}' requires unavailable tools: {', '.join(missing)}"
            )


def _message_text(message: Message) -> str:
    if message.content:
        return f"{message.role}: {message.content}"
    if message.tool_uses:
        return "assistant tools: " + ", ".join(tool.name for tool in message.tool_uses)
    if message.tool_results:
        return "tool results: " + "\n".join(result.content for result in message.tool_results)
    return ""


class SkillExecutor:
    def __init__(
        self,
        agent: Agent,
        client: LLMClient | None = None,
        protocol: str | None = None,
    ) -> None:
        self.agent = agent
        self.client = client or agent.client
        self.protocol = protocol or agent.protocol

    def _prepare_directory_tools(self, skill: SkillDef) -> int:
        if not skill.is_directory or skill.source_path is None:
            return 0
        return register_skill_tools(skill.source_path.parent, self.agent.registry)

    def execute_inline(self, skill: SkillDef, arguments: str = "") -> str:
        """Activate a rendered SOP in the parent Agent without calling the LLM."""

        self._prepare_directory_tools(skill)
        validate_skill_dependencies(
            [skill],
            self.agent.registry,
            allow_declared_directory_tools=False,
        )
        rendered = substitute_arguments(skill.prompt_body, arguments)
        self.agent.activate_skill(skill.name, rendered, skill.allowed_tools)
        self.agent.recovery_state.record_skill_invocation(skill.name, rendered)
        return rendered

    def _build_fork_conversation(
        self,
        skill: SkillDef,
        trigger: str,
        parent: ConversationManager,
    ) -> ConversationManager:
        fork = ConversationManager()
        if skill.context == "full":
            transcript = "\n".join(
                text for message in parent.history if (text := _message_text(message))
            )
            if transcript:
                fork.add_user_message("## Previous conversation summary\n\n" + transcript)
        elif skill.context == "recent":
            fork.history.extend(deepcopy(parent.history[-5:]))
        fork.add_user_message(trigger)
        return fork

    async def execute_fork(
        self,
        skill: SkillDef,
        arguments: str = "",
        parent_conversation: ConversationManager | None = None,
    ) -> str:
        """Run a Skill in an isolated Agent and return only its generated result."""

        from simplecode.agent import Agent as AgentClass
        from simplecode.agent import ErrorEvent, LoopComplete, StreamText

        self._prepare_directory_tools(skill)
        validate_skill_dependencies(
            [skill],
            self.agent.registry,
            allow_declared_directory_tools=False,
        )
        rendered = substitute_arguments(skill.prompt_body, arguments)
        parent = parent_conversation or self.agent.conversation
        trigger = arguments.strip() or f"Execute the isolated Skill '{skill.name}' now."
        fork_conversation = self._build_fork_conversation(skill, trigger, parent)
        filtered = filter_tool_registry(self.agent.registry, skill.allowed_tools)
        fork_agent = AgentClass(
            client=self.client,
            registry=filtered,
            protocol=self.protocol,
            work_dir=Path(self.agent.work_dir),
            max_iterations=self.agent.max_iterations,
            context_window=self.agent.context_window,
            conversation=fork_conversation,
            system=f"You are executing the isolated Skill '{skill.name}'. Follow its SOP strictly.",
        )
        fork_agent.activate_skill(skill.name, rendered, skill.allowed_tools)
        from simplecode.context import clone_replacement_state

        fork_agent.replacement_state = clone_replacement_state(self.agent.replacement_state)
        self.agent.recovery_state.record_skill_invocation(skill.name, rendered)

        parts: list[str] = []
        async for event in fork_agent.run(fork_conversation):
            if isinstance(event, StreamText):
                parts.append(event.text)
            elif isinstance(event, ErrorEvent):
                parts.append(f"\n[Skill error] {event.message}")
            elif isinstance(event, LoopComplete):
                break
        return "".join(parts).strip()


__all__ = [
    "SYSTEM_TOOL_NAMES",
    "SkillDependencyError",
    "SkillExecutor",
    "filter_tool_registry",
    "validate_skill_dependencies",
]
