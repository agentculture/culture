"""ChatPanel widget — message log and user input field."""

from __future__ import annotations

import time
from datetime import datetime

from rich.markdown import Markdown
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, RichLog, Static


def build_message_header(timestamp: float, icon: str, nick: str) -> Text:
    """Return the ``[ts] icon nick:`` header rendered as a Rich ``Text``.

    Pulled out of ``ChatPanel.add_message`` so it can be unit-tested without
    a running Textual app. Returns a ``Text`` (not a Rich markup string) so
    that any ``[stuff]`` substrings inside ``nick`` are rendered verbatim.
    """
    ts = datetime.fromtimestamp(timestamp).strftime("%H:%M")
    header = Text()
    header.append(ts, style="dim")
    header.append(" ")
    if icon:
        header.append(f"{icon} ")
    header.append(nick, style="bold")
    header.append(":")
    return header


def build_system_message_line(timestamp: float, text: str) -> str:
    """Return the Rich-markup line ``ChatPanel.add_system_message`` writes.

    Pulled out so the formatting can be unit-tested directly. Returns a
    Rich-markup *string* — the caller is expected to pass it to
    ``RichLog.write`` so Rich parses ``[red]…[/]`` / ``[bold]…[/]`` tags
    inside ``text`` as styling.
    """
    ts = datetime.fromtimestamp(timestamp).strftime("%H:%M")
    return f"[dim]{ts}[/] [bold]system[/] {text}"


class ChatInput(Input):
    """Input with Alt+Arrow word-jump and Alt+Backspace word-delete."""

    BINDINGS = [
        Binding("alt+left", "cursor_left_word", "Word left", show=False),
        Binding("alt+right", "cursor_right_word", "Word right", show=False),
        Binding(
            "alt+shift+left",
            "cursor_left_word(True)",
            "Select word left",
            show=False,
        ),
        Binding(
            "alt+shift+right",
            "cursor_right_word(True)",
            "Select word right",
            show=False,
        ),
        Binding("alt+backspace", "delete_left_word", "Delete word", show=False),
    ]


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
        yield ChatInput(placeholder="Type a message or /command…", id="chat-input")

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
        """Append a chat message rendered as CommonMark markdown.

        Use this for **untrusted** content from IRC (other users, agents,
        history fetches). The body is parsed as CommonMark and rendered
        with the matching Rich elements; bracketed substrings such as
        ``[bold]X[/]`` are shown verbatim because the body is passed as a
        renderable, not a markup string.

        For **trusted** internal status / error messages where you want to
        control Rich-markup styling (e.g. ``[red]error[/]``), use
        :py:meth:`add_system_message` instead.

        Parameters
        ----------
        timestamp:
            Unix timestamp of the message.
        icon:
            Entity icon (emoji or single character).
        nick:
            Sender nick.
        text:
            Message body (rendered as markdown).
        """
        log: RichLog = self.query_one(self._CHAT_LOG_ID, RichLog)
        log.write(build_message_header(timestamp, icon, nick))
        log.write(Markdown(text))

    def add_system_message(self, text: str) -> None:
        """Append a trusted system / status line interpreted as Rich markup.

        Counterpart to :py:meth:`add_message`. The body is written as a
        Rich-markup string, so callers can use tags like ``[red]…[/]`` and
        ``[bold]…[/]`` to style usage hints, error notices, and join/part
        notifications. The header is ``[ts] system``; the timestamp uses
        ``time.time()``.

        Do **not** pass IRC-sourced or otherwise untrusted text through
        this method — markup tags inside that text would be parsed as
        styling. Use :py:meth:`add_message` for that.
        """
        log: RichLog = self.query_one(self._CHAT_LOG_ID, RichLog)
        log.write(build_system_message_line(time.time(), text))

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
