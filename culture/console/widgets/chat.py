"""ChatPanel widget — message log and user input field."""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static


class ChatPanel(Widget):
    """Center panel showing the message log and an input field.

    Layout (top to bottom):
      - header bar  (channel name)
      - RichLog     (scrollable message history)
      - Input       (user text entry)

    Public API
    ----------
    add_message(timestamp, icon, nick, text)
        Append a formatted chat message to the log.
    set_channel(channel)
        Update the header and input placeholder for a new channel.
    set_content(title, lines)
        Replace the log with arbitrary content lines (for overview / status
        views).
    clear_log()
        Clear all messages from the log.
    """

    _CHAT_LOG_ID = "#chat-log"

    DEFAULT_CSS = """
    ChatPanel {
        width: 1fr;
        height: 1fr;
    }
    ChatPanel #chat-header {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        text-style: bold;
    }
    ChatPanel #chat-log {
        width: 1fr;
        height: 1fr;
    }
    ChatPanel #chat-input {
        height: 3;
        dock: bottom;
    }
    """

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------

    class UserInput(Message):
        """Posted when the user submits a line of input."""

        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("", id="chat-header")
        with Vertical():
            yield RichLog(id="chat-log", markup=True, wrap=True, highlight=False)
        yield Input(placeholder="Type a message or /command…", id="chat-input")

    def on_mount(self) -> None:
        self._channel = ""

    # ------------------------------------------------------------------
    # Input handler
    # ------------------------------------------------------------------

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Forward the submitted text as a UserInput message then clear."""
        value = event.value.strip()
        if value:
            self.post_message(self.UserInput(value))
        event.input.clear()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_message(self, timestamp: float, icon: str, nick: str, text: str) -> None:
        """Append a formatted message to the chat log.

        Parameters
        ----------
        timestamp:
            Unix timestamp of the message.
        icon:
            Entity icon (emoji or single character).
        nick:
            Sender nick.
        text:
            Message body.
        """
        log: RichLog = self.query_one(self._CHAT_LOG_ID, RichLog)
        ts = datetime.fromtimestamp(timestamp).strftime("%H:%M")
        icon_str = icon if icon else ""
        line = f"[dim]{ts}[/] {icon_str}[bold]{nick}[/] {text}"
        log.write(line)

    def set_channel(self, channel: str) -> None:
        """Update the header and input placeholder for ``channel``."""
        self._channel = channel
        header: Static = self.query_one("#chat-header", Static)
        header.update(f"  {channel}" if channel else "")
        chat_input: Input = self.query_one("#chat-input", Input)
        prompt = f"{channel}>" if channel else ">"
        chat_input.placeholder = f"{prompt} Type a message or /command…"

    def set_content(self, title: str, lines: list[str]) -> None:
        """Replace the log with arbitrary content (for overview / status views).

        Parameters
        ----------
        title:
            Displayed in the header bar.
        lines:
            List of Rich markup strings to display in the log.
        """
        header: Static = self.query_one("#chat-header", Static)
        header.update(f"  {title}")
        log: RichLog = self.query_one(self._CHAT_LOG_ID, RichLog)
        log.clear()
        for line in lines:
            log.write(line)

    def clear_log(self) -> None:
        """Clear all messages from the log."""
        log: RichLog = self.query_one(self._CHAT_LOG_ID, RichLog)
        log.clear()
