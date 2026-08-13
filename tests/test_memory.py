"""CH9 instruction, session, and automatic-memory regression tests."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from simplecode.agent import Agent
from simplecode.commands import CommandContext
from simplecode.commands.handlers.memory import handle_memory
from simplecode.commands.handlers.session import handle_session
from simplecode.conversation import (
    ConversationManager,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)
from simplecode.memory import (
    MemoryManager,
    SessionManager,
    SessionMeta,
    SessionRecord,
    build_time_gap_message,
    load_instructions,
    process_includes,
    validate_message_chain,
)
from simplecode.memory.auto_memory import MEMORY_EXTRACTION_PROMPT
from simplecode.memory.session import RecordType, records_to_messages
from simplecode.tools.base import StreamEnd, StreamEvent, TextDelta


def _home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


class TestProcessIncludes:
    def test_no_includes(self, tmp_path: Path) -> None:
        assert process_includes("hello", tmp_path, tmp_path) == "hello"

    def test_basic(self, tmp_path: Path) -> None:
        (tmp_path / "style.md").write_text("use ruff", encoding="utf-8")
        assert process_includes("@include style.md", tmp_path, tmp_path) == "use ruff"

    def test_recursive(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (tmp_path / "first.md").write_text("@include sub/second.md", encoding="utf-8")
        (sub / "second.md").write_text("nested", encoding="utf-8")
        assert process_includes("@include first.md", tmp_path, tmp_path) == "nested"

    def test_depth_limit(self, tmp_path: Path) -> None:
        for index in range(7):
            content = f"@include {index + 1}.md" if index < 6 else "end"
            (tmp_path / f"{index}.md").write_text(content, encoding="utf-8")
        result = process_includes("@include 0.md", tmp_path, tmp_path)
        assert result.startswith("@include ")

    def test_path_outside_project_blocked(self, tmp_path: Path) -> None:
        root = tmp_path / "project"
        root.mkdir()
        (tmp_path / "secret.md").write_text("secret", encoding="utf-8")
        assert "blocked: path outside project" in process_includes(
            "@include ../secret.md", root, root
        )

    def test_file_not_found(self, tmp_path: Path) -> None:
        assert "skipped: file not found" in process_includes(
            "@include absent.md", tmp_path, tmp_path
        )


class TestLoadInstructions:
    def test_single_layer(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _home(monkeypatch, tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        (project / "SIMPLECODE.md").write_text("project", encoding="utf-8")
        assert load_instructions(project) == "project"

    def test_multi_layer_priority(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        home = _home(monkeypatch, tmp_path)
        project = tmp_path / "project"
        (project / ".simplecode").mkdir(parents=True)
        (home / ".simplecode").mkdir()
        (project / "SIMPLECODE.md").write_text("root", encoding="utf-8")
        (project / ".simplecode" / "SIMPLECODE.md").write_text("local", encoding="utf-8")
        (home / ".simplecode" / "SIMPLECODE.md").write_text("user", encoding="utf-8")
        assert load_instructions(project) == "root\n---\nlocal\n---\nuser"

    def test_no_files_returns_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _home(monkeypatch, tmp_path)
        project = tmp_path / "project"
        project.mkdir()
        assert load_instructions(project) == ""


class TestSessionRecord:
    def test_user_roundtrip(self) -> None:
        record = SessionRecord.from_message(Message("user", "你好"))[0]
        assert SessionRecord.from_jsonl(record.to_jsonl()) == record
        assert "你好" in record.to_jsonl()

    def test_assistant_with_tool_uses(self) -> None:
        message = Message(
            "assistant",
            "working",
            tool_uses=[ToolUseBlock("call-1", "ReadFile", {"file_path": "a.py"})],
        )
        record = SessionRecord.from_message(message)[0]
        restored = records_to_messages([record])[0]
        assert restored.content == "working"
        assert restored.tool_uses == message.tool_uses

    def test_tool_results_multiple_records(self) -> None:
        records = SessionRecord.from_message(
            Message(
                "user",
                tool_results=[
                    ToolResultBlock("a", "one"),
                    ToolResultBlock("b", "two", True),
                ],
            )
        )
        assert [record.tool_use_id for record in records] == ["a", "b"]
        assert records[1].is_error is True

    @pytest.mark.parametrize("line", ["{", "[]", '{"type":"unknown"}', "{}"])
    def test_malformed_jsonl(self, line: str) -> None:
        assert SessionRecord.from_jsonl(line) is None

    def test_plain_assistant(self) -> None:
        record = SessionRecord.from_message(Message("assistant", "done"))[0]
        assert record.type is RecordType.ASSISTANT
        assert record.content == "done"


class TestSessionMeta:
    def test_save_and_load(self, tmp_path: Path) -> None:
        path = tmp_path / "a.meta"
        meta = SessionMeta("session_20260101_010101_ab12", title="hello")
        meta.save(path)
        assert SessionMeta.load(path) == meta

    def test_load_invalid_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.meta"
        path.write_text("bad", encoding="utf-8")
        assert SessionMeta.load(path) is None


class TestSession:
    def test_append_writes_jsonl_and_updates_meta(self, tmp_path: Path) -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()
        session.append(Message("user", "hello"))
        path = manager.sessions_dir / f"{session.id}.jsonl"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["content"] == "hello"
        assert session.meta.message_count == 1
        assert session.meta.total_tokens > 0
        session.close()

    def test_title_set_from_first_user_message(self, tmp_path: Path) -> None:
        session = SessionManager(tmp_path).create()
        session.append(Message("user", "x" * 80))
        assert session.meta.title == "x" * 50
        session.append(Message("user", "second"))
        assert session.meta.title == "x" * 50
        session.close()


class TestValidateMessageChain:
    @staticmethod
    def _chain() -> list[SessionRecord]:
        assistant = SessionRecord.from_message(
            Message(
                "assistant",
                tool_uses=[ToolUseBlock("call-1", "ReadFile", {"file_path": "a"})],
            )
        )[0]
        result = SessionRecord.from_message(
            Message("user", tool_results=[ToolResultBlock("call-1", "ok")])
        )[0]
        return [SessionRecord(RecordType.USER, "go"), assistant, result]

    def test_complete_chain(self) -> None:
        chain = self._chain()
        assert validate_message_chain(chain) == len(chain)

    def test_truncate_at_missing_tool_result(self) -> None:
        chain = self._chain()[:2]
        assert validate_message_chain(chain) == 1

    def test_empty_records(self) -> None:
        assert validate_message_chain([]) == 0


class TestRecordsToMessages:
    def test_basic_roundtrip(self) -> None:
        source = [Message("user", "go"), Message("assistant", "done")]
        records = [record for message in source for record in SessionRecord.from_message(message)]
        assert records_to_messages(records) == source

    def test_tool_result_grouping(self) -> None:
        records = [
            SessionRecord(RecordType.TOOL_RESULT, "one", tool_use_id="a"),
            SessionRecord(RecordType.TOOL_RESULT, "two", tool_use_id="b"),
        ]
        messages = records_to_messages(records)
        assert len(messages) == 1
        assert [result.content for result in messages[0].tool_results] == ["one", "two"]

    def test_system_prompt_skipped_and_compression_restored(self) -> None:
        messages = records_to_messages(
            [
                SessionRecord(RecordType.USER, "old history"),
                SessionRecord(RecordType.SYSTEM_PROMPT, "system"),
                SessionRecord(RecordType.COMPRESSION, "summary"),
            ]
        )
        assert messages == [Message("user", "[摘要]\nsummary")]


class TestSessionManager:
    def test_create_and_list(self, tmp_path: Path) -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()
        session.append(Message("user", "hello"))
        assert manager.list()[0].id == session.id
        session.close()

    def test_delete(self, tmp_path: Path) -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()
        session_id = session.id
        session.close()
        assert manager.delete(session_id) is True
        assert manager.delete(session_id) is False

    def test_cleanup_removes_old_sessions(self, tmp_path: Path) -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()
        session.meta.last_active = datetime.now(UTC) - timedelta(days=31)
        session.meta.save(session.meta_path)
        session.close()
        assert manager.cleanup() == 1
        assert manager.list() == []

    def test_create_generates_valid_id(self, tmp_path: Path) -> None:
        session = SessionManager(tmp_path).create()
        assert re.fullmatch(r"session_\d{8}_\d{6}_[a-z0-9]{4}", session.id)
        session.close()


class TestSessionResume:
    def test_restores_messages_and_skips_bad_line(self, tmp_path: Path) -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()
        session.append(Message("user", "hello"))
        session.append(Message("assistant", "world"))
        session_id = session.id
        session.close()
        path = manager.sessions_dir / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write("not-json\n")
        result = manager.resume(session_id)
        assert result is not None
        assert [message.content for message in result.messages] == ["hello", "world"]
        assert "not-json" not in path.read_text(encoding="utf-8")
        result.session.close()

    def test_nonexistent_returns_none(self, tmp_path: Path) -> None:
        assert SessionManager(tmp_path).resume("session_20260101_010101_ab12") is None

    def test_truncates_incomplete_chain(self, tmp_path: Path) -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()
        session.append(Message("user", "hello"))
        session.append(
            Message(
                "assistant",
                tool_uses=[ToolUseBlock("call-1", "ReadFile", {"file_path": "a"})],
            )
        )
        session_id = session.id
        session.close()
        result = manager.resume(session_id)
        assert result is not None
        assert result.messages == [Message("user", "hello")]
        result.session.close()

    def test_repairs_stale_meta_from_jsonl(self, tmp_path: Path) -> None:
        manager = SessionManager(tmp_path)
        session = manager.create()
        session.append(Message("user", "recover metadata"))
        session_id = session.id
        session.meta.message_count = 0
        session.meta.total_tokens = 0
        session.meta.title = "新会话"
        session.meta.save(session.meta_path)
        session.close()
        result = manager.resume(session_id)
        assert result is not None
        assert result.session.meta.message_count == 1
        assert result.session.meta.total_tokens > 0
        assert result.session.meta.title == "recover metadata"
        result.session.close()


class TestTimeGapMessage:
    def test_no_gap_returns_none(self) -> None:
        assert build_time_gap_message(datetime.now(UTC) - timedelta(hours=23)) is None

    def test_gap_returns_message(self) -> None:
        now = datetime(2026, 1, 3, tzinfo=UTC)
        message = build_time_gap_message(now - timedelta(hours=25), now=now)
        assert message is not None
        assert "25 小时" in message.content
        assert "代码可能有变更" in message.content

    def test_two_days_uses_days(self) -> None:
        now = datetime(2026, 1, 3, tzinfo=UTC)
        message = build_time_gap_message(now - timedelta(hours=50), now=now)
        assert message is not None and "2 天" in message.content


class MemoryClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompt = ""

    async def stream(
        self,
        conversation: ConversationManager,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        del system, tools
        self.prompt = conversation.history[0].content
        yield TextDelta(self.response)
        yield StreamEnd("end_turn")


class TestMemoryManager:
    def test_load_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _home(monkeypatch, tmp_path)
        assert MemoryManager(tmp_path / "project").load() == ""

    def test_load_merges_user_and_project(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        home = _home(monkeypatch, tmp_path)
        project = tmp_path / "project"
        (home / ".simplecode").mkdir()
        (project / ".simplecode").mkdir(parents=True)
        (home / ".simplecode" / "memories.md").write_text("user", encoding="utf-8")
        (project / ".simplecode" / "memories.md").write_text("project", encoding="utf-8")
        assert MemoryManager(project).load() == "user\n\nproject"

    def test_clear(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        home = _home(monkeypatch, tmp_path)
        project = tmp_path / "project"
        (home / ".simplecode").mkdir()
        (project / ".simplecode").mkdir(parents=True)
        manager = MemoryManager(project)
        manager.user_path.write_text("user", encoding="utf-8")
        manager.project_path.write_text("project", encoding="utf-8")
        manager.clear()
        assert manager.user_path.read_text(encoding="utf-8") == ""
        assert manager.project_path.read_text(encoding="utf-8") == ""

    def test_get_display_text_empty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        _home(monkeypatch, tmp_path)
        assert MemoryManager(tmp_path / "project").get_display_text() == ("当前没有任何自动记忆。")

    def test_write_memories_splits_correctly_and_filters_placeholders(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _home(monkeypatch, tmp_path)
        manager = MemoryManager(tmp_path / "project")
        manager._write_memories(
            "### 用户偏好\n- 用中文\n"
            "### 纠正反馈\n- 暂无\n"
            "### 项目知识\n- FastAPI\n"
            "### 参考资料\n- ...\n"
        )
        assert "用中文" in manager.user_path.read_text(encoding="utf-8")
        assert "FastAPI" in manager.project_path.read_text(encoding="utf-8")
        assert "暂无" not in manager.load()
        assert "..." not in manager.load()


class TestMemoryExtraction:
    def test_extraction_prompt_contains_categories(self) -> None:
        for category in ("用户偏好", "纠正反馈", "项目知识", "参考资料"):
            assert category in MEMORY_EXTRACTION_PROMPT
        assert "不要重复添加" in MEMORY_EXTRACTION_PROMPT
        assert "不要调用任何工具" in MEMORY_EXTRACTION_PROMPT

    @pytest.mark.asyncio
    async def test_extract_writes_and_uses_incremental_context(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _home(monkeypatch, tmp_path)
        manager = MemoryManager(tmp_path / "project")
        conversation = ConversationManager([Message("user", "请记住我喜欢简洁答案")])
        client = MemoryClient("### 用户偏好\n- 喜欢简洁答案")
        await manager.extract(client, conversation)
        assert "喜欢简洁答案" in manager.user_path.read_text(encoding="utf-8")
        assert "请记住我喜欢简洁答案" in client.prompt

    @pytest.mark.asyncio
    async def test_agent_triggers_background_extraction_every_five_turns(
        self, tmp_path: Path
    ) -> None:
        class Client:
            config = SimpleNamespace(protocol="anthropic")
            max_output_tokens = 1_000

            async def stream(
                self,
                conversation: ConversationManager,
                system: str | None = None,
                tools: list[dict[str, Any]] | None = None,
            ) -> AsyncIterator[StreamEvent]:
                del conversation, system, tools
                yield TextDelta("done")
                yield StreamEnd("end_turn", 1, 1)

            def set_max_output_tokens(self, value: int) -> None:
                self.max_output_tokens = value

        class Memories:
            calls = 0

            def load(self) -> str:
                return ""

            async def extract(
                self,
                client: Any,
                conversation: ConversationManager,
                protocol: str,
            ) -> None:
                del client, conversation, protocol
                self.calls += 1

        memories = Memories()
        conversation = ConversationManager()
        agent = Agent(Client(), work_dir=tmp_path, memory_manager=memories)  # type: ignore[arg-type]
        for index in range(5):
            assert await agent.run_to_completion(f"turn {index}", conversation) == "done"
        await agent.flush_memories()
        assert memories.calls == 1


class TestConversationInjection:
    def test_inject_long_term_memory(self) -> None:
        conversation = ConversationManager()
        assert conversation.inject_long_term_memory("rules", "facts") is True
        assert [message.content for message in conversation.history] == [
            "## 项目指令\nrules",
            "## 自动记忆\nfacts",
            "好的，我已了解项目背景和记忆。",
        ]

    def test_inject_idempotent(self) -> None:
        conversation = ConversationManager()
        conversation.inject_long_term_memory("rules", "")
        assert conversation.inject_long_term_memory("again", "facts") is False

    @pytest.mark.parametrize(
        ("instructions", "memories", "expected"),
        [
            ("rules", "", "## 项目指令\nrules"),
            ("", "facts", "## 自动记忆\nfacts"),
        ],
    )
    def test_inject_one_source(self, instructions: str, memories: str, expected: str) -> None:
        conversation = ConversationManager()
        assert conversation.inject_long_term_memory(instructions, memories)
        assert conversation.history[0].content == expected
        assert conversation.history[-1].role == "assistant"

    def test_inject_nothing(self) -> None:
        conversation = ConversationManager()
        assert conversation.inject_long_term_memory("", "") is False
        assert conversation.history == []
        assert conversation.ltm_injected is False

    def test_replace_history_resets_ltm(self) -> None:
        conversation = ConversationManager()
        conversation.inject_environment("env")
        conversation.inject_long_term_memory("rules", "facts")
        conversation.replace_history([Message("user", "restored")])
        assert conversation.env_injected is False
        assert conversation.ltm_injected is False


class TestCommands:
    @pytest.mark.asyncio
    async def test_memory_commands(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _home(monkeypatch, tmp_path)
        manager = MemoryManager(tmp_path / "project")
        messages: list[str] = []

        class StubUI:
            async def add_system_message(self, text: str) -> None:
                messages.append(text)

            async def send_user_message(self, text: str) -> None:
                del text

            def set_plan_mode(self, enabled: bool) -> None:
                del enabled

            def get_token_count(self) -> int:
                return 0

            def refresh_status(self) -> None:
                return

        ctx = CommandContext(
            args="",
            agent=None,
            conversation=ConversationManager(),
            session=None,
            session_manager=None,
            memory_manager=manager,
            ui=StubUI(),
        )
        for argument in ("", "edit", "clear", "unknown"):
            ctx.args = argument
            await handle_memory(ctx)
        assert messages[0] == "当前没有任何自动记忆。"
        assert "用户级" in messages[1]
        assert messages[2] == "所有自动记忆已清空。"
        assert messages[3].startswith("用法")

    @pytest.mark.asyncio
    async def test_session_list_new_resume_delete(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        _home(monkeypatch, tmp_path)
        manager = SessionManager(tmp_path / "project")
        archived = manager.create()
        archived.append(Message("user", "archived"))
        archived_id = archived.id
        archived.close()
        current = manager.create()
        agent = SimpleNamespace(
            conversation=ConversationManager(),
            _loop_count=4,
            context_window=200_000,
        )
        messages: list[str] = []

        class StubUI:
            async def add_system_message(self, text: str) -> None:
                messages.append(text)

            async def send_user_message(self, text: str) -> None:
                del text

            def set_plan_mode(self, enabled: bool) -> None:
                del enabled

            def get_token_count(self) -> int:
                return 0

            def refresh_status(self) -> None:
                return

        ctx = CommandContext(
            args="",
            session_manager=manager,
            session=current,
            conversation=agent.conversation,
            agent=agent,
            memory_manager=MemoryManager(tmp_path / "project"),
            ui=StubUI(),
            config={"resume_candidates": []},
        )
        ctx.config["set_session"] = lambda value: setattr(ctx, "session", value)
        ctx.config["set_conversation"] = lambda value: (
            setattr(ctx, "conversation", value),
            setattr(ctx.agent, "conversation", value),
        )
        ctx.args = "list"
        await handle_session(ctx)
        assert "archived" in messages[-1]
        ctx.args = "resume"
        await handle_session(ctx)
        assert "可恢复会话" in messages[-1]
        index = ctx.config["resume_candidates"].index(archived_id) + 1
        ctx.args = f"resume {index}"
        await handle_session(ctx)
        assert "已恢复会话" in messages[-1]
        assert ctx.conversation.history[0].content == "archived"
        assert ctx.agent._loop_count == 0
        old_id = ctx.session.id
        ctx.args = "new"
        await handle_session(ctx)
        assert "已创建新会话" in messages[-1]
        ctx.args = f"delete {old_id}"
        await handle_session(ctx)
        assert "会话已删除" in messages[-1]
        ctx.session.close()
