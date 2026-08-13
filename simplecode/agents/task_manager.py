"""Background sub-agent lifecycle, timeout, cancellation and completion queue."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from simplecode.agents.trace import TraceNode, TraceRegistry

if TYPE_CHECKING:
    from simplecode.agent import Agent


@dataclass(slots=True)
class ProgressInfo:
    turns: int = 0
    message: str = ""


@dataclass(slots=True)
class BackgroundTask:
    task_id: str
    agent_type: str
    description: str
    status: str = "running"
    result: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    end_time: datetime | None = None
    progress: ProgressInfo = field(default_factory=ProgressInfo)
    trace_id: str = ""
    agent_id: str = ""
    agent: Any = field(default=None, repr=False)
    execution_task: asyncio.Task[str] | None = field(default=None, repr=False)

    @property
    def elapsed_seconds(self) -> float:
        end = self.end_time or datetime.now(UTC)
        return max(0.0, (end - self.start_time).total_seconds())


class TaskManager:
    def __init__(
        self,
        trace_registry: TraceRegistry | None = None,
        *,
        default_timeout: float = 600.0,
    ) -> None:
        self.trace_registry = trace_registry or TraceRegistry()
        self.default_timeout = default_timeout
        self._tasks: dict[str, BackgroundTask] = {}
        self._async_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_index: dict[asyncio.Task[str], str] = {}
        self._notify_queue: asyncio.Queue[str] = asyncio.Queue()

    async def launch(
        self,
        agent: Agent,
        prompt: str,
        *,
        agent_type: str,
        description: str,
        trace_node: TraceNode,
        conversation: Any | None = None,
        timeout: float | None = None,
    ) -> BackgroundTask:
        execution = asyncio.create_task(agent.run_to_completion(prompt, conversation))
        return await self.adopt_task(
            execution,
            agent,
            agent_type=agent_type,
            description=description,
            trace_node=trace_node,
            timeout=timeout,
        )

    async def adopt_task(
        self,
        execution_task: asyncio.Task[str],
        agent: Agent,
        *,
        agent_type: str,
        description: str,
        trace_node: TraceNode,
        timeout: float | None = None,
    ) -> BackgroundTask:
        existing_id = self._task_index.get(execution_task)
        if existing_id is not None:
            return self._tasks[existing_id]
        task_id = uuid.uuid4().hex[:8]
        record = BackgroundTask(
            task_id=task_id,
            agent_type=agent_type,
            description=description,
            trace_id=trace_node.trace_id,
            agent_id=trace_node.agent_id,
            agent=agent,
            execution_task=execution_task,
        )
        self._tasks[task_id] = record
        self._task_index[execution_task] = task_id
        trace_node.task_id = task_id
        monitor = asyncio.create_task(
            self._run_background(record, execution_task, timeout or self.default_timeout)
        )
        self._async_tasks[task_id] = monitor
        return record

    async def adopt_running(
        self,
        agent: Agent,
        *,
        execution_task: asyncio.Task[str] | None = None,
        prompt: str = "",
        agent_type: str = "fork",
        description: str = "Detached foreground sub-agent",
        trace_node: TraceNode | None = None,
        timeout: float | None = None,
    ) -> BackgroundTask:
        node = trace_node or self.trace_registry.create(agent_type, agent.agent_id)
        if execution_task is None:
            execution_task = asyncio.create_task(agent.run_to_completion(prompt))
        return await self.adopt_task(
            execution_task,
            agent,
            agent_type=agent_type,
            description=description,
            trace_node=node,
            timeout=timeout,
        )

    async def _run_background(
        self,
        record: BackgroundTask,
        execution_task: asyncio.Task[str],
        timeout: float,
    ) -> None:
        try:
            record.result = await asyncio.wait_for(execution_task, timeout=timeout)
            record.status = "completed"
        except TimeoutError:
            record.status = "failed"
            record.error = f"Background Agent timed out after {timeout:g}s"
        except asyncio.CancelledError:
            if not execution_task.done():
                execution_task.cancel()
            await asyncio.gather(execution_task, return_exceptions=True)
            record.status = "cancelled"
        except Exception as exc:  # noqa: BLE001 - failure is task state, not process failure
            record.status = "failed"
            record.error = f"{type(exc).__name__}: {exc}"
        finally:
            record.end_time = datetime.now(UTC)
            record.input_tokens = int(getattr(record.agent, "total_input_tokens", 0))
            record.output_tokens = int(getattr(record.agent, "total_output_tokens", 0))
            self.trace_registry.update(
                record.agent_id,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
            )
            self.trace_registry.complete(
                record.agent_id,
                record.status,
                error=record.error,
            )
            self._task_index.pop(execution_task, None)
            self._async_tasks.pop(record.task_id, None)
            await self._notify_queue.put(record.task_id)

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return sorted(self._tasks.values(), key=lambda item: item.start_time, reverse=True)

    async def cancel(self, task_id: str) -> bool:
        record = self._tasks.get(task_id)
        monitor = self._async_tasks.get(task_id)
        if record is None or monitor is None or record.status != "running":
            return False
        execution = record.execution_task
        if execution is not None and not execution.done():
            execution.cancel()
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        if execution is not None:
            await asyncio.gather(execution, return_exceptions=True)
        # A monitor cancelled before its coroutine's first instruction never enters
        # _run_background's finally block, so finish the state transition here.
        if record.status == "running":
            record.status = "cancelled"
            record.end_time = datetime.now(UTC)
            record.input_tokens = int(getattr(record.agent, "total_input_tokens", 0))
            record.output_tokens = int(getattr(record.agent, "total_output_tokens", 0))
            self.trace_registry.update(
                record.agent_id,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
            )
            self.trace_registry.complete(record.agent_id, "cancelled")
            await self._notify_queue.put(record.task_id)
        self._async_tasks.pop(task_id, None)
        return True

    def poll_completed(self) -> list[BackgroundTask]:
        completed: list[BackgroundTask] = []
        while True:
            try:
                task_id = self._notify_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            record = self._tasks.get(task_id)
            if record is not None:
                completed.append(record)
        return completed

    async def shutdown(self) -> None:
        running = [task for task in self._async_tasks.values() if not task.done()]
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)


__all__ = ["BackgroundTask", "ProgressInfo", "TaskManager"]
