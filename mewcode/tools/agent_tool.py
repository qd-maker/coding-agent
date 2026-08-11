"""Unified defined-Agent and context-fork tool."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from mewcode.agent import Agent
from mewcode.agents.fork import ForkError, build_forked_messages
from mewcode.agents.loader import AgentLoader
from mewcode.agents.parser import AgentDefinition
from mewcode.agents.task_manager import BackgroundTask, TaskManager
from mewcode.agents.tool_filter import build_teammate_tools, resolve_agent_tools
from mewcode.agents.trace import TraceNode, TraceRegistry
from mewcode.client import LLMClient, create_client
from mewcode.config import ProviderConfig
from mewcode.context import clone_replacement_state
from mewcode.conversation import ConversationManager
from mewcode.permissions import (
    DangerousCommandDetector,
    Decision,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.teams.models import BackendType, TeammateInfo, sanitize_name
from mewcode.teams.spawn_inprocess import spawn_inprocess_teammate
from mewcode.teams.spawn_iterm2 import spawn_iterm2_teammate
from mewcode.teams.spawn_tmux import spawn_tmux_teammate
from mewcode.tools.base import Tool, ToolResult
from mewcode.tools.send_message import SendMessageTool
from mewcode.worktree import (
    Worktree,
    WorktreeManager,
    build_worktree_notice,
    generate_worktree_name,
)


class AgentToolParams(BaseModel):
    prompt: str = Field(min_length=1, description="Concrete task for the sub-agent")
    description: str = Field(min_length=1, description="Short task label shown in /tasks")
    subagent_type: str | None = Field(
        default=None,
        description="Named Agent definition; omit to fork the parent context",
    )
    model: str | None = Field(default=None, description="Optional model alias override")
    run_in_background: bool = False
    name: str | None = None
    isolation: str | None = None
    team_name: str | None = None
    requires_approval: bool = False


PERMISSION_MODE_MAP = {
    "default": PermissionMode.DONT_ASK,
    "acceptEdits": PermissionMode.ACCEPT_EDITS,
    "dontAsk": PermissionMode.DONT_ASK,
}

TEAMMATE_ADDENDUM = """
You are a persistent teammate in an Agent team. Work only on assigned shared tasks in your
isolated worktree. Update task status and coordinate with SendMessage. Never spawn another Agent
or create/delete the team. When done, commit coherent work when appropriate and return a concise
report with result, changed files, verification, risks, and commit SHA. Your conversation will be
saved; a later message may resume you without creating a new teammate.
""".strip()


class _NonInteractivePermissionChecker(PermissionChecker):
    """Never leave RunToCompletion blocked on a UI permission future."""

    def check(self, tool: Tool, arguments: dict[str, Any]) -> Decision:
        decision = super().check(tool, arguments)
        if decision.effect == "ask":
            return Decision("deny", "Non-interactive sub-agent cannot request confirmation")
        return decision


@dataclass(slots=True)
class _ForegroundRun:
    execution: asyncio.Task[str]
    agent: Agent
    agent_type: str
    description: str
    trace_node: TraceNode


class AgentTool(Tool):
    name = "Agent"
    description = (
        "Run a bounded task in a named isolated Agent, or omit subagent_type to fork the full "
        "parent conversation in the background."
    )
    params_model = AgentToolParams
    category = "command"
    is_concurrency_safe = False
    execution_timeout = None

    def __init__(
        self,
        provider_config: ProviderConfig | None = None,
        system: str = "",
        parent_agent: Agent | None = None,
        *,
        agent_loader: AgentLoader | None = None,
        task_manager: TaskManager | None = None,
        trace_manager: TraceRegistry | None = None,
        enable_fork: bool = True,
        client: LLMClient | None = None,
        worktree_manager: Any | None = None,
        team_manager: Any | None = None,
        foreground_timeout: float = 120.0,
        background_timeout: float = 600.0,
    ) -> None:
        work_dir = parent_agent.work_dir if parent_agent is not None else Path.cwd()
        self.agent_loader = agent_loader or AgentLoader(work_dir)
        if not self.agent_loader.list_agents():
            self.agent_loader.load_all()
        self.trace_manager = trace_manager or TraceRegistry()
        self.task_manager = task_manager or TaskManager(
            self.trace_manager,
            default_timeout=background_timeout,
        )
        self.parent_agent = parent_agent
        self.provider_config = provider_config
        self.system = system
        self.enable_fork = enable_fork
        self.client = client
        self.worktree_manager = worktree_manager
        self.team_manager = team_manager
        self.foreground_timeout = foreground_timeout
        self.background_timeout = background_timeout
        self._foreground: _ForegroundRun | None = None

    @property
    def foreground_running(self) -> bool:
        return self._foreground is not None and not self._foreground.execution.done()

    def _select_llm(
        self,
        definition: AgentDefinition | None,
        model_override: str | None = None,
    ) -> LLMClient:
        selected = model_override or (definition.model if definition is not None else "inherit")
        if selected in {None, "", "inherit"}:
            if self.parent_agent is not None:
                return self.parent_agent.client
            if self.client is not None:
                return self.client
            if self.provider_config is not None:
                return create_client(self.provider_config)
            raise RuntimeError("AgentTool requires provider_config, client, or parent_agent")
        try:
            return self._create_client_for_model(selected)
        except Exception:  # noqa: BLE001 - model override falls back to parent provider
            if self.parent_agent is not None:
                return self.parent_agent.client
            if self.client is not None:
                return self.client
            raise

    def _create_client_for_model(self, model_alias: str) -> LLMClient:
        config = self.provider_config
        if config is None and self.parent_agent is not None:
            config = self.parent_agent.client.config
        if config is None:
            raise RuntimeError("Cannot route a model without ProviderConfig")
        model_map = {
            "haiku": "claude-haiku-4-5",
            "sonnet": "claude-sonnet-4-6",
            "opus": "claude-opus-4-6",
        }
        model = model_map.get(model_alias, model_alias)
        return create_client(
            config.model_copy(update={"name": f"subagent-{model_alias}", "model": model})
        )

    def _permission_checker(
        self,
        definition: AgentDefinition | None,
        work_dir: Path,
    ) -> PermissionChecker:
        requested = definition.permission_mode if definition is not None else "dontAsk"
        mode = PERMISSION_MODE_MAP.get(requested, PermissionMode.DONT_ASK)
        return _NonInteractivePermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(work_dir),
            rule_engine=RuleEngine(
                user_rules_path=Path.home() / ".mewcode" / "permissions.yaml",
                project_rules_path=work_dir / ".mewcode" / "permissions.yaml",
                local_rules_path=work_dir / ".mewcode" / "permissions.local.yaml",
            ),
            mode=mode,
        )

    def _build_agent(
        self,
        definition: AgentDefinition | None,
        *,
        conversation: ConversationManager,
        is_background: bool,
        model_override: str | None,
        trace_node: TraceNode,
        work_dir_override: Path | None = None,
        registry_override: Any | None = None,
        system_addendum: str = "",
        team_name: str | None = None,
        team_manager: Any | None = None,
    ) -> Agent:
        parent = self.parent_agent
        if parent is None:
            raise RuntimeError("AgentTool requires parent_agent for tool inheritance")
        work_dir = (work_dir_override or parent.work_dir).resolve()
        parent_registry = parent.registry
        registry = registry_override or resolve_agent_tools(
            parent_registry,
            definition,
            is_background=is_background,
            work_dir=work_dir,
        )
        is_fork = definition is None
        system = parent.system if is_fork else self.system
        if definition is not None:
            system = definition.system_prompt
        if system_addendum:
            system = f"{system.strip()}\n\n{system_addendum}".strip()
        child = Agent(
            client=self._select_llm(definition, model_override),
            system=system,
            registry=registry,
            protocol=parent.protocol,
            work_dir=work_dir,
            max_iterations=definition.max_turns if definition else parent.max_iterations,
            conversation=conversation,
            permission_checker=self._permission_checker(definition, work_dir),
            context_window=parent.context_window,
            instructions_content=(parent.instructions_content if is_fork else ""),
            hook_engine=parent.hook_engine,
            active_skills=(parent.active_skills if is_fork else None),
            team_name=team_name,
            team_manager=team_manager,
            completion_gate_enabled=False,
        )
        child.agent_id = trace_node.agent_id
        if is_fork:
            child.replacement_state = clone_replacement_state(parent.replacement_state)
        return child

    async def _execute_with_worktree(
        self,
        child: Agent,
        prompt: str,
        conversation: ConversationManager,
        worktree: Worktree,
    ) -> str:
        """Run one child in its isolated checkout and clean it only when provably empty."""

        try:
            result = await child.run_to_completion(prompt, conversation)
        except Exception as exc:
            notice = f"[Worktree preserved at {worktree.path}, branch {worktree.branch}]"
            raise RuntimeError(f"{exc}\n\n{notice}") from exc
        manager = self.worktree_manager
        if not isinstance(manager, WorktreeManager):
            return result
        try:
            cleanup = await manager.auto_cleanup(worktree)
        except Exception:  # noqa: BLE001 - preserve work and still return the Agent result
            cleanup = None
        if cleanup is not None and not cleanup.kept:
            return result
        notice = f"[Worktree preserved at {worktree.path}, branch {worktree.branch}]"
        return f"{result}\n\n{notice}" if result else notice

    async def run(self, prompt: str, model: str | None = None) -> str:
        """Compatibility helper: run the general-purpose definition synchronously."""

        result = await self.execute(
            AgentToolParams(
                prompt=prompt,
                description=prompt[:80] or "Sub-agent task",
                subagent_type="general-purpose",
                model=model,
            )
        )
        if result.is_error:
            raise RuntimeError(result.output)
        return result.output

    async def detach_foreground(self) -> BackgroundTask | None:
        active = self._foreground
        if active is None or active.execution.done():
            return None
        record = await self.task_manager.adopt_task(
            active.execution,
            active.agent,
            agent_type=active.agent_type,
            description=active.description,
            trace_node=active.trace_node,
            timeout=self.background_timeout,
        )
        self._foreground = None
        return record

    def _background_result(self, task: BackgroundTask, *, reason: str = "started") -> ToolResult:
        return ToolResult(
            f"Background Agent {reason}. Task ID: {task.task_id}. "
            "Use /tasks or /task info <id> to inspect it."
        )

    async def execute(self, params: AgentToolParams | dict[str, Any]) -> ToolResult:
        p = (
            params
            if isinstance(params, AgentToolParams)
            else AgentToolParams.model_validate(params)
        )
        if self.parent_agent is None:
            return ToolResult("Error: AgentTool is not attached to a parent Agent.", is_error=True)
        if p.team_name:
            return await self._execute_as_teammate(p)
        is_fork = not p.subagent_type
        definition: AgentDefinition | None = None
        if is_fork:
            if not self.enable_fork:
                return ToolResult("Error: Agent fork mode is disabled.", is_error=True)
            try:
                conversation = build_forked_messages(self.parent_agent.conversation, p.prompt)
            except ForkError as exc:
                return ToolResult(f"Error: {exc}", is_error=True)
            execution_prompt = ""
            agent_type = "fork"
        else:
            definition = self.agent_loader.get(p.subagent_type or "")
            if definition is None:
                available = ", ".join(item.agent_type for item in self.agent_loader.list_agents())
                return ToolResult(
                    f"Error: unknown subagent_type {p.subagent_type!r}. Available: {available}",
                    is_error=True,
                )
            conversation = ConversationManager()
            execution_prompt = p.prompt
            agent_type = definition.agent_type

        isolation = p.isolation or (definition.isolation if definition is not None else "")
        if isolation not in {None, "", "worktree"}:
            return ToolResult(
                f"Error: unsupported Agent isolation mode {isolation!r}.",
                is_error=True,
            )

        is_background = bool(
            is_fork or p.run_in_background or (definition is not None and definition.background)
        )
        trace_node = self.trace_manager.create(agent_type, self.parent_agent.agent_id)
        worktree: Worktree | None = None
        work_dir_override: Path | None = None
        if isolation == "worktree":
            if not isinstance(self.worktree_manager, WorktreeManager):
                self.trace_manager.complete(
                    trace_node.agent_id,
                    "failed",
                    error="WorktreeManager is unavailable",
                )
                return ToolResult(
                    "Error: worktree isolation is not available in this runtime.",
                    is_error=True,
                )
            try:
                worktree = await self.worktree_manager.create(generate_worktree_name(), "HEAD")
            except Exception as exc:  # noqa: BLE001 - surface lifecycle failure to parent
                self.trace_manager.complete(trace_node.agent_id, "failed", error=str(exc))
                return ToolResult(f"Error: cannot create isolated worktree: {exc}", is_error=True)
            work_dir_override = worktree.path
            notice = build_worktree_notice(str(self.parent_agent.work_dir), str(worktree.path))
            if is_fork:
                conversation.add_system_reminder(notice)
            else:
                execution_prompt = f"{notice}\n\n{execution_prompt}"
        try:
            child = self._build_agent(
                definition,
                conversation=conversation,
                is_background=is_background,
                model_override=p.model,
                trace_node=trace_node,
                work_dir_override=work_dir_override,
            )
        except Exception as exc:  # noqa: BLE001 - tool returns model-visible construction errors
            if worktree is not None and isinstance(self.worktree_manager, WorktreeManager):
                await self.worktree_manager.auto_cleanup(worktree)
            self.trace_manager.complete(trace_node.agent_id, "failed", error=str(exc))
            return ToolResult(f"Error: cannot create Agent: {exc}", is_error=True)

        if is_background:
            if worktree is not None:
                execution = asyncio.create_task(
                    self._execute_with_worktree(
                        child,
                        execution_prompt,
                        conversation,
                        worktree,
                    )
                )
                task = await self.task_manager.adopt_task(
                    execution,
                    child,
                    agent_type=agent_type,
                    description=p.description,
                    trace_node=trace_node,
                    timeout=self.background_timeout,
                )
            else:
                task = await self.task_manager.launch(
                    child,
                    execution_prompt,
                    agent_type=agent_type,
                    description=p.description,
                    trace_node=trace_node,
                    conversation=conversation,
                    timeout=self.background_timeout,
                )
            return self._background_result(task)

        if worktree is not None:
            execution = asyncio.create_task(
                self._execute_with_worktree(
                    child,
                    execution_prompt,
                    conversation,
                    worktree,
                )
            )
        else:
            execution = asyncio.create_task(child.run_to_completion(execution_prompt, conversation))
        self._foreground = _ForegroundRun(
            execution,
            child,
            agent_type,
            p.description,
            trace_node,
        )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(execution),
                timeout=self.foreground_timeout,
            )
        except TimeoutError:
            task = await self.task_manager.adopt_task(
                execution,
                child,
                agent_type=agent_type,
                description=p.description,
                trace_node=trace_node,
                timeout=self.background_timeout,
            )
            return self._background_result(task, reason="auto-detached after foreground timeout")
        except asyncio.CancelledError:
            await self.task_manager.adopt_task(
                execution,
                child,
                agent_type=agent_type,
                description=p.description,
                trace_node=trace_node,
                timeout=self.background_timeout,
            )
            raise
        except Exception as exc:  # noqa: BLE001 - child failure becomes a ToolResult
            self.trace_manager.update(
                trace_node.agent_id,
                input_tokens=child.total_input_tokens,
                output_tokens=child.total_output_tokens,
            )
            self.trace_manager.complete(trace_node.agent_id, "failed", error=str(exc))
            return ToolResult(
                f"Error: sub-agent failed: {type(exc).__name__}: {exc}",
                is_error=True,
            )
        else:
            self.trace_manager.update(
                trace_node.agent_id,
                input_tokens=child.total_input_tokens,
                output_tokens=child.total_output_tokens,
            )
            self.trace_manager.complete(trace_node.agent_id)
            return ToolResult(result or "(sub-agent produced no final text)")
        finally:
            if self._foreground is not None and self._foreground.execution is execution:
                self._foreground = None

    async def _execute_as_teammate(self, p: AgentToolParams) -> ToolResult:
        """Create one long-lived teammate in an isolated worktree/runtime backend."""

        parent = self.parent_agent
        manager = self.team_manager
        worktrees = self.worktree_manager
        if parent is None or manager is None:
            return ToolResult("Error: Agent teams are not configured.", is_error=True)
        if not isinstance(worktrees, WorktreeManager):
            return ToolResult("Error: teammate worktree isolation is unavailable.", is_error=True)
        team = manager.get_team(p.team_name or "")
        if team is None:
            return ToolResult(f"Error: unknown team {p.team_name!r}.", is_error=True)

        base_name = p.name or p.subagent_type or "teammate"
        try:
            base_name = sanitize_name(base_name)
        except ValueError as exc:
            return ToolResult(f"Error: invalid teammate name: {exc}", is_error=True)
        teammate_name = base_name
        suffix = 2
        while team.get_member(teammate_name) is not None:
            marker = f"-{suffix}"
            teammate_name = f"{base_name[: 63 - len(marker)]}{marker}"
            suffix += 1

        definition: AgentDefinition | None = None
        if p.subagent_type:
            definition = self.agent_loader.get(p.subagent_type)
            if definition is None:
                available = ", ".join(item.agent_type for item in self.agent_loader.list_agents())
                return ToolResult(
                    f"Error: unknown subagent_type {p.subagent_type!r}. Available: {available}",
                    is_error=True,
                )
            conversation = ConversationManager()
            execution_prompt = p.prompt
            agent_type = definition.agent_type
        else:
            if not self.enable_fork:
                return ToolResult("Error: Agent fork mode is disabled.", is_error=True)
            try:
                conversation = build_forked_messages(parent.conversation, p.prompt)
            except ForkError as exc:
                return ToolResult(f"Error: {exc}", is_error=True)
            execution_prompt = ""
            agent_type = "fork"

        worktree_name = f"team-{team.name}/{teammate_name}"
        try:
            worktree = await worktrees.create(worktree_name, "HEAD")
            backend = manager.detect_backend()
        except Exception as exc:  # noqa: BLE001 - lifecycle error is model-visible
            return ToolResult(f"Error: cannot prepare teammate runtime: {exc}", is_error=True)

        trace_node = self.trace_manager.create(agent_type, parent.agent_id)
        source_registry = getattr(parent, "_full_registry", parent.registry)
        restricted = resolve_agent_tools(
            source_registry,
            definition,
            is_background=True,
            work_dir=worktree.path,
        )
        teammate_registry = build_teammate_tools(
            restricted,
            backend,
            work_dir=worktree.path,
        )
        try:
            child = self._build_agent(
                definition,
                conversation=conversation,
                is_background=True,
                model_override=p.model,
                trace_node=trace_node,
                work_dir_override=worktree.path,
                registry_override=teammate_registry,
                system_addendum=TEAMMATE_ADDENDUM,
                team_name=team.name,
                team_manager=manager,
            )
            child.registry.register(SendMessageTool(manager, child))
        except Exception as exc:  # noqa: BLE001 - clean only a newly empty worktree
            self.trace_manager.complete(trace_node.agent_id, "failed", error=str(exc))
            try:
                await worktrees.remove(worktree.name, discard=True)
            except Exception:
                pass
            return ToolResult(f"Error: cannot construct teammate: {exc}", is_error=True)

        member = TeammateInfo(
            name=teammate_name,
            agent_id=child.agent_id,
            agent_type=agent_type,
            model=p.model or (definition.model if definition else "inherit"),
            worktree_path=str(worktree.path),
            backend_type=backend,
            is_active=True,
            requires_approval=p.requires_approval,
            metadata={
                "worktree_name": worktree.name,
                "worktree_branch": worktree.branch,
                "description": p.description,
                "initial_prompt": p.prompt,
            },
        )
        try:
            manager.register_member(team.name, member)
            if backend is BackendType.IN_PROCESS:
                handle = spawn_inprocess_teammate(
                    child,
                    execution_prompt,
                    teammate_name,
                    conversation,
                )
                manager.register_inprocess_handle(team.name, member, handle)
                runtime = "in-process coroutine"
            elif backend is BackendType.TMUX:
                tmux_pane = spawn_tmux_teammate(
                    team_name=team.name,
                    teammate_name=teammate_name,
                    mailbox_dir=team.directory / "mailboxes",
                    work_dir=worktree.path,
                    prompt=p.prompt,
                    agent_type=p.subagent_type or "",
                    model=p.model or "",
                )
                manager.register_pane_id(member.agent_id, tmux_pane.pane_id)
                member.metadata["pane_id"] = tmux_pane.pane_id
                team.save()
                runtime = f"tmux pane {tmux_pane.pane_id}"
            else:
                iterm_pane = spawn_iterm2_teammate(
                    team_name=team.name,
                    teammate_name=teammate_name,
                    mailbox_dir=team.directory / "mailboxes",
                    work_dir=worktree.path,
                    prompt=p.prompt,
                    agent_type=p.subagent_type or "",
                    model=p.model or "",
                )
                manager.register_pane_id(member.agent_id, iterm_pane.session_id)
                member.metadata["pane_id"] = iterm_pane.session_id
                team.save()
                runtime = f"iTerm2 session {iterm_pane.session_id}"
        except Exception as exc:  # noqa: BLE001 - preserve worktree for inspection on launch errors
            team.remove_member(member.agent_id)
            self.trace_manager.complete(trace_node.agent_id, "failed", error=str(exc))
            return ToolResult(
                f"Error: teammate launch failed: {exc}. Worktree preserved at {worktree.path}",
                is_error=True,
            )
        return ToolResult(
            f"Teammate {teammate_name!r} started via {runtime}. Agent ID: {member.agent_id}; "
            f"worktree: {worktree.path}; branch: {worktree.branch}."
        )


__all__ = [
    "AgentTool",
    "AgentToolParams",
    "PERMISSION_MODE_MAP",
    "TEAMMATE_ADDENDUM",
]
