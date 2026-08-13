"""Provider-neutral, event-driven Agent loop."""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from collections.abc import AsyncIterator, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, cast

from simplecode.client import LLMClient
from simplecode.context import (
    CompactCircuitBreaker,
    CompactEvent,
    ContentReplacementState,
    RecoveryState,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    create_replacement_state,
    ensure_session_dir,
    estimate_conversation_tokens,
    is_prompt_too_long,
)
from simplecode.conversation import (
    ConversationManager,
    ThinkingBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from simplecode.evidence import EvidenceBundle, RunEvidenceTracker
from simplecode.hooks import HookContext, HookEngine
from simplecode.memory.auto_memory import MemoryManager
from simplecode.permissions import (
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    Rule,
    RuleEngine,
    extract_content,
    normalize_permission_content,
    permission_argument_hash,
)
from simplecode.prompts import (
    build_environment_context,
    build_plan_mode_exit_reminder,
    build_plan_mode_reminder,
    build_system_prompt,
)
from simplecode.tools import ToolRegistry, register_task_tools
from simplecode.tools.base import (
    StreamEnd,
    StreamEvent,
    TextDelta,
    ThinkingComplete,
    ThinkingDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
    ToolResult,
)
from simplecode.tools.write_plan import WritePlanTool

log = logging.getLogger(__name__)

MEMORY_EXTRACTION_INTERVAL = 5
MAX_TOKENS_CEILING = 64_000
MAX_OUTPUT_TOKENS_RECOVERIES = 3
MAX_PROMPT_TOO_LONG_RECOVERIES = 2
INTERRUPTED_TOOL_RESULT = "Error: tool call was interrupted."
NON_INTERACTIVE_PERMISSION_DENIED = (
    "Permission denied: non-interactive run cannot ask for confirmation."
)

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
    data: dict[str, Any] | None = None
    preview: str | None = None
    artifact_path: str | None = None
    exit_code: int | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolResultEvent(ToolUseEvent):
    """Completed tool event; subclasses ToolUseEvent for ch03 compatibility."""


@dataclass(frozen=True, slots=True)
class ToolBatchEvent:
    batch_id: str
    tool_ids: tuple[str, ...]
    concurrent: bool = False


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
    outcome: Literal[
        "answered", "completed", "waiting_background", "verification_failed"
    ] = "answered"
    evidence: EvidenceBundle | None = None


@dataclass(frozen=True, slots=True)
class VerificationEvent:
    status: Literal["started", "passed", "failed"]
    evidence: EvidenceBundle | None = None


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
    before_tokens: int
    after_tokens: int
    summary: str = ""


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
    reason: str = ""
    work_dir: str = ""
    argument_hash: str = ""


AgentEvent = (
    StreamText
    | ThinkingText
    | RetryEvent
    | ToolUseEvent
    | ToolResultEvent
    | ToolBatchEvent
    | TurnComplete
    | LoopComplete
    | VerificationEvent
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
            tool is not None
            and registry.is_enabled(call.tool_name)
            and tool.concurrency_safe_for(call.arguments)
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
    result: ToolResult | None = None


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
        memory_manager: MemoryManager | None = None,
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
        completion_gate_enabled: bool = True,
        allow_permission_prompts: bool = True,
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
        self.completion_gate_enabled = completion_gate_enabled
        self.allow_permission_prompts = allow_permission_prompts
        self.hook_engine = hook_engine or HookEngine()
        self.system = system
        self.conversation = conversation or ConversationManager()
        self.agent_id = uuid.uuid4().hex[:12]
        self.session_dir = ensure_session_dir(self.work_dir)
        self.compact_breaker = CompactCircuitBreaker()
        self.replacement_state: ContentReplacementState = create_replacement_state()
        self.recovery_state: RecoveryState = RecoveryState()
        self.permission_checker = permission_checker or PermissionChecker(
            sandbox=PathSandbox(self.work_dir),
            rule_engine=RuleEngine(
                user_rules_path=Path.home() / ".simplecode" / "permissions.yaml",
                project_rules_path=self.work_dir / ".simplecode" / "permissions.yaml",
                local_rules_path=self.work_dir / ".simplecode" / "permissions.local.yaml",
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
        self._active_skill_allowed_tools: dict[str, tuple[str, ...]] = {}
        self._skill_catalog = skill_catalog
        self._agent_catalog = agent_catalog
        self.hook_prompts = list(hook_prompts or [])
        self.skill_section = skill_section
        self.memory_section = memory_section
        self._plan_path_cache: Path | None = None
        self._pending_plan_execution = False
        self._mode_transition_reminder: str | None = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._completed_turns = 0
        self._loop_count = 0
        self._extracting = False
        self._memory_tasks: set[asyncio.Task[None]] = set()
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
            event_name=event,
            agent_id=self.agent_id,
            tool_name=cast(str, kwargs.get("tool_name") or ""),
            file_path=cast(
                str,
                kwargs.get("file_path") or self._infer_file_path(arguments) or "",
            ),
            tool_args=arguments,
            message=cast(str, kwargs.get("message") or ""),
            error=cast(str, kwargs.get("error") or ""),
            result=cast(str, kwargs.get("result") or ""),
        )

    def _drain_hook_events(self) -> list[HookEvent]:
        return [
            HookEvent(item.hook_id, item.output) for item in self.hook_engine.drain_notifications()
        ]

    async def _run_hook(self, event: str, **kwargs: Any) -> list[HookEvent]:
        await self.hook_engine.run_hooks(event, self._build_hook_context(event, **kwargs))
        self.hook_prompts.extend(self.hook_engine.get_prompt_messages())
        return self._drain_hook_events()

    @staticmethod
    def _latest_message_text(conversation: ConversationManager) -> str:
        for message in reversed(conversation.history):
            if message.content:
                return message.content
        return ""

    async def _error_hook_events(self, message: str) -> list[HookEvent]:
        return await self._run_hook("error", error=message, message=message)

    def _consume_mailbox(self, conversation: ConversationManager) -> None:
        if self._team_manager is None or not self.team_name:
            return
        try:
            messages = self._team_manager.get_mailbox(self.team_name).consume(self.agent_id)
            for message in messages:
                if message.metadata.get("event") == "idle":
                    member_name = str(message.metadata.get("member", ""))
                    if member_name:
                        self._team_manager.set_member_active(
                            self.team_name,
                            member_name,
                            False,
                        )
                if message.message_type == "text":
                    prefix = f"[Message from {message.from_agent}]"
                else:
                    prefix = f"[{message.message_type} from {message.from_agent}]"
                conversation.add_user_message(f"{prefix} {message.content}")
        except Exception:  # noqa: BLE001 - mailbox is auxiliary to the Agent loop
            log.debug("failed to consume team mailbox", exc_info=True)

    def _memory_items(self) -> list[str]:
        if self.memory_manager is None:
            return []
        loader = getattr(self.memory_manager, "load", None)
        if callable(loader):
            value = loader()
            return [str(value)] if value else []
        getter = getattr(self.memory_manager, "get_memories", None)
        if callable(getter):
            value = getter()
            return [str(item) for item in value] if value else []
        return []

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

    def _refresh_dynamic_environment(self, conversation: ConversationManager) -> None:
        conversation.refresh_environment(
            build_environment_context(
                self.work_dir,
                active_skills=self.active_skills,
                skill_catalog=self._skill_catalog,
                agent_catalog=self._agent_catalog,
            )
        )

    def _active_allowed_tool_names(self) -> set[str] | None:
        restrictions = [set(names) for names in self._active_skill_allowed_tools.values() if names]
        if not restrictions:
            return None
        allowed = restrictions[0]
        for restriction in restrictions[1:]:
            allowed &= restriction
        return allowed

    def _tool_allowed_by_active_skills(self, name: str) -> bool:
        allowed = self._active_allowed_tool_names()
        if allowed is None or name in allowed:
            return True
        tool = next((item for item in self.registry.list_tools() if item.name == name), None)
        return bool(tool is not None and tool.is_system_tool)

    def _skill_filtered_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = self.registry.get_all_schemas(self.protocol)
        allowed = self._active_allowed_tool_names()
        if allowed is None:
            return schemas
        system_names = {tool.name for tool in self.registry.list_tools() if tool.is_system_tool}
        visible = allowed | system_names
        return [schema for schema in schemas if str(schema.get("name", "")) in visible]

    def _apply_context_budget(self, conversation: ConversationManager) -> None:
        """Apply Layer 1 and commit its immutable result to the outer conversation."""
        budgeted, new_records = apply_tool_result_budget(
            conversation,
            self.session_dir,
            self.replacement_state,
        )
        if new_records:
            append_replacement_records(self.session_dir, new_records)

        conversation.history = budgeted.get_messages()
        conversation.env_injected = budgeted.env_injected
        conversation.ltm_injected = budgeted.ltm_injected
        conversation.last_input_tokens = max(
            budgeted.last_input_tokens,
            estimate_conversation_tokens(budgeted),
        )

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
    def _seal_open_tools(
        conversation: ConversationManager,
        open_ids: list[str],
        collected: list[ToolResultBlock],
        reason: str = INTERRUPTED_TOOL_RESULT,
    ) -> None:
        have = {block.tool_use_id for block in collected}
        blocks = list(collected)
        for tool_id in open_ids:
            if tool_id not in have:
                blocks.append(ToolResultBlock(tool_id, reason, is_error=True))
        if blocks:
            conversation.add_tool_results_message(blocks)
        conversation.ensure_tool_result_pairing(reason)

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

    async def _extract_memories(self, conversation: ConversationManager) -> None:
        if self._extracting:
            return
        if self.memory_manager is None:
            return
        extractor = getattr(self.memory_manager, "extract", None)
        if not callable(extractor):
            return
        self._extracting = True
        try:
            value = extractor(self.client, conversation, self.protocol)
            if asyncio.iscoroutine(value):
                await value
        except Exception:  # noqa: BLE001 - automatic memory is best-effort
            log.debug("automatic memory extraction failed", exc_info=True)
        finally:
            self._extracting = False

    def _schedule_memory_extraction(self, conversation: ConversationManager) -> None:
        if self.memory_manager is None or self._extracting:
            return
        task = asyncio.ensure_future(self._extract_memories(conversation))
        self._memory_tasks.add(task)
        task.add_done_callback(self._memory_tasks.discard)

    async def flush_memories(
        self,
        conversation: ConversationManager | None = None,
        *,
        force: bool = False,
    ) -> None:
        """Finish pending extraction and optionally schedule the latest delta."""
        if force and self.memory_manager is not None:
            self._schedule_memory_extraction(conversation or self.conversation)
        if self._memory_tasks:
            await asyncio.gather(*tuple(self._memory_tasks), return_exceptions=True)

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
        ptl_recoveries = 0

        initial_message = self._latest_message_text(active)
        evidence_tracker = RunEvidenceTracker(self.work_dir, initial_message)
        evidence_tracker.plan_pending = (
            self._pending_plan_execution and self.permission_mode is not PermissionMode.PLAN
        )
        agent_tool = self._registered_tool("Agent")
        task_manager = getattr(agent_tool, "task_manager", None)
        list_tasks = getattr(task_manager, "list_tasks", None)
        if callable(list_tasks):
            evidence_tracker.background_pending = any(
                getattr(task, "status", "") == "running" for task in list_tasks()
            )
        for event in await self._run_hook("session_start", message=initial_message):
            yield event
        for event in await self._run_hook("turn_start", message=initial_message):
            yield event

        for iteration in range(1, self.max_iterations + 1):
            self._consume_mailbox(active)
            # Skills can be activated by a tool in the preceding iteration. Rebuild the
            # pinned environment before every provider request so the new SOP is visible.
            self._refresh_dynamic_environment(active)
            # Layer 1 first: persist large results and commit previews to outer history.
            self._apply_context_budget(active)

            # Layer 2: may replace the whole outer history when near the window limit.
            compacted = await auto_compact(
                active,
                self.client,
                self.context_window,
                self.session_dir,
                protocol=self.protocol,
                breaker=self.compact_breaker,
                recovery=self.recovery_state,
                tool_schemas=self._skill_filtered_tool_schemas(),
            )
            if isinstance(compacted, str):
                for event in await self._error_hook_events(compacted):
                    yield event
                yield ErrorEvent(compacted)
                return
            if isinstance(compacted, CompactEvent):
                self._inject_context(active, force=True)
                after_tokens = estimate_conversation_tokens(active)
                active.last_input_tokens = after_tokens
                for event in await self._run_hook(
                    "compact",
                    message=f"{compacted.before_tokens} -> {after_tokens}",
                ):
                    yield event
                yield CompactNotification(
                    compacted.before_tokens,
                    after_tokens,
                )

            if self._mode_transition_reminder is not None:
                active.add_system_reminder(self._mode_transition_reminder)
                self._mode_transition_reminder = None

            if self.permission_mode is PermissionMode.PLAN:
                plan_path = self._get_plan_path()
                active.add_system_reminder(
                    build_plan_mode_reminder(plan_path, plan_path.exists(), iteration)
                )

            for event in await self._run_hook(
                "pre_send",
                message=self._latest_message_text(active),
            ):
                yield event

            # Reminders/environment added after compaction also count toward the window.
            active.last_input_tokens = max(
                active.last_input_tokens,
                estimate_conversation_tokens(active),
            )

            active.ensure_tool_result_pairing(INTERRUPTED_TOOL_RESULT)

            collector = StreamCollector()
            try:
                stream = self.client.stream(
                    active,
                    system=self._system_prompt(),
                    tools=self._skill_filtered_tool_schemas() or None,
                )
                async for stream_event in collector.consume(stream):
                    yield stream_event
            except asyncio.CancelledError:
                partial = collector.response()
                if partial.tool_calls:
                    self._append_response(active, partial)
                    self._seal_open_tools(
                        active,
                        [call.tool_id for call in partial.tool_calls],
                        [],
                    )
                else:
                    self._seal_open_tools(active, [], [])
                raise
            except Exception as exc:
                if (
                    is_prompt_too_long(exc)
                    and ptl_recoveries < MAX_PROMPT_TOO_LONG_RECOVERIES
                ):
                    ptl_recoveries += 1
                    compacted = await auto_compact(
                        active,
                        self.client,
                        self.context_window,
                        self.session_dir,
                        protocol=self.protocol,
                        manual=True,
                        breaker=self.compact_breaker,
                        recovery=self.recovery_state,
                        tool_schemas=self._skill_filtered_tool_schemas(),
                    )
                    if isinstance(compacted, CompactEvent):
                        self._inject_context(active, force=True)
                        after_tokens = estimate_conversation_tokens(active)
                        active.last_input_tokens = after_tokens
                        yield CompactNotification(compacted.before_tokens, after_tokens)
                        yield RetryEvent(f"prompt_too_long compact {ptl_recoveries}")
                        continue
                    message = (
                        compacted
                        if isinstance(compacted, str)
                        else f"Context too large: {type(exc).__name__}: {exc}"
                    )
                    for event in await self._error_hook_events(message):
                        yield event
                    yield ErrorEvent(message)
                    return
                raise
            response = collector.response()
            active.last_input_tokens = max(
                response.input_tokens,
                estimate_conversation_tokens(active),
            )

            for event in await self._run_hook("post_receive", message=response.text):
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
                evidence: EvidenceBundle | None = None
                outcome: Literal["answered", "completed", "waiting_background"] = "answered"
                if callable(list_tasks):
                    evidence_tracker.background_pending = any(
                        getattr(task, "status", "") == "running" for task in list_tasks()
                    )
                if self.completion_gate_enabled and evidence_tracker.gate_required:
                    yield VerificationEvent("started")
                    verified = await evidence_tracker.verify(response.text)
                    if verified.outcome == "verification_failed":
                        yield VerificationEvent("failed", verified)
                        if evidence_tracker.repair_attempts < 2:
                            evidence_tracker.repair_attempts += 1
                            active.add_system_reminder(evidence_tracker.repair_message(verified))
                            yield RetryEvent(
                                f"completion verification repair "
                                f"{evidence_tracker.repair_attempts}/2"
                            )
                            continue
                        message = "Completion verification failed after 2 repair attempts."
                        for event in await self._run_hook("turn_end"):
                            yield event
                        for event in await self._run_hook("session_end"):
                            yield event
                        yield LoopComplete(
                            response.stop_reason,
                            turn_input_tokens,
                            turn_output_tokens,
                            "verification_failed",
                            verified,
                        )
                        for event in await self._error_hook_events(message):
                            yield event
                        yield ErrorEvent(message)
                        return
                    evidence = verified
                    outcome = cast(
                        Literal["answered", "completed", "waiting_background"],
                        verified.outcome,
                    )
                    yield VerificationEvent("passed", verified)
                    if (
                        self.permission_mode is not PermissionMode.PLAN
                        and verified.outcome == "completed"
                        and evidence_tracker.saw_successful_execution
                        and not evidence_tracker.background_pending
                    ):
                        self._pending_plan_execution = False
                self._completed_turns += 1
                self._loop_count += 1
                if self._loop_count % MEMORY_EXTRACTION_INTERVAL == 0:
                    self._schedule_memory_extraction(active)
                for event in await self._run_hook("turn_end"):
                    yield event
                for event in await self._run_hook("session_end"):
                    yield event
                yield LoopComplete(
                    response.stop_reason,
                    turn_input_tokens,
                    turn_output_tokens,
                    outcome,
                    evidence,
                )
                return

            result_blocks: list[ToolResultBlock] = []
            open_ids = [call.tool_id for call in response.tool_calls]
            unknown_in_round = False
            try:
                for batch_index, batch in enumerate(
                    partition_tool_calls(response.tool_calls, self.registry),
                    start=1,
                ):
                    yield ToolBatchEvent(
                        f"{iteration}-{batch_index}",
                        tuple(call.tool_id for call in batch.calls),
                        batch.concurrent,
                    )
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
                        result_value = result.result or ToolResult(
                            result.output,
                            is_error=result.is_error,
                        )
                        registered = self._registered_tool(result.tool_name)
                        arguments = (
                            self._arguments_for(response.tool_calls, result.tool_id) or {}
                        )
                        evidence_tracker.record(
                            result.tool_name,
                            str(getattr(registered, "category", "read")),
                            arguments,
                            result_value,
                            result.elapsed,
                        )
                        if result.tool_name == WritePlanTool.name and not result.is_error:
                            self._pending_plan_execution = True
                        output = result.output
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
                            arguments=arguments,
                            elapsed_seconds=result.elapsed,
                            data=result_value.data,
                            preview=result_value.preview,
                            artifact_path=result_value.artifact_path,
                            exit_code=result_value.exit_code,
                            diagnostics=result_value.diagnostics,
                        )
            except BaseException:
                self._seal_open_tools(active, open_ids, result_blocks)
                raise

            active.add_tool_results_message(result_blocks)
            yield TurnComplete(response.stop_reason, turn_input_tokens, turn_output_tokens)
            consecutive_unknown = consecutive_unknown + 1 if unknown_in_round else 0
            if consecutive_unknown >= 3:
                message = "Agent stopped after 3 consecutive unknown-tool rounds."
                for event in await self._error_hook_events(message):
                    yield event
                yield ErrorEvent(message)
                return

        message = f"Agent exceeded max iterations: {self.max_iterations}"
        for event in await self._error_hook_events(message):
            yield event
        yield ErrorEvent(message)

    async def run_to_completion(
        self,
        prompt: str,
        conversation: ConversationManager | None = None,
    ) -> str:
        """Run non-interactively and return the final model turn's text."""

        previous_prompts = self.allow_permission_prompts
        self.allow_permission_prompts = False
        try:
            active = conversation or self.conversation
            if prompt:
                active.add_user_message(prompt)
            current_turn: list[str] = []
            previous_turn: list[str] = []
            async for event in self.run(active):
                if isinstance(event, PermissionRequest):
                    if not event.future.done():
                        event.future.set_result(PermissionResponse.DENY)
                    continue
                if isinstance(event, StreamText):
                    current_turn.append(event.text)
                elif isinstance(event, TurnComplete):
                    if current_turn:
                        previous_turn = current_turn
                        current_turn = []
                elif isinstance(event, ErrorEvent):
                    raise RuntimeError(event.message)
            return "".join(current_turn or previous_turn)
        finally:
            self.allow_permission_prompts = previous_prompts

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
        result = await self.registry.execute(call.tool_name, call.arguments, truncate=False)
        await self.hook_engine.run_hooks(
            "post_tool_use",
            self._build_hook_context(
                "post_tool_use",
                tool_name=call.tool_name,
                arguments=call.arguments,
                message=result.output,
                result=result.output,
            ),
        )
        if not result.is_error:
            tool = self._registered_tool(call.tool_name)
            if tool is not None and tool.category == "write":
                file_path = self._infer_file_path(call.arguments) or ""
                await self.hook_engine.run_hooks(
                    "file_change",
                    self._build_hook_context(
                        "file_change",
                        tool_name=call.tool_name,
                        arguments=call.arguments,
                        file_path=file_path,
                        message=result.output,
                        result=result.output,
                    ),
                )
        self.hook_prompts.extend(self.hook_engine.get_prompt_messages())
        exec_result = _ToolExecResult(
            call.tool_id,
            call.tool_name,
            result.output,
            max(0.0, perf_counter() - started),
            is_error=result.is_error,
            result=result,
        )
        self._snapshot_for_recovery(call, exec_result)
        return exec_result

    async def _run_pre_tool_hook(
        self,
        call: ToolCallComplete,
        started: float,
    ) -> _ToolExecResult | None:
        rejection = await self.hook_engine.run_pre_tool_hooks(
            self._build_hook_context(
                "pre_tool_use",
                tool_name=call.tool_name,
                arguments=call.arguments,
            )
        )
        self.hook_prompts.extend(self.hook_engine.get_prompt_messages())
        if rejection is None:
            return None
        return _ToolExecResult(
            call.tool_id,
            call.tool_name,
            f"Hook rejected: {rejection.reason}",
            max(0.0, perf_counter() - started),
            is_error=True,
        )

    async def _execute_single_tool_direct(self, call: ToolCallComplete) -> _ToolExecResult:
        started = perf_counter()
        if not self._tool_allowed_by_active_skills(call.tool_name):
            return _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Permission denied: active Skill tool whitelist excludes {call.tool_name}.",
                max(0.0, perf_counter() - started),
                is_error=True,
            )
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
        rejection = await self._run_pre_tool_hook(call, started)
        if rejection is not None:
            return rejection
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
            message = (
                NON_INTERACTIVE_PERMISSION_DENIED
                if not self.allow_permission_prompts
                else f"Permission required for concurrent tool {call.tool_name}; retry serially."
            )
            return _ToolExecResult(
                call.tool_id,
                call.tool_name,
                message,
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
        if not self._tool_allowed_by_active_skills(call.tool_name):
            yield _ToolExecResult(
                call.tool_id,
                call.tool_name,
                f"Permission denied: active Skill tool whitelist excludes {call.tool_name}.",
                0.0,
                is_error=True,
            )
            return
        tool = self._registered_tool(call.tool_name)
        if tool is None or not self.registry.is_enabled(call.tool_name):
            yield await self._execute_single_tool_direct(call)
            return

        started = perf_counter()
        rejection = await self._run_pre_tool_hook(call, started)
        for event in self._drain_hook_events():
            yield event
        if rejection is not None:
            yield rejection
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
        if decision.effect == "ask" and not self.allow_permission_prompts:
            yield _ToolExecResult(
                call.tool_id,
                call.tool_name,
                NON_INTERACTIVE_PERMISSION_DENIED,
                0.0,
                is_error=True,
            )
            return
        if decision.effect == "ask":
            for event in await self._run_hook(
                "permission_request",
                tool_name=call.tool_name,
                arguments=call.arguments,
                message=decision.reason,
            ):
                yield event
            future: asyncio.Future[PermissionResponse] = asyncio.get_running_loop().create_future()
            yield PermissionRequest(
                call.tool_id,
                call.tool_name,
                dict(call.arguments),
                future,
                reason=decision.reason,
                work_dir=str(self.work_dir),
                argument_hash=permission_argument_hash(call.tool_name, call.arguments),
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
                representative = normalize_permission_content(call.tool_name, representative)
                self.permission_checker.rule_engine.append_local_rule(
                    Rule(
                        call.tool_name,
                        representative,
                        match_mode="exact",
                        argument_hash=permission_argument_hash(
                            call.tool_name,
                            call.arguments,
                        ),
                    )
                )

        result = await self._execute_allowed(call)
        for event in self._drain_hook_events():
            yield event
        yield result

    def activate_skill(
        self,
        name: str,
        body: str,
        allowed_tools: tuple[str, ...] | list[str] = (),
    ) -> None:
        """Pin a skill SOP into the environment context for subsequent turns."""
        self.active_skills[name] = body
        self._active_skill_allowed_tools[name] = tuple(allowed_tools)

    def clear_active_skills(self) -> None:
        self.active_skills.clear()
        self._active_skill_allowed_tools.clear()

    def set_skill_catalog(self, catalog: str) -> None:
        self._skill_catalog = catalog.strip()

    def set_agent_catalog(self, catalog: str) -> None:
        self._agent_catalog = catalog.strip()

    def _snapshot_for_recovery(self, tc: ToolCallComplete, result: _ToolExecResult) -> None:
        if result.is_error or tc.tool_name != "ReadFile":
            return
        file_path = tc.arguments.get("file_path") or tc.arguments.get("path")
        if not isinstance(file_path, str) or not file_path:
            return
        try:
            content = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return
        self.recovery_state.record_file_read(file_path, content)

    async def manual_compact(
        self,
        conversation: ConversationManager | None = None,
    ) -> CompactNotification | ErrorEvent:
        """User-triggered compact (`/compact`); uses the tighter manual safety margin."""
        active = conversation or self.conversation
        compacted = await auto_compact(
            active,
            self.client,
            self.context_window,
            self.session_dir,
            protocol=self.protocol,
            manual=True,
            breaker=self.compact_breaker,
            recovery=self.recovery_state,
            tool_schemas=self._skill_filtered_tool_schemas(),
        )
        if isinstance(compacted, CompactEvent):
            self._inject_context(active, force=True)
            after_tokens = estimate_conversation_tokens(active)
            active.last_input_tokens = after_tokens
            await self._run_hook(
                "compact",
                message=f"{compacted.before_tokens} -> {after_tokens}",
            )
            return CompactNotification(compacted.before_tokens, after_tokens)
        if isinstance(compacted, str):
            await self._run_hook("error", error=compacted, message=compacted)
            return ErrorEvent(compacted)
        message = "Nothing to compact."
        await self._run_hook("error", error=message, message=message)
        return ErrorEvent(message)


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
    "ToolBatchEvent",
    "ToolUseEvent",
    "TurnComplete",
    "UsageEvent",
    "VerificationEvent",
    "partition_tool_calls",
]
