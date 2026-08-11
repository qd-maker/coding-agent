"""LLM-maintained user and project memories."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from mewcode.conversation import ConversationManager, Message
from mewcode.tools.base import StreamEvent, TextDelta

USER_MEMORIES_RELPATH = ".mewcode/memories.md"
PROJECT_MEMORIES_RELPATH = ".mewcode/memories.md"

MEMORY_EXTRACTION_PROMPT = """\
请根据当前 memories.md 和最近对话，输出一份完整、更新后的 memories.md。

只保留未来对话中仍然有用、明确且可复用的信息，并严格使用以下四个三级标题：

### 用户偏好
- 用户长期稳定的表达、工具、流程或输出偏好。

### 纠正反馈
- 用户对助手行为的纠正，以及今后应避免重复的错误。

### 项目知识
- 项目的技术栈、架构、约定、关键路径、当前事实和决策。

### 参考资料
- 用户明确要求保留的链接、文档、命令或其他参考信息。

规则：
1. 输出完整文件，不要只输出增量或解释。
2. 对相同含义的条目进行语义去重，不要重复添加。
3. 没有值得记忆的内容，该分类下留空（不要写任何条目，不要写占位符）。
4. 不要猜测，不要保存临时任务状态、寒暄、工具原始输出或敏感凭据。
5. 不要调用任何工具。
"""

_USER_LEVEL_HEADERS = {"用户偏好", "纠正反馈"}
_PROJECT_LEVEL_HEADERS = {"项目知识", "参考资料"}
_PLACEHOLDERS = {"", "...", "…", "无", "暂无", "n/a"}


class MemoryManager:
    """Load, rewrite, display, and clear two levels of durable notes."""

    def __init__(self, project_root: str | Path) -> None:
        self._user_path = Path.home() / USER_MEMORIES_RELPATH
        self._project_path = Path(project_root).resolve() / PROJECT_MEMORIES_RELPATH
        self._last_extraction_msg_count = 0

    @property
    def user_path(self) -> Path:
        return self._user_path

    @property
    def project_path(self) -> Path:
        return self._project_path

    @staticmethod
    def _read(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def load(self) -> str:
        """Return user-level notes first, followed by project-level notes."""
        sections: list[str] = []
        seen: set[Path] = set()
        for path in (self.user_path, self.project_path):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if content := self._read(path):
                sections.append(content)
        return "\n\n".join(sections)

    def get_memories(self) -> list[str]:
        """Compatibility adapter used by the Agent context injector."""
        content = self.load()
        return [content] if content else []

    def reset_cursor(self, message_count: int = 0) -> None:
        self._last_extraction_msg_count = max(0, message_count)

    async def extract(
        self,
        client: Any,
        conversation: ConversationManager,
        protocol: str = "anthropic",
    ) -> None:
        """Ask a separate LLM turn to rewrite memories from new conversation text."""
        del protocol
        history = conversation.history
        start = self._last_extraction_msg_count
        # Auto-compact replaces history with a shorter list. Restarting at zero in
        # that case preserves the compact summary and post-compact dialogue.
        recent = history[start:] if start <= len(history) else history
        lines: list[str] = []
        for message in recent:
            if not message.content.strip():
                continue
            if self._is_injected_context(message.content):
                continue
            role = "用户" if message.role == "user" else "助手"
            lines.append(f"{role}: {message.content.strip()}")
        if not lines:
            self._last_extraction_msg_count = len(history)
            return

        prompt = (
            f"{MEMORY_EXTRACTION_PROMPT}\n\n"
            f"## 当前 memories.md\n{self.load() or '(空)'}\n\n"
            "## 最近对话\n" + "\n".join(lines)
        )
        extract_conv = ConversationManager([Message(role="user", content=prompt)])
        collected: list[str] = []
        try:
            stream: AsyncIterator[StreamEvent] = client.stream(
                extract_conv,
                system="你是一个记忆提取助手。",
                tools=None,
            )
            async for event in stream:
                if isinstance(event, TextDelta):
                    collected.append(event.text)
        except Exception:  # noqa: BLE001 - memory is best-effort by design
            return

        content = "".join(collected).strip()
        if not content:
            return
        self._write_memories(content)
        self._last_extraction_msg_count = len(history)

    @staticmethod
    def _is_injected_context(content: str) -> bool:
        return content.startswith(
            (
                "<environment>",
                "<system-reminder>",
                "## 项目指令\n",
                "## 自动记忆\n",
                "[系统提示] 距离上次会话",
            )
        )

    def _write_memories(self, content: str) -> None:
        user_sections: list[tuple[str, list[str]]] = []
        project_sections: list[tuple[str, list[str]]] = []
        header: str | None = None
        lines: list[str] = []

        def assign() -> None:
            if header is None:
                return
            self._assign_section(
                header,
                lines,
                user_sections,
                project_sections,
            )

        for raw_line in content.splitlines():
            stripped = raw_line.strip()
            if stripped.startswith("### "):
                assign()
                header = stripped.removeprefix("### ").strip()
                lines = []
            elif header is not None:
                lines.append(stripped)
        assign()

        self._write_sections(self.user_path, user_sections)
        if self.project_path.resolve() != self.user_path.resolve():
            self._write_sections(self.project_path, project_sections)
        else:
            self._write_sections(
                self.project_path,
                [*user_sections, *project_sections],
            )

    @staticmethod
    def _write_sections(path: Path, sections: list[tuple[str, list[str]]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = "\n\n".join(
            f"### {header}\n" + "\n".join(lines) for header, lines in sections if lines
        ).strip()
        path.write_text(f"{rendered}\n" if rendered else "", encoding="utf-8")

    @staticmethod
    def _is_placeholder(line: str) -> bool:
        normalized = line.strip().removeprefix("-").strip().casefold()
        return normalized in _PLACEHOLDERS

    @staticmethod
    def _assign_section(
        header: str,
        lines: list[str],
        user_sections: list[tuple[str, list[str]]],
        project_sections: list[tuple[str, list[str]]],
    ) -> None:
        items = [
            line
            for line in lines
            if line.startswith("- ") and not MemoryManager._is_placeholder(line)
        ]
        if not items:
            return
        if any(name in header for name in _USER_LEVEL_HEADERS):
            user_sections.append((header, items))
        elif any(name in header for name in _PROJECT_LEVEL_HEADERS):
            project_sections.append((header, items))

    def clear(self) -> None:
        seen: set[Path] = set()
        for path in (self.user_path, self.project_path):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            if path.exists():
                try:
                    path.write_text("", encoding="utf-8")
                except OSError:
                    continue

    def get_display_text(self) -> str:
        sections: list[str] = []
        user_content = self._read(self.user_path)
        project_content = self._read(self.project_path)
        if user_content:
            sections.append(f"[用户级] {self.user_path}\n{user_content}")
        if project_content and self.project_path.resolve() != self.user_path.resolve():
            sections.append(f"[项目级] {self.project_path}\n{project_content}")
        if not sections:
            return "当前没有任何自动记忆。"
        return "\n\n".join(sections)


__all__ = [
    "MEMORY_EXTRACTION_PROMPT",
    "PROJECT_MEMORIES_RELPATH",
    "USER_MEMORIES_RELPATH",
    "MemoryManager",
]
