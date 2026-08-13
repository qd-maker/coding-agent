"""Core tool and registry behavior tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel

from simplecode.cache import FileCache
from simplecode.tools import ToolRegistry, create_default_registry
from simplecode.tools.base import MAX_OUTPUT_CHARS, Tool, ToolResult
from simplecode.tools.bash import Bash, _decode_output
from simplecode.tools.bash import Params as BashParams
from simplecode.tools.edit_file import EditFile
from simplecode.tools.edit_file import Params as EditParams
from simplecode.tools.glob import Glob
from simplecode.tools.glob import Params as GlobParams
from simplecode.tools.grep import Grep
from simplecode.tools.grep import Params as GrepParams
from simplecode.tools.read_file import Params as ReadParams
from simplecode.tools.read_file import ReadFile
from simplecode.tools.write_file import Params as WriteParams
from simplecode.tools.write_file import WriteFile


class EmptyParams(BaseModel):
    pass


class SlowTool(Tool):
    name = "Slow"
    description = "Wait too long"
    params_model: ClassVar[type[BaseModel]] = EmptyParams
    category = "read"
    execution_timeout = 0.01

    async def execute(self, params: EmptyParams) -> ToolResult:
        del params
        await asyncio.sleep(1)
        return ToolResult("late")


class BrokenTool(Tool):
    name = "Broken"
    description = "Raise an exception"
    params_model: ClassVar[type[BaseModel]] = EmptyParams
    category = "command"

    async def execute(self, params: EmptyParams) -> ToolResult:
        del params
        raise RuntimeError("boom")


class LargeTool(Tool):
    name = "Large"
    description = "Return a large result"
    params_model: ClassVar[type[BaseModel]] = EmptyParams
    category = "read"

    async def execute(self, params: EmptyParams) -> ToolResult:
        del params
        return ToolResult("x" * (MAX_OUTPUT_CHARS + 20))


def test_default_registry_and_protocol_schemas() -> None:
    registry = create_default_registry()
    assert [tool.name for tool in registry.list_tools()] == [
        "ReadFile",
        "WriteFile",
        "EditFile",
        "Bash",
        "Glob",
        "Grep",
    ]
    anthropic = registry.get_all_schemas("anthropic")
    openai = registry.get_all_schemas("openai")
    assert anthropic[0]["input_schema"]["type"] == "object"
    assert openai[0]["type"] == "function"
    assert openai[0]["parameters"]["type"] == "object"


@pytest.mark.asyncio
async def test_registry_returns_structured_failures_and_truncates() -> None:
    registry = ToolRegistry()
    registry.register(SlowTool())
    registry.register(BrokenTool())
    registry.register(LargeTool())

    unknown = await registry.execute("Missing", {})
    invalid = await registry.execute("Slow", {"extra": "ignored"})
    timeout = await registry.execute("Slow", {})
    broken = await registry.execute("Broken", {})
    large = await registry.execute("Large", {})

    assert unknown.is_error and "unknown" in unknown.output
    assert not invalid.is_error or "timed out" in invalid.output
    assert timeout.is_error and "timed out" in timeout.output
    assert broken.is_error and "RuntimeError: boom" in broken.output
    assert "output truncated" in large.output
    assert len(large.output) == MAX_OUTPUT_CHARS


@pytest.mark.asyncio
async def test_read_write_and_cache(tmp_path: Path) -> None:
    cache = FileCache()
    target = tmp_path / "nested" / "demo.txt"
    written = await WriteFile(cache).execute(WriteParams(file_path=str(target), content="a\nb\nc"))
    assert not written.is_error
    result = await ReadFile(cache).execute(ReadParams(file_path=str(target), offset=1, limit=2))
    assert result.output == "2\tb\n3\tc"
    assert cache.get(target) == "a\nb\nc"


@pytest.mark.asyncio
async def test_edit_file_requires_unique_match(tmp_path: Path) -> None:
    target = tmp_path / "demo.txt"
    target.write_text("one two two", encoding="utf-8")
    edit = EditFile()

    missing = await edit.execute(
        EditParams(file_path=str(target), old_string="zero", new_string="0")
    )
    repeated = await edit.execute(
        EditParams(file_path=str(target), old_string="two", new_string="2")
    )
    success = await edit.execute(
        EditParams(file_path=str(target), old_string="one", new_string="1")
    )

    assert missing.is_error and "not found" in missing.output
    assert repeated.is_error and "found 2 times" in repeated.output
    assert not success.is_error
    assert target.read_text(encoding="utf-8") == "1 two two"


@pytest.mark.asyncio
async def test_edit_file_requires_fresh_read(tmp_path: Path) -> None:
    cache = FileCache()
    target = tmp_path / "demo.txt"
    target.write_text("alpha", encoding="utf-8")
    editor = EditFile(cache)

    unread = await editor.execute(
        EditParams(file_path=str(target), old_string="alpha", new_string="beta")
    )
    assert unread.is_error
    assert "has not been read" in unread.output
    assert target.read_text(encoding="utf-8") == "alpha"

    await ReadFile(cache).execute(ReadParams(file_path=str(target)))
    target.write_text("changed by user", encoding="utf-8")
    stale = await editor.execute(
        EditParams(file_path=str(target), old_string="alpha", new_string="beta")
    )
    assert stale.is_error
    assert "changed since last read" in stale.output
    assert target.read_text(encoding="utf-8") == "changed by user"

    await ReadFile(cache).execute(ReadParams(file_path=str(target)))
    ok = await editor.execute(
        EditParams(file_path=str(target), old_string="changed by user", new_string="beta")
    )
    assert not ok.is_error
    assert target.read_text(encoding="utf-8") == "beta"


@pytest.mark.asyncio
async def test_write_then_edit_does_not_require_reread(tmp_path: Path) -> None:
    cache = FileCache()
    target = tmp_path / "demo.txt"
    written = await WriteFile(cache).execute(
        WriteParams(file_path=str(target), content="hello world")
    )
    edited = await EditFile(cache).execute(
        EditParams(file_path=str(target), old_string="hello", new_string="hi")
    )
    assert not written.is_error
    assert not edited.is_error
    assert target.read_text(encoding="utf-8") == "hi world"


@pytest.mark.asyncio
async def test_glob_and_grep_skip_ignored_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("async def execute():\n    pass\n", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hidden.py").write_text("async def execute(): pass", encoding="utf-8")

    globbed = await Glob().execute(GlobParams(pattern="**/*.py", path=str(tmp_path)))
    grepped = await Grep().execute(
        GrepParams(pattern=r"async def\s+execute", path=str(tmp_path), include="*.py")
    )
    invalid = await Grep().execute(GrepParams(pattern="[", path=str(tmp_path)))

    assert globbed.output == "src/main.py"
    assert grepped.output.startswith("src/main.py:1:async def execute")
    assert ".git" not in grepped.output
    assert invalid.is_error and "invalid regular expression" in invalid.output


@pytest.mark.asyncio
async def test_glob_exact_filename_falls_back_to_recursive_search(tmp_path: Path) -> None:
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    target = plan_dir / "quiet-delta-0715-2356.md"
    target.write_text("# Plan", encoding="utf-8")

    exact = await Glob().execute(
        GlobParams(pattern="quiet-delta-0715-2356.md", path=str(tmp_path))
    )
    shallow_wildcard = await Glob().execute(GlobParams(pattern="*.md", path=str(tmp_path)))
    explicit_recursive = await Glob().execute(GlobParams(pattern="**/*.md", path=str(tmp_path)))

    assert exact.output == "plan/quiet-delta-0715-2356.md"
    assert shallow_wildcard.output == "No files matched the pattern."
    assert explicit_recursive.output == "plan/quiet-delta-0715-2356.md"


@pytest.mark.asyncio
async def test_bash_reports_stdout_nonzero_and_timeout() -> None:
    executable = str(Path(sys.executable))
    success = await Bash().execute(BashParams(command=f'"{executable}" -c "print(123)"', timeout=5))
    failure = await Bash().execute(
        BashParams(command=f'"{executable}" -c "import sys;sys.exit(3)"', timeout=5)
    )
    timeout = await Bash().execute(
        BashParams(command=f'"{executable}" -c "import time;time.sleep(.2)"', timeout=0.01)
    )

    assert not success.is_error and "STDOUT:" in success.output and "123" in success.output
    assert failure.is_error and "Exit code: 3" in failure.output
    assert timeout.is_error and "timed out" in timeout.output


def test_bash_decodes_windows_chinese_stderr() -> None:
    message = "'rm' 不是内部或外部命令，也不是可运行的程序"
    assert _decode_output(message.encode("gb18030")) == message


@pytest.mark.asyncio
async def test_bash_detaches_stdin_so_readers_dont_hang() -> None:
    # A command that reads stdin must not inherit the terminal and block; stdin
    # is detached to DEVNULL so it sees immediate EOF and returns well within
    # the timeout. Regression for stdio MCP servers / REPLs hanging until kill.
    executable = str(Path(sys.executable))
    result = await Bash().execute(
        BashParams(
            command=f'"{executable}" -c "import sys; sys.stdout.write(sys.stdin.read())"',
            timeout=5,
        )
    )
    assert not result.is_error
    assert "timed out" not in result.output
