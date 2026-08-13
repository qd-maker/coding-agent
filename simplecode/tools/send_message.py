"""Point-to-point and broadcast team mailbox tool."""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from simplecode.teams.mailbox import create_message
from simplecode.teams.models import BackendType
from simplecode.teams.registry import AgentNameRegistry
from simplecode.teams.spawn_tmux import send_keys_to_pane
from simplecode.tools.base import Tool, ToolResult

VALID_MESSAGE_TYPES = {
    "text",
    "shutdown_request",
    "shutdown_response",
    "approval_response",
}


class SendMessageParams(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    to: str = Field(min_length=1, description="Member name, agent id, lead, or '*' broadcast")
    content: str = Field(min_length=1, alias="message")
    summary: str = Field(default="", max_length=160)
    message_type: Literal["text", "shutdown_request", "shutdown_response", "approval_response"] = (
        "text"
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class SendMessageTool(Tool):
    name = "SendMessage"
    description = "Send a resumable direct/broadcast message to teammates through disk mailboxes."
    params_model: ClassVar[type[BaseModel]] = SendMessageParams
    category = "write"
    is_concurrency_safe = True

    def __init__(self, team_manager: Any, sender_agent: Any) -> None:
        self.team_manager = team_manager
        self.sender_agent = sender_agent

    def _team(self) -> Any | None:
        team_name = getattr(self.sender_agent, "team_name", "")
        if team_name:
            team = self.team_manager.get_team(team_name)
            if team is not None:
                return team
        return self.team_manager.get_team_for_teammate(
            self.sender_agent.agent_id
        ) or self.team_manager.get_team("__active__")

    async def _deliver(self, team: Any, target_id: str, params: SendMessageParams) -> None:
        mailbox = self.team_manager.get_mailbox(team.name)
        mailbox.write(
            target_id,
            create_message(
                self.sender_agent.agent_id,
                target_id,
                params.content,
                params.summary,
                params.message_type,
                params.metadata,
            ),
        )
        member = team.get_member(target_id)
        if member is not None and member.is_active is False:
            await self.team_manager.resume_member(team.name, member.agent_id)
        elif member is not None and member.backend_type is BackendType.TMUX:
            pane_id = self.team_manager.get_pane_id(member.agent_id)
            if pane_id:
                send_keys_to_pane(pane_id)

    async def execute(self, params: SendMessageParams) -> ToolResult:
        if params.message_type not in VALID_MESSAGE_TYPES:
            return ToolResult(f"Unsupported message_type: {params.message_type}", is_error=True)
        if params.message_type == "text" and not params.summary.strip():
            return ToolResult("text messages require a concise summary", is_error=True)
        team = self._team()
        if team is None:
            return ToolResult("No active team is attached to this Agent.", is_error=True)
        if params.to == "*":
            targets = [
                member.agent_id
                for member in team.members
                if member.agent_id != self.sender_agent.agent_id
            ]
            if team.lead_agent_id != self.sender_agent.agent_id:
                targets.append(team.lead_agent_id)
            for target_id in dict.fromkeys(targets):
                await self._deliver(team, target_id, params)
            return ToolResult(f"Broadcast delivered to {len(set(targets))} recipients.")

        target_id = AgentNameRegistry.instance().resolve(params.to)
        if params.to == "lead":
            target_id = team.lead_agent_id
        if target_id is None:
            member = team.get_member(params.to)
            target_id = member.agent_id if member is not None else None
        valid_ids = {member.agent_id for member in team.members} | {team.lead_agent_id}
        if target_id is None or target_id not in valid_ids:
            return ToolResult(f"Cannot resolve recipient {params.to!r}", is_error=True)
        try:
            await self._deliver(team, target_id, params)
        except Exception as exc:  # noqa: BLE001 - delivery failures are model-visible
            return ToolResult(f"Message delivery failed: {exc}", is_error=True)
        return ToolResult(f"Message delivered to {params.to} ({target_id}).")


__all__ = ["SendMessageParams", "SendMessageTool", "VALID_MESSAGE_TYPES"]
