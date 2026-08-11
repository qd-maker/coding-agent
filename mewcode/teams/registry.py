"""Thread-safe process-local human-name to Agent-ID registry."""

from __future__ import annotations

import threading


class AgentNameRegistry:
    _instance: AgentNameRegistry | None = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._lock = threading.RLock()

    @classmethod
    def instance(cls) -> AgentNameRegistry:
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def register(self, name: str, agent_id: str) -> None:
        with self._lock:
            existing = self._names.get(name)
            if existing not in {None, agent_id}:
                raise ValueError(f"Agent name already registered: {name}")
            self._names[name] = agent_id

    def resolve(self, name_or_id: str) -> str | None:
        with self._lock:
            if name_or_id in self._names:
                return self._names[name_or_id]
            return name_or_id if name_or_id in self._names.values() else None

    def unregister(self, name: str) -> None:
        with self._lock:
            self._names.pop(name, None)

    def list_all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._names)


__all__ = ["AgentNameRegistry"]
