"""Process-local team state with a stable API for later persistent backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from threading import RLock
from typing import Any

VALID_STATUSES = {"pending", "in_progress", "completed", "blocked"}


@dataclass(slots=True)
class TaskRecord:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"
    blocks: list[str] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._next_id = 1
        self._lock = RLock()

    def create(
        self,
        subject: str,
        description: str = "",
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
    ) -> TaskRecord:
        with self._lock:
            task_id = str(self._next_id)
            self._next_id += 1
            task = TaskRecord(
                id=task_id,
                subject=subject,
                description=description,
                blocks=list(blocks or []),
                blocked_by=list(blocked_by or []),
            )
            self._tasks[task_id] = task
            return task

    def get(self, task_id: str) -> TaskRecord | None:
        with self._lock:
            return self._tasks.get(task_id)

    def list_tasks(self, status: str | None = None) -> list[TaskRecord]:
        with self._lock:
            tasks = list(self._tasks.values())
        return [task for task in tasks if status is None or task.status == status]

    def update(self, task_id: str, **updates: Any) -> TaskRecord | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for name, value in updates.items():
                if value is not None and hasattr(task, name):
                    setattr(task, name, list(value) if name in {"blocks", "blocked_by"} else value)
            return task


class TeamManager:
    def __init__(self) -> None:
        self._task_stores: dict[str, TaskStore] = {}
        self._mailboxes: dict[str, list[str]] = {}
        self._lock = RLock()

    def get_task_store(self, team_name: str) -> TaskStore:
        with self._lock:
            return self._task_stores.setdefault(team_name, TaskStore())

    def send_message(self, agent_id: str, message: str) -> None:
        with self._lock:
            self._mailboxes.setdefault(agent_id, []).append(message)

    def consume_mailbox(self, agent_id: str) -> list[str]:
        with self._lock:
            return self._mailboxes.pop(agent_id, [])


__all__ = ["TaskRecord", "TaskStore", "TeamManager", "VALID_STATUSES"]
