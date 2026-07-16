"""Shared task-board tool tests for the ch04 Agent loop."""

from __future__ import annotations

import json

import pytest

from mewcode.teams import TeamManager
from mewcode.tools import ToolRegistry, register_task_tools


@pytest.mark.asyncio
async def test_task_create_get_list_update_round_trip() -> None:
    manager = TeamManager()
    registry = ToolRegistry()
    register_task_tools(registry, manager, "mew")

    created = await registry.execute(
        "TaskCreate",
        {
            "subject": "Implement Agent loop",
            "description": "Follow ch04",
            "blocked_by": ["0"],
        },
    )
    task = json.loads(created.output)
    assert task["id"] == "1"
    assert task["blocked_by"] == ["0"]

    updated = await registry.execute(
        "TaskUpdate",
        {"task_id": "1", "status": "in_progress", "blocks": ["2"]},
    )
    assert json.loads(updated.output)["status"] == "in_progress"

    fetched = await registry.execute("TaskGet", {"task_id": "1"})
    assert json.loads(fetched.output)["blocks"] == ["2"]

    listed = await registry.execute("TaskList", {"status": "in_progress"})
    assert "◐ #1 [in_progress] Implement Agent loop" in listed.output


def test_all_task_tools_are_registered_and_concurrency_safe() -> None:
    registry = ToolRegistry()
    register_task_tools(registry, TeamManager(), "mew")
    names = {"TaskCreate", "TaskGet", "TaskList", "TaskUpdate"}
    tools = [tool for tool in registry.list_tools() if tool.name in names]
    assert {tool.name for tool in tools} == names
    assert all(tool.is_concurrency_safe for tool in tools)
