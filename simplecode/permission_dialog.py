"""Claude Code-style inline prompts for questions and tool permissions."""

from __future__ import annotations

import json
from collections.abc import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, OptionList, Static

from simplecode.agent import PermissionRequest, PermissionResponse
from simplecode.tools.ask_user import QuestionItem

_INLINE_PROMPT_CSS = """
InlineQuestionPrompt, InlinePermissionPrompt {
    width: 100%;
    height: auto;
    margin: 1 0;
    padding: 1 2;
    border-left: thick #8f7ad1;
    background: #1d1c23;
}

InlinePermissionPrompt { border-left: thick #54b89a; }

.inline-prompt-title {
    width: 100%;
    height: 1;
    color: #b5a5ec;
    text-style: bold;
}

InlinePermissionPrompt .inline-prompt-title { color: #7bd7b9; }

.inline-prompt-message, .inline-prompt-help {
    width: 100%;
    height: auto;
    margin-top: 1;
}

.inline-prompt-help { color: #817d89; }

.inline-prompt-options {
    width: 100%;
    height: auto;
    max-height: 8;
    margin-top: 1;
    padding: 0;
    border: none;
    background: #1d1c23;
}

.inline-prompt-options:focus {
    border: none;
    background: #1d1c23;
}

.inline-prompt-options > .option-list--option-highlighted,
.inline-prompt-options:focus > .option-list--option-highlighted {
    color: #f2eff8;
    background: #40384f;
    text-style: bold;
}

.inline-prompt-input {
    width: 100%;
    height: 3;
    margin-top: 1;
    border: solid #625d6d;
    background: #17171c;
}
"""


class InlineQuestionPrompt(Vertical):
    """Render AskUserQuestion inside the conversation instead of a modal screen."""

    DEFAULT_CSS = _INLINE_PROMPT_CSS
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(
        self,
        questions: list[QuestionItem],
        on_complete: Callable[[dict[str, str] | None], None],
        on_command: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(classes="inline-question-prompt")
        self.questions = questions
        self.on_complete = on_complete
        self.on_command = on_command
        self.answers: dict[str, str] = {}
        self.index = 0
        self.completed = False
        self._current_options: list[str] = []

    def compose(self) -> ComposeResult:
        first_message = self.questions[0].message if self.questions else "No question provided."
        yield Static(
            f"Simple Code question 1/{len(self.questions)}",
            classes="inline-prompt-title",
            markup=False,
        )
        yield Static(first_message, classes="inline-prompt-message", markup=False)
        yield OptionList(
            classes="inline-prompt-options",
            compact=True,
            markup=False,
        )
        yield Input(classes="inline-prompt-input")
        yield Static("", classes="inline-prompt-help", markup=False)

    def on_mount(self) -> None:
        self._show_current()

    @staticmethod
    def _options_for(question: QuestionItem) -> list[str]:
        if question.type == "confirm":
            return list(question.options or ["Yes", "No"])
        if question.type == "select":
            return list(question.options)
        return []

    def _show_current(self) -> None:
        question = self.questions[self.index]
        self.query_one(".inline-prompt-title", Static).update(
            f"Simple Code question {self.index + 1}/{len(self.questions)}"
        )
        self.query_one(".inline-prompt-message", Static).update(question.message)
        help_widget = self.query_one(".inline-prompt-help", Static)
        options_widget = self.query_one(".inline-prompt-options", OptionList)
        input_widget = self.query_one(".inline-prompt-input", Input)
        self._current_options = self._options_for(question)
        if self._current_options:
            options_widget.clear_options()
            options_widget.add_options(
                [f"{number}. {option}" for number, option in enumerate(self._current_options, 1)]
            )
            options_widget.display = True
            options_widget.disabled = False
            options_widget.highlighted = 0
            input_widget.display = False
            help_widget.update("↑/↓ move · Enter confirm · Esc cancel")
            options_widget.focus()
            return
        options_widget.display = False
        input_widget.display = True
        input_widget.disabled = False
        input_widget.value = ""
        input_widget.placeholder = "Type your answer and press Enter"
        help_widget.update("Enter confirm · Esc cancel")
        input_widget.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is not self.query_one(".inline-prompt-options", OptionList):
            return
        event.stop()
        self._accept_answer(self._current_options[event.option_index])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input is not self.query_one(".inline-prompt-input", Input):
            return
        event.stop()
        answer = event.value.strip()
        command = answer.casefold()
        if command in {"/do", "/plan"} and self.on_command is not None:
            self.on_command(command)
            self._finish(None)
            return
        if answer:
            self._accept_answer(answer)

    def _accept_answer(self, answer: str) -> None:
        question = self.questions[self.index]
        self.answers[question.name] = answer
        self.index += 1
        if self.index < len(self.questions):
            self._show_current()
            return
        self._finish(dict(self.answers))

    def _finish(self, answers: dict[str, str] | None) -> None:
        if self.completed:
            return
        self.completed = True
        options_widget = self.query_one(".inline-prompt-options", OptionList)
        input_widget = self.query_one(".inline-prompt-input", Input)
        options_widget.disabled = True
        options_widget.display = False
        input_widget.disabled = True
        input_widget.display = False
        title = self.query_one(".inline-prompt-title", Static)
        message = self.query_one(".inline-prompt-message", Static)
        help_widget = self.query_one(".inline-prompt-help", Static)
        if answers is None:
            title.update("Question cancelled")
            message.update("No answer provided.")
            help_widget.update("")
        else:
            title.update("✓ Answer submitted")
            message.update("\n".join(f"{name}: {value}" for name, value in answers.items()))
            help_widget.update("")
        self.on_complete(answers)

    def action_cancel(self) -> None:
        self._finish(None)


class InlinePermissionPrompt(Vertical):
    """Render a three-way permission decision in the conversation flow."""

    DEFAULT_CSS = _INLINE_PROMPT_CSS
    BINDINGS = [
        Binding("1,y", "allow", "Allow once", show=False, priority=True),
        Binding("2,a", "allow_always", "Always allow", show=False, priority=True),
        Binding("3,n,escape", "deny", "Deny", show=False, priority=True),
    ]

    _RESPONSES = (
        PermissionResponse.ALLOW,
        PermissionResponse.ALLOW_ALWAYS,
        PermissionResponse.DENY,
    )

    def __init__(
        self,
        request: PermissionRequest,
        on_complete: Callable[[PermissionResponse], None],
    ) -> None:
        super().__init__(classes="inline-permission-prompt")
        self.request = request
        self.on_complete = on_complete
        self.completed = False

    def compose(self) -> ComposeResult:
        yield Static("Simple Code needs permission", classes="inline-prompt-title", markup=False)
        yield Static(
            self._permission_summary(),
            classes="inline-prompt-message",
            markup=False,
        )
        yield OptionList(
            "1. Yes",
            "2. Always allow this exact request",
            "3. No",
            classes="inline-prompt-options",
            compact=True,
            markup=False,
        )
        yield Static(
            "Y/1 allow · A/2 always allow exact request · N/3/Esc deny",
            classes="inline-prompt-help",
            markup=False,
        )

    def on_mount(self) -> None:
        options = self.query_one(".inline-prompt-options", OptionList)
        options.highlighted = 0
        options.focus()

    def _permission_summary(self) -> str:
        arguments = self.request.arguments
        tool_name = self.request.tool_name
        lines = [tool_name]
        affected = arguments.get("file_path") or arguments.get("path")
        if tool_name == "Bash":
            command = str(arguments.get("command", ""))
            referenced_paths = [
                token.strip("\"'(),")
                for token in command.split()
                if "/" in token or "\\" in token
            ]
            impact = referenced_paths[:5] or [self.request.work_dir or "current working directory"]
            lines.extend(
                [
                    "",
                    "Command:",
                    f"  {command}",
                    "",
                    "Potential impact:",
                    *(f"  {item}" for item in impact),
                    "File changes: determined after execution",
                ]
            )
            risk = self.request.reason or "Shell command may modify workspace or external state"
        elif tool_name == "WriteFile":
            content = str(arguments.get("content", ""))
            lines.extend(
                [
                    "",
                    "Write target:",
                    f"  {affected or '(unknown)'}",
                    (
                        f"Content: {len(content.splitlines())} lines · "
                        f"{len(content.encode('utf-8'))} bytes"
                    ),
                ]
            )
            risk = self.request.reason or "Creates or overwrites a file"
        elif tool_name == "EditFile":
            old = str(arguments.get("old_string", ""))
            new = str(arguments.get("new_string", ""))
            lines.extend(
                [
                    "",
                    "Edit target:",
                    f"  {affected or '(unknown)'}",
                    f"Change: {len(old)} chars → {len(new)} chars",
                ]
            )
            risk = self.request.reason or "Modifies an existing file"
        else:
            rendered = json.dumps(arguments, ensure_ascii=False, indent=2)
            lines.extend(["", "Arguments:", rendered])
            risk = self.request.reason or "Tool requires confirmation"
        if self.request.work_dir:
            lines.extend(["", "Working directory:", f"  {self.request.work_dir}"])
        if affected and tool_name not in {"WriteFile", "EditFile"}:
            lines.extend(["", "Affected path:", f"  {affected}"])
        lines.extend(
            [
                "",
                "Risk:",
                f"  {risk}",
                f"Approval fingerprint: {self.request.argument_hash or '(legacy request)'}",
            ]
        )
        return "\n".join(lines)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list is not self.query_one(".inline-prompt-options", OptionList):
            return
        event.stop()
        self._finish(self._RESPONSES[event.option_index])

    def _finish(self, response: PermissionResponse) -> None:
        if self.completed:
            return
        self.completed = True
        options = self.query_one(".inline-prompt-options", OptionList)
        options.disabled = True
        options.display = False
        labels = {
            PermissionResponse.ALLOW: "Allowed once",
            PermissionResponse.ALLOW_ALWAYS: "Always allowed",
            PermissionResponse.DENY: "Denied",
        }
        self.query_one(".inline-prompt-title", Static).update(f"✓ Permission: {labels[response]}")
        self.query_one(".inline-prompt-help", Static).update("")
        self.on_complete(response)

    def action_deny(self) -> None:
        self._finish(PermissionResponse.DENY)

    def action_allow(self) -> None:
        self._finish(PermissionResponse.ALLOW)

    def action_allow_always(self) -> None:
        self._finish(PermissionResponse.ALLOW_ALWAYS)


__all__ = ["InlinePermissionPrompt", "InlineQuestionPrompt"]
