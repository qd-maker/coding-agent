"""Persistent multi-agent team coordination."""

from mewcode.teams.manager import MergeResult, TaskRecord, TaskStore, TeamError, TeamManager
from mewcode.teams.models import AgentTeam, BackendType, TeammateInfo

__all__ = [
    "AgentTeam",
    "BackendType",
    "MergeResult",
    "TaskRecord",
    "TaskStore",
    "TeamError",
    "TeamManager",
    "TeammateInfo",
]
