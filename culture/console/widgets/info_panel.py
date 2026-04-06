"""InfoPanel widget — context-sensitive right panel."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Static

# ---------------------------------------------------------------------------
# InfoPanel
# ---------------------------------------------------------------------------


class InfoPanel(Widget):
    """Right info panel displaying context-sensitive information.

    The panel always shows keybinding hints at the bottom.  The main body
    is replaced by calling one of the public setter methods:

    Public API
    ----------
    set_channel_info(info)
        Show channel topic, members, and message count.
    set_agent_actions(nick)
        Show restart / stop / whisper quick-actions for ``nick``.
    set_mesh_stats(stats)
        Show aggregate mesh statistics.
    """

    DEFAULT_CSS = """
    InfoPanel {
        width: 24;
        border-left: solid $accent;
        overflow-y: auto;
    }
    InfoPanel .section-header {
        width: 1fr;
        height: 1;
        padding: 0 1;
        color: $warning;
        text-style: bold;
    }
    InfoPanel .info-row {
        width: 1fr;
        height: 1;
        padding: 0 2;
    }
    InfoPanel #info-body {
        width: 1fr;
        height: 1fr;
    }
    InfoPanel #keybindings {
        width: 1fr;
        dock: bottom;
        padding: 0 1;
        border-top: solid $accent;
        color: $text-muted;
    }
    """

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="info-body"):
            yield Static("", id="info-content")
        yield Static(self._keybinding_text(), id="keybindings", markup=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_channel_info(self, info: dict) -> None:
        """Display channel information.

        Expected keys in ``info`` (all optional):
          - ``name``         channel name
          - ``topic``        channel topic string
          - ``members``      list of nick strings
          - ``message_count`` integer
        """
        lines: list[str] = []

        name = info.get("name", "")
        if name:
            lines.append("[bold $warning]CHANNEL[/]")
            lines.append(f"  {name}")

        topic = info.get("topic", "")
        if topic:
            lines.append("")
            lines.append("[bold $warning]TOPIC[/]")
            lines.append(f"  {topic}")

        members: list[str] = info.get("members", [])
        if members:
            lines.append("")
            lines.append(f"[bold $warning]MEMBERS ({len(members)})[/]")
            for nick in members:
                lines.append(f"  {nick}")

        count = info.get("message_count")
        if count is not None:
            lines.append("")
            lines.append(f"[dim]Messages: {count}[/]")

        self._update_content(lines)

    def set_agent_actions(self, nick: str) -> None:
        """Display quick-action menu for agent ``nick``."""
        lines: list[str] = [
            "[bold $warning]AGENT[/]",
            f"  {nick}",
            "",
            "[bold $warning]ACTIONS[/]",
            "  [dim]/restart[/]  Restart agent",
            "  [dim]/stop[/]     Stop agent",
            "  [dim]/whisper[/]  Send DM",
            "",
            "[dim]Select then press Enter[/]",
        ]
        self._update_content(lines)

    def set_mesh_stats(self, stats: dict) -> None:
        """Display aggregate mesh statistics.

        Expected keys in ``stats`` (all optional):
          - ``servers``      int — server count
          - ``agents``       int — total agent count
          - ``channels``     int — total channel count
          - ``messages``     int — total messages (or msgs/hr)
          - ``federation``   int — federation peer count
        """
        lines: list[str] = ["[bold $warning]MESH STATS[/]", ""]

        field_map = [
            ("servers", "Servers"),
            ("agents", "Agents"),
            ("channels", "Channels"),
            ("messages", "Msgs/hr"),
            ("federation", "Peers"),
        ]
        for key, label in field_map:
            value = stats.get(key)
            if value is not None:
                lines.append(f"  {label}: [bold]{value}[/]")

        self._update_content(lines)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_content(self, lines: list[str]) -> None:
        """Replace the main content area with the given markup lines."""
        content: Static = self.query_one("#info-content", Static)
        text = "\n".join(lines)
        content.update(text)

    def _keybinding_text(self) -> str:
        """Return the keybinding hint text as Rich markup."""
        hints = [
            "[bold $warning]KEYS[/]",
            "  [dim]Tab[/]    Next channel",
            "  [dim]Ctrl+O[/] Overview",
            "  [dim]Ctrl+S[/] Status",
            "  [dim]Esc[/]    Back to chat",
            "  [dim]Ctrl+Q[/] Quit",
        ]
        return "\n".join(hints)
