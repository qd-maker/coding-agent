"""Textual completion popup for slash commands."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import OptionList
from textual.widgets.option_list import Option


class CompletionPopup(Vertical):
    """Small selectable list shown above the input composer."""

    DEFAULT_CSS = """
    CompletionPopup {
        display: none;
        width: 48;
        max-height: 12;
        dock: bottom;
        margin: 0 0 3 2;
        border: round #716887;
        background: #211f29;
        layer: completion;
    }

    CompletionPopup OptionList {
        width: 100%;
        height: auto;
        max-height: 10;
        background: #211f29;
    }
    """

    class Selected(Message):
        """Posted when a completion is selected."""

        def __init__(self, value: str) -> None:
            self.value = value
            super().__init__()

    def compose(self) -> ComposeResult:
        yield OptionList(id="command-completions")

    @property
    def is_visible(self) -> bool:
        return bool(self.display)

    def show(self, items: list[str]) -> None:
        option_list = self.query_one(OptionList)
        option_list.clear_options()
        option_list.add_options(Option(item, id=item) for item in items)
        self.display = bool(items)
        if items:
            option_list.highlighted = 0
            option_list.focus()

    def hide(self) -> None:
        self.display = False

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        value = str(event.option.id or event.option.prompt)
        self.hide()
        self.post_message(self.Selected(value))


__all__ = ["CompletionPopup"]
