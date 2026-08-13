"""In-memory parent/child execution trace registry."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class TraceNode:
    agent_id: str
    parent_id: str | None
    trace_id: str
    agent_type: str
    status: str = "running"
    input_tokens: int = 0
    output_tokens: int = 0
    start_time: datetime = datetime.min.replace(tzinfo=UTC)
    end_time: datetime | None = None
    task_id: str | None = None
    error: str = ""

    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or datetime.now(UTC)
        return max(0.0, (end - self.start_time).total_seconds())


class TraceRegistry:
    def __init__(self) -> None:
        self._nodes: dict[str, TraceNode] = {}
        self._latest_trace_id: str | None = None

    @property
    def latest_trace_id(self) -> str | None:
        return self._latest_trace_id

    def create(
        self,
        agent_type: str,
        parent_id: str | None = None,
        trace_id: str | None = None,
    ) -> TraceNode:
        actual_trace_id = trace_id or uuid.uuid4().hex[:12]
        node = TraceNode(
            agent_id=uuid.uuid4().hex[:12],
            parent_id=parent_id,
            trace_id=actual_trace_id,
            agent_type=agent_type,
            start_time=datetime.now(UTC),
        )
        self._nodes[node.agent_id] = node
        self._latest_trace_id = actual_trace_id
        return node

    def update(self, agent_id: str, **updates: Any) -> None:
        node = self._nodes.get(agent_id)
        if node is None:
            return
        for name, value in updates.items():
            if hasattr(node, name):
                setattr(node, name, value)

    def complete(self, agent_id: str, status: str = "completed", *, error: str = "") -> None:
        node = self._nodes.get(agent_id)
        if node is None:
            return
        node.status = status
        node.error = error
        node.end_time = datetime.now(UTC)

    def get(self, agent_id: str) -> TraceNode | None:
        return self._nodes.get(agent_id)

    def get_tree(self, trace_id: str) -> list[TraceNode]:
        return sorted(
            (node for node in self._nodes.values() if node.trace_id == trace_id),
            key=lambda node: node.start_time,
        )

    def get_total_tokens(self, trace_id: str) -> tuple[int, int]:
        nodes = self.get_tree(trace_id)
        return (
            sum(node.input_tokens for node in nodes),
            sum(node.output_tokens for node in nodes),
        )

    def remove(self, agent_id: str) -> None:
        self._nodes.pop(agent_id, None)


# Backwards-compatible course name.
TraceManager = TraceRegistry

__all__ = ["TraceManager", "TraceNode", "TraceRegistry"]
