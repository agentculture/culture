"""ConsoleApp — main Textual TUI application for the culture console."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.widgets import Footer, Header

from culture.console.client import ConsoleIRCClient
from culture.console.commands import CommandType, parse_command
from culture.console.widgets.chat import ChatPanel
from culture.console.widgets.info_panel import InfoPanel
from culture.console.widgets.sidebar import ChannelItem, EntityItem, Sidebar

logger = logging.getLogger(__name__)

BUFFER_INTERVAL = 10.0  # seconds between UI refreshes


class ConsoleApp(App):
    """Main TUI application — wires IRC client, sidebar, chat, and info panel."""

    TITLE = "culture console"
    _CHAT_INPUT_ID = "#chat-input"

    BINDINGS = [
        Binding("ctrl+o", "show_overview", "Overview", show=True),
        Binding("ctrl+s", "show_status", "Status", show=True),
        Binding("escape", "back_to_chat", "Chat", show=True),
        Binding("ctrl+q", "quit_app", "Quit", show=True),
        Binding("tab", "next_channel", "Next channel", show=False),
        Binding("shift+tab", "prev_channel", "Prev channel", show=False),
    ]

    DEFAULT_CSS = """
    ConsoleApp {
        layout: vertical;
    }
    #main-area {
        width: 1fr;
        height: 1fr;
    }
    """

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self, irc_client: ConsoleIRCClient, server_name: str) -> None:
        super().__init__()
        self._client = irc_client
        self._server_name = server_name

        # Current state
        self._current_channel: str = ""
        self._channel_list: list[str] = []
        self._current_view: str = "chat"  # "chat" | "overview" | "status"

        self._buffer_task: asyncio.Task | None = None
        self._background_tasks: set[asyncio.Task] = set()

        # Dispatch table for command execution
        self._command_handlers: dict[CommandType, Any] = {
            CommandType.CHAT: self._handle_chat,
            CommandType.JOIN: self._handle_join,
            CommandType.PART: self._handle_part,
            CommandType.CHANNELS: self._handle_channels,
            CommandType.WHO: self._handle_who,
            CommandType.READ: self._handle_read,
            CommandType.SEND: self._handle_send,
            CommandType.OVERVIEW: self._handle_overview,
            CommandType.STATUS: self._handle_status,
            CommandType.AGENTS: self._handle_agents,
            CommandType.ICON: self._handle_icon,
            CommandType.TOPIC: self._handle_topic,
            CommandType.KICK: self._handle_kick,
            CommandType.INVITE: self._handle_invite,
            CommandType.SERVER: self._handle_server,
            CommandType.QUIT: self._handle_quit,
        }

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main-area"):
            yield Sidebar()
            yield ChatPanel()
            yield InfoPanel()
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def on_mount(self) -> None:
        """Set sub-title and kick off the buffer-drain loop."""
        self.sub_title = f"{self._client.nick}@{self._server_name}"
        self._buffer_task = asyncio.create_task(self._buffer_loop())

        # Populate sidebar with any channels already joined at startup
        self._sync_sidebar()

    # ------------------------------------------------------------------
    # Buffer loop
    # ------------------------------------------------------------------

    async def _buffer_loop(self) -> None:
        """Periodically drain the IRC client's message buffer."""
        while True:
            try:
                await asyncio.sleep(BUFFER_INTERVAL)
                self._flush_messages()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Error in _buffer_loop")

    def _flush_messages(self) -> None:
        """Drain buffered IRC messages and add them to the chat panel."""
        messages = self._client.drain_messages()
        if self._current_view != "chat" or not messages:
            return
        chat: ChatPanel = self.query_one(ChatPanel)
        for msg in messages:
            if msg.channel == self._current_channel:
                chat.add_message(
                    timestamp=msg.timestamp,
                    icon="",
                    nick=msg.nick,
                    text=msg.text,
                )

    # ------------------------------------------------------------------
    # Input handler
    # ------------------------------------------------------------------

    def on_chat_panel_user_input(self, event: ChatPanel.UserInput) -> None:
        """Handle user input submitted from the ChatPanel."""
        cmd = parse_command(event.value)
        task = asyncio.create_task(self._execute_command(cmd))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    async def _execute_command(self, cmd) -> None:  # noqa: ANN001
        """Dispatch a ParsedCommand to the appropriate handler."""
        handler = self._command_handlers.get(cmd.type)
        if handler:
            await handler(cmd)
        elif cmd.type in (CommandType.START, CommandType.STOP, CommandType.RESTART):
            await self._handle_agent_management(cmd)
        elif cmd.type == CommandType.UNKNOWN:
            chat: ChatPanel = self.query_one(ChatPanel)
            chat.add_message(time.time(), "", "system", f"[red]Unknown command: {cmd.text}[/]")

    # ------------------------------------------------------------------
    # Command handlers
    # ------------------------------------------------------------------

    async def _handle_chat(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        if not cmd.text:
            return
        if not self._current_channel:
            chat.add_message(
                time.time(), "", "system", "[red]Not in a channel — use /join #channel[/]"
            )
            return
        await self._client.send_privmsg(self._current_channel, cmd.text)
        chat.add_message(time.time(), "", self._client.nick, cmd.text)

    async def _handle_join(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        if not cmd.args:
            chat.add_message(time.time(), "", "system", "[red]Usage: /join #channel[/]")
            return
        channel = cmd.args[0]
        await self._client.join(channel)
        self._current_channel = channel
        self._sync_sidebar()
        chat.set_channel(channel)
        chat.add_message(time.time(), "", "system", f"Joined [bold]{channel}[/]")

    async def _handle_part(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        channel = cmd.args[0] if cmd.args else self._current_channel
        if not channel:
            chat.add_message(time.time(), "", "system", "[red]Not in a channel[/]")
            return
        await self._client.part(channel)
        self._sync_sidebar()
        if self._current_channel == channel:
            remaining = sorted(self._client.joined_channels)
            self._current_channel = remaining[0] if remaining else ""
            chat.set_channel(self._current_channel)
        chat.add_message(time.time(), "", "system", f"Parted [bold]{channel}[/]")

    async def _handle_channels(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        channels = await self._client.list_channels()
        lines = ["[bold $warning]CHANNELS ON SERVER[/]", ""]
        for ch in channels:
            lines.append(f"  {ch}")
        if not channels:
            lines.append("  [dim](none)[/]")
        chat.set_content("Channel List", lines)

    async def _handle_who(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        target = cmd.args[0] if cmd.args else self._current_channel
        if not target:
            chat.add_message(
                time.time(), "", "system", "[red]Usage: /who #channel or /who <nick>[/]"
            )
            return
        entries = await self._client.who(target)
        lines = [f"[bold $warning]WHO {target}[/]", ""]
        for e in entries:
            flags = e.get("flags", "")
            nick = e.get("nick", "")
            realname = e.get("realname", "")
            lines.append(f"  [bold]{nick}[/] {flags}  [dim]{realname}[/]")
        if not entries:
            lines.append("  [dim](no results)[/]")
        chat.set_content(f"WHO {target}", lines)

    async def _handle_read(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        channel = cmd.args[0] if cmd.args else self._current_channel
        if not channel:
            chat.add_message(time.time(), "", "system", "[red]Usage: /read #channel[/]")
            return
        limit = 50
        for i, arg in enumerate(cmd.args[1:], start=1):
            if arg == "-n" and i + 1 <= len(cmd.args) - 1:
                try:
                    limit = int(cmd.args[i + 1])
                except ValueError:
                    pass
                break
        entries = await self._client.history(channel, limit=limit)
        chat.clear_log()
        for e in entries:
            try:
                ts = float(e.get("timestamp", 0))
            except (ValueError, TypeError):
                ts = time.time()
            chat.add_message(ts, "", e.get("nick", ""), e.get("text", ""))
        if not entries:
            chat.add_message(time.time(), "", "system", f"[dim]No history for {channel}[/]")

    async def _handle_send(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        if len(cmd.args) < 1:
            chat.add_message(time.time(), "", "system", "[red]Usage: /send <target> <text>[/]")
            return
        target = cmd.args[0]
        text = cmd.text
        if not text:
            chat.add_message(time.time(), "", "system", "[red]No message text provided[/]")
            return
        await self._client.send_privmsg(target, text)
        chat.add_message(time.time(), "", self._client.nick, f"→ {target}: {text}")

    async def _handle_overview(self, cmd) -> None:  # noqa: ANN001
        await self.action_show_overview()

    async def _handle_status(self, cmd) -> None:  # noqa: ANN001
        agent = cmd.args[0] if cmd.args else None
        await self._show_status(agent=agent)

    async def _handle_agents(self, cmd) -> None:  # noqa: ANN001
        await self._show_agents()

    async def _handle_icon(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        if not cmd.args:
            chat.add_message(time.time(), "", "system", "[red]Usage: /icon <emoji>[/]")
            return
        icon = cmd.args[-1]
        await self._client.send_raw(f"ICON {icon}")
        chat.add_message(time.time(), "", "system", f"Icon set to {icon}")

    async def _handle_topic(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        channel = cmd.args[0] if cmd.args else self._current_channel
        if not channel or not cmd.text:
            chat.add_message(time.time(), "", "system", "[red]Usage: /topic #channel <text>[/]")
            return
        await self._client.send_raw(f"TOPIC {channel} :{cmd.text}")

    async def _handle_kick(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        if len(cmd.args) < 2:
            chat.add_message(time.time(), "", "system", "[red]Usage: /kick #channel <nick>[/]")
            return
        await self._client.send_raw(f"KICK {cmd.args[0]} {cmd.args[1]}")

    async def _handle_invite(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        if len(cmd.args) < 2:
            chat.add_message(time.time(), "", "system", "[red]Usage: /invite <nick> #channel[/]")
            return
        await self._client.send_raw(f"INVITE {cmd.args[0]} {cmd.args[1]}")

    async def _handle_agent_management(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        verb = cmd.type.name.lower()
        chat.add_message(
            time.time(),
            "",
            "system",
            f"[yellow]Agent management ({verb}) requires the CLI: "
            f"[bold]culture {verb} <agent>[/][/]",
        )

    async def _handle_server(self, cmd) -> None:  # noqa: ANN001
        chat: ChatPanel = self.query_one(ChatPanel)
        target = cmd.args[0] if cmd.args else ""
        chat.add_message(
            time.time(),
            "",
            "system",
            f"[yellow]To switch servers, restart the console: "
            f"[bold]culture console {target}[/][/]",
        )

    async def _handle_quit(self, cmd) -> None:  # noqa: ANN001
        await self.action_quit_app()

    # ------------------------------------------------------------------
    # View actions
    # ------------------------------------------------------------------

    async def action_show_overview(self) -> None:
        """Switch to the overview view showing mesh stats."""
        self._current_view = "overview"
        chat: ChatPanel = self.query_one(ChatPanel)
        info: InfoPanel = self.query_one(InfoPanel)

        # Build overview content
        channels = sorted(self._client.joined_channels)
        lines = [
            "[bold $warning]MESH OVERVIEW[/]",
            "",
            f"  Server:   [bold]{self._server_name}[/]",
            f"  Nick:     [bold]{self._client.nick}[/]",
            "",
            f"[bold $warning]JOINED CHANNELS ({len(channels)})[/]",
        ]
        for ch in channels:
            lines.append(f"  {ch}")
        if not channels:
            lines.append("  [dim](none)[/]")

        chat.set_content("Overview", lines)

        # Show aggregate stats in info panel
        info.set_mesh_stats(
            {
                "servers": 1,
                "channels": len(channels),
            }
        )

        # Hide input — not meaningful in overview mode
        try:
            input_widget = self.query_one(self._CHAT_INPUT_ID)
            input_widget.display = False
        except Exception:
            pass

    async def action_show_status(self) -> None:
        """Bound to Ctrl+S — show status for current channel / server."""
        await self._show_status()

    async def _show_status(self, agent: str | None = None) -> None:
        """Show server or agent status in the chat panel."""
        self._current_view = "status"
        chat: ChatPanel = self.query_one(ChatPanel)
        info: InfoPanel = self.query_one(InfoPanel)

        if agent:
            # Show agent-specific status via WHO
            entries = await self._client.who(agent)
            if entries:
                e = entries[0]
                lines = [
                    f"[bold $warning]AGENT STATUS: {agent}[/]",
                    "",
                    f"  Nick:     [bold]{e.get('nick', agent)}[/]",
                    f"  Host:     {e.get('host', '?')}",
                    f"  Server:   {e.get('server', '?')}",
                    f"  Flags:    {e.get('flags', '')}",
                    f"  Realname: {e.get('realname', '')}",
                ]
            else:
                lines = [f"[bold $warning]AGENT STATUS: {agent}[/]", "", "  [dim](not found)[/]"]
            chat.set_content(f"Status: {agent}", lines)
            info.set_agent_actions(agent)
        else:
            # Show server/channel status
            channel = self._current_channel
            if channel:
                entries = await self._client.who(channel)
                nicks = [e.get("nick", "") for e in entries]
                lines = [
                    f"[bold $warning]STATUS: {channel}[/]",
                    "",
                    f"  Server:  [bold]{self._server_name}[/]",
                    f"  Members: [bold]{len(nicks)}[/]",
                    "",
                    "[bold $warning]MEMBERS[/]",
                ]
                for nick in sorted(nicks):
                    lines.append(f"  {nick}")
                chat.set_content(f"Status: {channel}", lines)
                info.set_channel_info({"name": channel, "members": nicks})
            else:
                lines = [
                    "[bold $warning]SERVER STATUS[/]",
                    "",
                    f"  Server: [bold]{self._server_name}[/]",
                    f"  Nick:   [bold]{self._client.nick}[/]",
                    f"  Channels joined: [bold]{len(self._client.joined_channels)}[/]",
                ]
                chat.set_content("Server Status", lines)

    async def _show_agents(self) -> None:
        """List all visible agents across joined channels."""
        chat: ChatPanel = self.query_one(ChatPanel)
        sidebar: Sidebar = self.query_one(Sidebar)

        # Collect agents from WHO queries on each joined channel
        all_agents: dict[str, dict] = {}
        for channel in sorted(self._client.joined_channels):
            entries = await self._client.who(channel)
            for e in entries:
                nick = e.get("nick", "")
                if nick and nick not in all_agents:
                    all_agents[nick] = e

        lines = [
            f"[bold $warning]AGENTS ({len(all_agents)})[/]",
            "",
        ]
        for nick in sorted(all_agents):
            e = all_agents[nick]
            flags = e.get("flags", "")
            server = e.get("server", "")
            lines.append(f"  [bold]{nick}[/]  [dim]{flags}  {server}[/]")
        if not all_agents:
            lines.append("  [dim](no agents visible)[/]")

        chat.set_content("Agents", lines)

        # Update sidebar entity roster
        entity_items = [
            EntityItem(nick=nick, entity_type="agent", online=True) for nick in sorted(all_agents)
        ]
        sidebar.entities = entity_items

    def action_back_to_chat(self) -> None:
        """Return to the normal chat view."""
        if self._current_view == "chat":
            return
        self._current_view = "chat"
        chat: ChatPanel = self.query_one(ChatPanel)
        chat.set_channel(self._current_channel)

        # Re-show input
        try:
            input_widget = self.query_one(self._CHAT_INPUT_ID)
            input_widget.display = True
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Channel cycling
    # ------------------------------------------------------------------

    def action_next_channel(self) -> None:
        """Switch to the next channel in the list."""
        self._cycle_channel(+1)

    def action_prev_channel(self) -> None:
        """Switch to the previous channel in the list."""
        self._cycle_channel(-1)

    def _cycle_channel(self, direction: int) -> None:
        """Cycle through joined channels by direction (+1 or -1)."""
        channels = sorted(self._client.joined_channels)
        if not channels:
            return
        if self._current_channel not in channels:
            self._current_channel = channels[0]
        else:
            idx = channels.index(self._current_channel)
            idx = (idx + direction) % len(channels)
            self._current_channel = channels[idx]

        chat: ChatPanel = self.query_one(ChatPanel)
        sidebar: Sidebar = self.query_one(Sidebar)
        chat.set_channel(self._current_channel)
        sidebar.active_channel = self._current_channel

    # ------------------------------------------------------------------
    # Quit
    # ------------------------------------------------------------------

    async def action_quit_app(self) -> None:
        """Disconnect the IRC client and exit the app."""
        if self._buffer_task:
            self._buffer_task.cancel()
            await asyncio.gather(self._buffer_task, return_exceptions=True)
            self._buffer_task = None

        if self._client.connected:
            try:
                await self._client.disconnect()
            except Exception:
                logger.exception("Error disconnecting IRC client during quit")

        self.exit()

    # ------------------------------------------------------------------
    # Sidebar message handlers
    # ------------------------------------------------------------------

    def on_sidebar_channel_selected(self, event: Sidebar.ChannelSelected) -> None:
        """Switch to the selected channel when user clicks sidebar."""
        self._current_channel = event.channel
        self._current_view = "chat"
        chat: ChatPanel = self.query_one(ChatPanel)
        sidebar: Sidebar = self.query_one(Sidebar)
        chat.set_channel(self._current_channel)
        sidebar.active_channel = self._current_channel

        # Re-show input if hidden
        try:
            input_widget = self.query_one(self._CHAT_INPUT_ID)
            input_widget.display = True
        except Exception:
            pass

    def on_sidebar_entity_selected(self, event: Sidebar.EntitySelected) -> None:
        """Show agent detail when user clicks an entity in the sidebar."""
        task = asyncio.create_task(self._show_status(agent=event.nick))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _sync_sidebar(self) -> None:
        """Sync the sidebar channel list from the client's joined_channels."""
        sidebar: Sidebar = self.query_one(Sidebar)
        channels = sorted(self._client.joined_channels)
        self._channel_list = channels
        sidebar.channels = [ChannelItem(name=ch) for ch in channels]
        if channels and not self._current_channel:
            self._current_channel = channels[0]
        sidebar.active_channel = self._current_channel
