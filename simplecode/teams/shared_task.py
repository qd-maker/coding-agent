"""Crash-safe shared task board used by an Agent team."""

from __future__ import annotations

import importlib
import json
import os
import threading
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

TaskStatus = Literal["pending", "in_progress", "completed", "blocked"]
TaskId = int | str
_STATUSES = {"pending", "in_progress", "completed", "blocked"}


@dataclass(slots=True)
class SharedTask:
    id: int
    title: str
    description: str = ""
    status: TaskStatus = "pending"
    assignee: str = ""
    blocks: list[TaskId] = field(default_factory=list)
    blocked_by: list[TaskId] = field(default_factory=list)
    created_by: str = ""

    @property
    def subject(self) -> str:
        """Backward-compatible title alias used by the CH10 task tools."""

        return self.title

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["id"] = str(self.id)
        value["blocks"] = [str(item) for item in self.blocks]
        value["blocked_by"] = [str(item) for item in self.blocked_by]
        value["subject"] = self.title
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> SharedTask:
        status = str(value.get("status", "pending"))
        if status not in _STATUSES:
            status = "pending"
        return cls(
            id=int(value["id"]),
            title=str(value.get("title", value.get("subject", ""))),
            description=str(value.get("description", "")),
            status=status,  # type: ignore[arg-type]
            assignee=str(value.get("assignee", "")),
            blocks=[int(item) for item in value.get("blocks", [])],
            blocked_by=[int(item) for item in value.get("blocked_by", [])],
            created_by=str(value.get("created_by", "")),
        )


class SharedTaskStore:
    """Small atomic JSON store; an omitted path provides legacy in-memory mode."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).resolve() if path is not None else None
        self._lock = threading.RLock()
        self._next_id = 1
        self._tasks: dict[int, SharedTask] = {}
        self._load()

    @contextmanager
    def _disk_lock(self) -> Any:
        """Serialize read-modify-write cycles across teammate processes."""

        if self.path is None:
            yield
            return
        lock_path = self.path.with_suffix(f"{self.path.suffix}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - exercised on POSIX CI
                fcntl: Any = importlib.import_module("fcntl")

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised on POSIX CI
                    fcntl = importlib.import_module("fcntl")

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            tasks = raw.get("tasks", []) if isinstance(raw, dict) else []
            self._tasks = {
                task.id: task
                for item in tasks
                if isinstance(item, dict)
                for task in [SharedTask.from_dict(item)]
            }
            self._next_id = max(
                int(raw.get("next_id", 1)),
                max(self._tasks, default=0) + 1,
            )
        except (OSError, ValueError, TypeError):
            # A damaged task board must not prevent the team from loading. Keep the
            # file for diagnosis and start from an empty in-memory view.
            self._tasks = {}
            self._next_id = 1

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f"{self.path.suffix}.tmp")
        payload = {
            "next_id": self._next_id,
            "tasks": [
                asdict(task) for task in sorted(self._tasks.values(), key=lambda item: item.id)
            ],
        }
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def init_empty(self) -> None:
        with self._lock:
            with self._disk_lock():
                self._next_id = 1
                self._tasks.clear()
                self._save()

    def create(
        self,
        subject: str,
        description: str = "",
        blocks: Sequence[TaskId] | None = None,
        blocked_by: Sequence[TaskId] | None = None,
        *,
        assignee: str = "",
        created_by: str = "",
    ) -> SharedTask:
        title = subject.strip()
        if not title:
            raise ValueError("task title cannot be empty")
        with self._lock:
            with self._disk_lock():
                self._load()
                task = SharedTask(
                    id=self._next_id,
                    title=title,
                    description=description,
                    assignee=assignee,
                    blocks=list(dict.fromkeys(blocks or [])),
                    blocked_by=list(dict.fromkeys(blocked_by or [])),
                    created_by=created_by,
                )
                self._tasks[task.id] = task
                self._next_id += 1
                self._save()
                return task

    def get(self, task_id: TaskId) -> SharedTask | None:
        with self._lock:
            with self._disk_lock():
                self._load()
                try:
                    return self._tasks.get(int(task_id))
                except (TypeError, ValueError):
                    return None

    def list_tasks(
        self,
        status: str | None = None,
        assignee: str | None = None,
    ) -> list[SharedTask]:
        with self._lock:
            with self._disk_lock():
                self._load()
                tasks = sorted(self._tasks.values(), key=lambda item: item.id)
                if status is not None:
                    tasks = [task for task in tasks if task.status == status]
                if assignee is not None:
                    tasks = [task for task in tasks if task.assignee == assignee]
                return tasks

    def update(
        self,
        task_id: TaskId,
        *,
        title: str | None = None,
        subject: str | None = None,
        description: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        blocks: list[TaskId] | None = None,
        blocked_by: list[TaskId] | None = None,
        add_blocks: list[TaskId] | None = None,
        add_blocked_by: list[TaskId] | None = None,
    ) -> SharedTask | None:
        with self._lock:
            with self._disk_lock():
                self._load()
                try:
                    task = self._tasks.get(int(task_id))
                except (TypeError, ValueError):
                    task = None
                if task is None:
                    return None
                new_title = title if title is not None else subject
                if new_title is not None:
                    if not new_title.strip():
                        raise ValueError("task title cannot be empty")
                    task.title = new_title.strip()
                if description is not None:
                    task.description = description
                if status is not None:
                    if status not in _STATUSES:
                        raise ValueError(f"invalid task status: {status}")
                    task.status = status  # type: ignore[assignment]
                if assignee is not None:
                    task.assignee = assignee
                if blocks is not None:
                    task.blocks = list(dict.fromkeys(blocks))
                if blocked_by is not None:
                    task.blocked_by = list(dict.fromkeys(blocked_by))
                task.blocks = list(dict.fromkeys([*task.blocks, *(add_blocks or [])]))
                task.blocked_by = list(dict.fromkeys([*task.blocked_by, *(add_blocked_by or [])]))
                self._save()
                return task


# Compatibility names used by earlier chapters.
TaskRecord = SharedTask
TaskStore = SharedTaskStore

__all__ = ["SharedTask", "SharedTaskStore", "TaskRecord", "TaskStatus", "TaskStore"]
