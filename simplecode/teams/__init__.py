"""Persistent multi-agent team coordination."""

from simplecode.teams.manager import MergeResult, TaskRecord, TaskStore, TeamError, TeamManager
from simplecode.teams.models import AgentTeam, BackendType, TeammateInfo

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
