"""Provider-neutral, event-driven Agent loop."""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from mewcode.client import LLMClient
from mewcode.context import (
    MAX_OUTPUT_CHARS,
    SINGLE_RESULT_CHAR_LIMIT,
    CompactCircuitBreaker,
    CompactEvent,
    apply_tool_result_budget,
    auto_compact,
    ensure_session_dir,
    make_persisted_preview,
    persist_tool_result,
)
from mewcode.conversation import (
    ConversationManager,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.hooks import HookContext, HookEngine
from mewcode.permissions import (
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    Rule,
    RuleEngine,
    extract_content,
)
from mewcode.prompts import (
    build_environment_context,
    build_plan_mode_exit_reminder,
    build_plan_mode_reminder,
    build_system_prompt,
)
from mewcode.tools import ToolRegistry, register_task_tools
from mewcode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)
from mewcode.tools.write_plan import WritePlanTool

MEMORY_EXTRACTION_INTERVAL = 5
MAX_TOKENS_CEILING = 64_000
MAX_OUTPUT_TOKENS_RECOVERIES = 3

_ADJECTIVES = (
    "amber",
    "brisk",
    "calm",
    "clear",
    "coral",
    "cosmic",
    "crisp",
    "daring",
    "eager",
    "gentle",
    "golden",
    "lively",
    "lucid",
    "mellow",
    "nimble",
    "quiet",
    "rapid",
    "silver",
    "steady",
    "sunny",
    "tidy",
    "vivid",
    "warm",
    "wise",
)
_NOUNS = (
    "badger",
    "beacon",
    "brook",
    "cedar",
    "comet",
    "delta",
    "falcon",
    "forest",
    "harbor",
    "heron",
    "island",
    "lantern",
    "maple",
    "meadow",
    "meteor",
    "otter",
    "pebble",
    "pine",
    "river",
    "sparrow",
    "summit",
    "tiger",
    "willow",
    "zephyr",
)


@dataclass(frozen=True, slots=True)
class StreamText:
    text: str


@dataclass(frozen=True, slots=True)
class ThinkingText:
    text: str
    complete: bool = False


@dataclass(frozen=True, slots=True)
class RetryEvent:
    reason: str


@dataclass(frozen=True, slots=True)
class ToolUseEvent:
    tool_id: str
    tool_name: str
    status: Literal["start", "complete", "result"]
    detail: str = ""
    is_error: bool = False
    arguments: dict[str, Any] | None = None
    elapsed_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class ToolResultEvent(ToolUseEvent):
    """Completed tool event; subclasses ToolUseEvent for ch03 compatibility."""


@dataclass(frozen=True, slots=True)
class TurnComplete:
    stop_reason: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class LoopComplete:
    stop_reason: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class UsageEvent:
    input_tokens: int
    output_tokens: int
    total_input_tokens: int
    total_output_tokens: int


@dataclass(frozen=True, slots=True)
class ErrorEvent:
    message: str


@dataclass(frozen=True, slots=True)
class CompactNotification:
    removed_messages: int
    summary: str


@dataclass(frozen=True, slots=True)
class HookEvent:
    hook_name: str
    message: str


class PermissionResponse(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ALLOW_ALWAYS = "allow_always"


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    tool_id: str
    tool_name: str
    arguments: dict[str, Any]
    future: asyncio.Future[PermissionResponse]


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | TurnComplete
    | LoopComplete
    | UsageEvent
    | ErrorEvent
    | PermissionRequest
    | CompactNotification
    | HookEvent
)


@dataclass(slots=True)
class LLMResponse:
    text: str = ""
    thinking_blocks: list[ThinkingBlock] = field(default_factory=list)
    tool_calls: list[ToolCallComplete] = field(default_factory=list)
    stop_reason: str = "end_turn"
    input_tokens: int = 0
    output_tokens: int = 0


class StreamCollector:
    """Fold provider stream events into one response while forwarding UI events."""

    def __init__(self) -> None:
        self._response = LLMResponse()
        self._text_parts: list[str] = []
        self._thinking_parts: list[str] = []

    async def consume(self, stream: AsyncIterator[StreamEvent]) -> AsyncIterator[AgentEvent]:
        async for event in stream:
            if isinstance(event, TextDelta):
                self._text_parts.append(event.text)
                yield StreamText(event.text)
            elif isinstance(event, ThinkingDelta):
                self._thinking_parts.append(event.text)
                yield ThinkingText(event.text)
            elif isinstance(event, ThinkingComplete):
                text = event.thinking or "".join(self._thinking_parts)
                self._response.thinking_blocks.append(ThinkingBlock(text, event.signature))
                self._thinking_parts.clear()
                yield ThinkingText("", complete=True)
            elif isinstance(event, ToolCallStart):
                yield ToolUseEvent(event.tool_id, event.tool_name, "start")
            elif isinstance(event, ToolCallDelta):
                continue
            elif isinstance(event, ToolCallComplete):
                self._response.tool_calls.append(event)
                yield ToolUseEvent(
                    event.tool_id,
                    event.tool_name,
                    "complete",
                    arguments=dict(event.arguments),
                )
            elif isinstance(event, StreamEnd):
                self._response.stop_reason = event.stop_reason
                self._response.input_tokens = event.input_tokens
                self._response.output_tokens = event.output_tokens
            else:  # pragma: no cover
                raise TypeError(f"Unhandled stream event: {type(event).__name__}")

    def response(self) -> LLMResponse:
        self._response.text = "".join(self._text_parts)
        return self._response


@dataclass(frozen=True, slots=True)
class ToolBatch:
    concurrent: bool
    calls: list[ToolCallComplete]


def partition_tool_calls(
    tool_calls: list[ToolCallComplete], registry: ToolRegistry
) -> list[ToolBatch]:
    """Group adjacent enabled, concurrency-safe tools; serialize every other call."""
    batches: list[ToolBatch] = []
    for call in tool_calls:
        tool = registry.get(call.tool_name)
        safe = bool(
            tool is not None and registry.is_enabled(call.tool_name) and tool.is_concurrency_safe
        )
        if safe and batches and batches[-1].concurrent:
            batches[-1].calls.append(call)
        else:
            batches.append(ToolBatch(concurrent=safe, calls=[call]))
    return batches


@dataclass(frozen=True, slots=True)
class _ToolExecResult:
    tool_id: str
    tool_name: str
    output: str
    elapsed: float
    is_error: bool = False
    is_unknown: bool = False


class StreamingExecutor:
    """Collect concurrently started tool coroutines in submission order."""

    def __init__(self) -> None:
        self._order = 0
        self._tasks: list[tuple[int, asyncio.Task[_ToolExecResult]]] = []

    def submit(self, coro: Coroutine[Any, Any, _ToolExecResult]) -> None:
        self._tasks.append((self._order, asyncio.create_task(coro)))
        self._order += 1

    async def collect_results(self) -> list[_ToolExecResult]:
        ordered = sorted(self._tasks, key=lambda item: item[0])
        gathered = await asyncio.gather(
            *(task for _, task in ordered),
            return_exceptions=True,
        )
        results: list[_ToolExecResult] = []
        for value in gathered:
            if isinstance(value, BaseException):
                results.append(
                    _ToolExecResult(
                        "",
                        "",
                        f"Tool execution error: {type(value).__name__}: {value}",
                        0.0,
                        is_error=True,
                    )
                )
            else:
                results.append(value)
        self._tasks.clear()
        return results


class Agent:
    """Run LLM/tool iterations and expose every state transition as an event."""

    def __init__(
        self,
        client: LLMClient,
        registry: ToolRegistry | None = None,
        protocol: str | None = None,
        work_dir: str | Path = ".",
        max_iterations: int = 50,
        permission_checker: PermissionChecker | None = None,
        context_window: int = 200_000,
        instructions_content: str = "",
        memory_manager: Any | None = None,
        hook_engine: HookEngine | None = None,
        *,
        system: str = "",
        tools: ToolRegistry | None = None,
        conversation: ConversationManager | None = None,
        max_steps: int | None = None,
        coordinator_mode: bool = False,
        team_name: str | None = None,
        team_manager: Any | None = None,
        active_skills: dict[str, str] | None = None,
        skill_catalog: str = "",
        agent_catalog: str = "",
        hook_prompts: list[str] | None = None,
        skill_section: str = "",
        memory_section: str = "",
    ) -> None:
        if registry is not None and tools is not None:
            raise ValueError("Pass registry or tools, not both")
        self.client = client
        self.registry = registry or tools or ToolRegistry()
        self.tools = self.registry
        self.protocol = protocol or client.config.protocol
        self.work_dir = Path(work_dir).resolve()
        self.max_iterations = max_steps if max_steps is not None else max_iterations
        self.max_steps = self.max_iterations
        self.context_window = context_window
        self.instructions_content = instructions_content
        self.memory_manager = memory_manager
        self.hook_engine = hook_engine or HookEngine()
        self.system = system
        self.conversation = conversation or ConversationManager()
        self.agent_id = uuid.uuid4().hex[:12]
        self.session_dir = ensure_session_dir(self.work_dir)
        self.compact_breaker = CompactCircuitBreaker()
        self.permission_checker = permission_checker or PermissionChecker(
            sandbox=PathSandbox(self.work_dir),
            rule_engine=RuleEngine(
                user_rules_path=Path.home() / ".mewcode" / "permissions.yaml",
                project_rules_path=self.work_dir / ".mewcode" / "permissions.yaml",
                local_rules_path=self.work_dir / ".mewcode" / "permissions.local.yaml",
            ),
            # Library callers without a UI have no HITL future consumer. The TUI injects
            # its own DEFAULT checker; standalone Agent usage remains non-blocking.
            mode=PermissionMode.BYPASS,
        )
        self.permission_mode = self.permission_checker.mode
        self.coordinator_mode = coordinator_mode
        self.team_name = team_name
        self._team_manager = team_manager
        self.active_skills = dict(active_skills or {})
        self._skill_catalog = skill_catalog
        self._agent_catalog = agent_catalog
        self.hook_prompts = list(hook_prompts or [])
        self.skill_section = skill_section
        self.memory_section = memory_section
        self._plan_path_cache: Path | None = None
        self._mode_transition_reminder: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._completed_turns = 0
        if team_manager is not None and team_name:
            register_task_tools(self.registry, team_manager, team_name)
        self.set_permission_mode(self.permission_mode)

    def set_permission_mode(self, mode: PermissionMode) -> None:
        previous = self.permission_mode
        if mode is PermissionMode.PLAN:
            self._ensure_plan_tool()
            self.permission_checker.plan_file_path = str(self._get_plan_path())
            self._mode_transition_reminder = None
        elif previous is PermissionMode.PLAN:
            self._mode_transition_reminder = build_plan_mode_exit_reminder()
        self.registry.set_plan_mode(mode is PermissionMode.PLAN)
        self.permission_mode = mode
        self.permission_checker.mode = mode

    def _ensure_plan_tool(self) -> None:
        if any(tool.name == WritePlanTool.name for tool in self.registry.list_tools()):
            return
        self.registry.register(WritePlanTool(self._get_plan_path()))

    def _get_plan_path(self) -> Path:
        if self._plan_path_cache is None:
            plans_dir = self.work_dir / "plan"
            plans_dir.mkdir(parents=True, exist_ok=True)
            slug = (
                f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}-"
                f"{datetime.now().strftime('%m%d-%H%M')}"
            )
            self._plan_path_cache = plans_dir / f"{slug}.md"
        return self._plan_path_cache

    @staticmethod
    def _infer_file_path(arguments: dict[str, Any]) -> str | None:
        for key in ("file_path", "path"):
            value = arguments.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _build_hook_context(self, event: str, **kwargs: Any) -> HookContext:
        arguments = cast(dict[str, Any], kwargs.get("arguments") or {})
        return HookContext(
            event=event,
            agent_id=self.agent_id,
            tool_name=cast(str | None, kwargs.get("tool_name")),
            file_path=self._infer_file_path(arguments),
            arguments=arguments,
            result=cast(str | None, kwargs.get("result")),
        )

    def _drain_hook_events(self) -> list[HookEvent]:
        return [
            HookEvent(name, message) for name, message in self.hook_engine.drain_notifications()
        ]

    async def _run_hook(self, event: str, **kwargs: Any) -> list[HookEvent]:
        await self.hook_engine.run_hooks(event, self._build_hook_context(event, **kwargs))
        return self._drain_hook_events()

    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        if self._team_manager is None:
            return
        messages = self._team_manager.consume_mailbox(self.agent_id)
        if messages:
            conversation.add_system_reminder("Team mailbox:\n" + "\n".join(messages))

    def _memory_items(self) -> list[str]:
        if self.memory_manager is None:
            return []
        getter = getattr(self.memory_manager, "get_memories", None)
        if not callable(getter):
            return []
        value = getter()
        return [str(item) for item in value] if value else []

    def _inject_context(self, conversation: ConversationManager, *, force: bool = False) -> None:
        if force:
            conversation.env_injected = False
            conversation.ltm_injected = False
        conversation.inject_environment(
            build_environment_context(
                self.work_dir,
                active_skills=self.active_skills,
                skill_catalog=self._skill_catalog,
                agent_catalog=self._agent_catalog,
            )
        )
        memories = self._memory_items()
        if self.instructions_content or memories:
            conversation.inject_long_term_memory(self.instructions_content, memories)

    def _system_prompt(self) -> str:
        custom_instructions = "\n".join(
            part.strip() for part in (self.system, self.instructions_content) if part.strip()
        )
        memory_section = "\n".join(
            part for part in (self.memory_section.strip(), *self._memory_items()) if part
        )
        return build_system_prompt(
            hook_prompts=self.hook_prompts,
            coordinator_mode=self.coordinator_mode,
            agent_catalog=self._agent_catalog,
            custom_instructions=custom_instructions,
            skill_section=self.skill_section,
            memory_section=memory_section,
            work_dir=self.work_dir,
        )

    @staticmethod
    def _append_response(conversation: ConversationManager, response: LLMResponse) -> None:
        tool_uses = [
            ToolUseBlock(call.tool_id, call.tool_name, dict(call.arguments))
            for call in response.tool_calls
        ]
        conversation.add_assistant_message(
            response.text,
            tool_uses,
            thinking_blocks=response.thinking_blocks,
        )

    async def _maybe_extract_memory(self, conversation: ConversationManager) -> None:
        if self.memory_manager is None:
            return
        extractor = getattr(self.memory_manager, "extract", None)
        if not callable(extractor):
            return
        value = extractor(conversation.get_messages())
        if asyncio.iscoroutine(value):
            await value

    async def run(self, conversation: ConversationManager | str) -> AsyncIterator[AgentEvent]:
        """Run until the model stops requesting tools or a structured stop condition fires.

        Passing a string is retained as a compatibility shortcut: it is appended as a user
        message to the Agent's conversation before the documented conversation-based loop runs.
        """
        if isinstance(conversation, str):
            self.conversation.add_user_message(conversation)
            active = self.conversation
        else:
            self.conversation = conversation
            active = conversation

        self._inject_context(active)
        turn_input_tokens = 0
        turn_output_tokens = 0
        consecutive_unknown = 0
        max_token_recoveries = 0

        for event in await self._run_hook("session_start"):
            yield event
        for event in await self._run_hook("turn_start"):
            yield event

        for iteration in range(1, self.max_iterations + 1):
            self._consume_mailbox(active)
            apply_tool_result_budget(active)
            compacted = auto_compact(active, self.context_window, self.compact_breaker)
            if isinstance(compacted, str):
                yield ErrorEvent(compacted)
                return
            if isinstance(compacted, CompactEvent):
                self._inject_context(active, force=True)
                yield CompactNotification(compacted.removed_messages, compacted.summary)

            if self._mode_transition_reminder is not None:
                active.add_system_reminder(self._mode_transition_reminder)
                self._mode_transition_reminder = None

            if self.permission_mode is PermissionMode.PLAN:
                plan_path = self._get_plan_path()
                active.add_system_reminder(
                    build_plan_mode_reminder(plan_path, plan_path.exists(), iteration)
                )

            for event in await self._run_hook("pre_send"):
                yield event

            collector = StreamCollector()
            stream = self.client.stream(
                active,
                system=self._system_prompt(),
                tools=self.registry.get_all_schemas(self.protocol) or None,
            )
            async for stream_event in collector.consume(stream):
                yield stream_event
            response = collector.response()

            for event in await self._run_hook("post_receive"):
                yield event

            self.total_input_tokens += response.input_tokens
            self.total_output_tokens += response.output_tokens
            turn_input_tokens += response.input_tokens
            turn_output_tokens += response.output_tokens
            yield UsageEvent(
                response.input_tokens,
                response.output_tokens,
                self.total_input_tokens,
                self.total_output_tokens,
            )

            if (
                response.stop_reason == "max_tokens"
                and max_token_recoveries < MAX_OUTPUT_TOKENS_RECOVERIES
            ):
                self._append_response(active, response)
                if max_token_recoveries == 0 and self.client.max_output_tokens < MAX_TOKENS_CEILING:
                    self.client.set_max_output_tokens(MAX_TOKENS_CEILING)
                    reason = "max_tokens escalation"
                else:
                    reason = f"max_tokens recovery {max_token_recoveries + 1}"
                max_token_recoveries += 1
                active.add_system_reminder(
                    "Continue exactly where the previous response stopped. Do not repeat text."
                )
                yield RetryEvent(reason)
                continue

            self._append_response(active, response)
            if not response.tool_calls:
                self._completed_turns += 1
                if self._completed_turns % MEMORY_EXTRACTION_INTERVAL == 0:
                    await self._maybe_extract_memory(active)
                for event in await self._run_hook("turn_end"):
                    yield event
                for event in await self._run_hook("session_end"):
                    yield event
                yield LoopComplete(
                    response.stop_reason,
                    turn_input_tokens,
                    turn_output_tokens,
                )
                return

            result_blocks: list[ToolResultBlock] = []
            unknown_in_round = False
            for batch in partition_tool_calls(response.tool_calls, self.registry):
                if batch.concurrent and len(batch.calls) > 1:
                    results = await self._execute_batch_parallel(batch.calls)
                    for hook_event in self._drain_hook_events():
                        yield hook_event
                else:
                    results = []
                    for call in batch.calls:
                        async for item in self._execute_tool(call):
                            if isinstance(item, _ToolExecResult):
                                results.append(item)
                            else:
                                yield item

                for result in results:
                    output = self._maybe_persist_or_truncate(result.tool_id, result.output)
                    result_blocks.append(
                        ToolResultBlock(result.tool_id, output, is_error=result.is_error)
                    )
                    unknown_in_round = unknown_in_round or result.is_unknown
                    yield ToolResultEvent(
                        result.tool_id,
                        result.tool_name,
                        "result",
                        output,
                        is_error=result.is_error,
                        arguments=self._arguments_for(response.tool_calls, result.tool_id),
                        elapsed_seconds=result.elapsed,
                    )

            active.add_tool_results_message(result_blocks)
            yield TurnComplete(response.stop_reason, turn_input_tokens, turn_output_tokens)
            consecutive_unknown = consecutive_unknown + 1 if unknown_in_round else 0
            if consecutive_unknown >= 3:
                yield ErrorEvent("Agent stopped after 3 consecutive unknown-tool rounds.")
                return

        yield ErrorEvent(f"Agent exceeded max iterations: {self.max_iterations}")

    async def run_to_completion(
        self,
        prompt: str,
        conversation: ConversationManager | None = None,
    ) -> str:
        """Run the event loop and return its generated text for synchronous callers."""

        active = conversation or self.conversation
        if prompt:
            active.add_user_message(prompt)
        chunks: list[str] = []
        async for event in self.run(active):
            if isinstance(event, StreamText):
                chunks.append(event.text)
            elif isinstance(event, ErrorEvent):
                raise RuntimeError(event.message)
        return "".join(chunks)

    @staticmethod
    def _arguments_for(calls: list[ToolCallComplete], tool_id: str) -> dict[str, Any] | None:
        for call in calls:
            if call.tool_id == tool_id:
                return dict(call.arguments)
        return None

    def _registered_tool(self, name: str) -> Any | None:
        return next((tool for tool in self.registry.list_tools() if tool.name == name), None)

    async def _execute_allowed(self, call: ToolCallComplete) -> _ToolExecResult:
        started = perf_counter()
        context = self._build_hook_context(
            "pre_tool_use",
            tool_name=call.tool_name,
            arguments=call.arguments,
        )
        pre_hook = await self.hook_engine.run_pre_tool_hooks(context)
        if not pre_hook.allowed:
            return _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Hook rejected: {pre_hook.reason}",
                max(0.0, perf_counter() - started),
                is_error=True,
            )
        result = await self.registry.execute(call.tool_name, call.arguments, truncate=False)
        await self.hook_engine.run_hooks(
            "post_tool_use",
            self._build_hook_context(
                "post_tool_use",
                tool_name=call.tool_name,
                arguments=call.arguments,
                result=result.output,
            ),
        )
        return _ToolExecResult(
            call.tool_id,
            call.tool_name,
            result.output,
            max(0.0, perf_counter() - started),
            is_error=result.is_error,
        )

    async def _execute_single_tool_direct(self, call: ToolCallComplete) -> _ToolExecResult:
        started = perf_counter()
        tool = self._registered_tool(call.tool_name)
        if tool is None:
            return _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Error: tool {call.tool_name!r} is unknown.",
                max(0.0, perf_counter() - started),
                is_error=True,
                is_unknown=True,
            )
        if not self.registry.is_enabled(call.tool_name):
            return _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Error: tool {call.tool_name!r} is disabled.",
                max(0.0, perf_counter() - started),
                is_error=True,
            )
        decision = self.permission_checker.check(tool, call.arguments)
        if decision.effect == "deny":
            return _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Permission denied: {decision.reason}",
                max(0.0, perf_counter() - started),
                is_error=True,
            )
        if decision.effect == "ask":
            return _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Permission required for concurrent tool {call.tool_name}; retry serially.",
                max(0.0, perf_counter() - started),
                is_error=True,
            )
        return await self._execute_allowed(call)

    async def _execute_batch_parallel(self, calls: list[ToolCallComplete]) -> list[_ToolExecResult]:
        executor = StreamingExecutor()
        for call in calls:
            executor.submit(self._execute_single_tool_direct(call))
        return await executor.collect_results()

    async def _execute_tool(
        self, call: ToolCallComplete
    ) -> AsyncIterator[PermissionRequest | HookEvent | _ToolExecResult]:
        tool = self._registered_tool(call.tool_name)
        if tool is None or not self.registry.is_enabled(call.tool_name):
            yield await self._execute_single_tool_direct(call)
            return

        decision = self.permission_checker.check(tool, call.arguments)
        if decision.effect == "deny":
            yield _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Permission denied: {decision.reason}",
                0.0,
                is_error=True,
            )
            return
        if decision.effect == "ask":
            future: asyncio.Future[PermissionResponse] = asyncio.get_running_loop().create_future()
            yield PermissionRequest(
                call.tool_id,
                call.tool_name,
                dict(call.arguments),
                future,
            )
            response = await future
            if response is PermissionResponse.DENY:
                yield _ToolExecResult(
                    call.tool_id,
                    call.tool_name,
                    f"User denied permission for {call.tool_name}.",
                    0.0,
                    is_error=True,
                )
                return
            if response is PermissionResponse.ALLOW_ALWAYS:
                representative = extract_content(call.tool_name, call.arguments)
                if not representative:
                    representative = self._infer_file_path(call.arguments) or str(
                        next(iter(call.arguments.values()), "")
                    )
                self.permission_checker.rule_engine.append_local_rule(
                    Rule(call.tool_name, representative[:60] + "*")
                )

        result = await self._execute_allowed(call)
        for event in self._drain_hook_events():
            yield event
        yield result

    def _maybe_persist_or_truncate(self, tool_id: str, output: str) -> str:
        if len(output) > SINGLE_RESULT_CHAR_LIMIT:
            try:
                path = persist_tool_result(self.session_dir, tool_id, output)
                return make_persisted_preview(output, path)
            except OSError:
                pass
        if len(output) > MAX_OUTPUT_CHARS:
            return output[:MAX_OUTPUT_CHARS] + "\n… (output truncated)"
        return output


__all__ = [
    "Agent",
    "AgentEvent",
    "CompactNotification",
    "ErrorEvent",
    "HookEvent",
    "LLMResponse",
    "LoopComplete",
    "MAX_OUTPUT_TOKENS_RECOVERIES",
    "MAX_TOKENS_CEILING",
    "MEMORY_EXTRACTION_INTERVAL",
    "PermissionRequest",
    "PermissionResponse",
    "RetryEvent",
    "StreamCollector",
    "StreamingExecutor",
    "StreamText",
    "ThinkingText",
    "ToolBatch",
    "ToolResultEvent",
    "ToolUseEvent",
    "TurnComplete",
    "UsageEvent",
    "partition_tool_calls",
]
