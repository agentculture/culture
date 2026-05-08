"""ConsoleIRCClient — persistent IRC client for the console TUI.

Combines the persistent connection pattern of IRCTransport with the query
methods of IRCObserver. Designed for the console TUI: buffers incoming
PRIVMSG messages and provides async query methods for LIST, WHO, and HISTORY.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from culture.aio import maybe_await
from culture.protocol.message import Message

logger = logging.getLogger(__name__)

# Timeout for query operations (LIST, WHO, HISTORY)
QUERY_TIMEOUT = 10.0
# Timeout for registration (NICK + USER → 001)
REGISTER_TIMEOUT = 15.0


class ConsoleConnectionLost(ConnectionError):
    """Raised by ConsoleIRCClient when the underlying socket is broken mid-send."""


@dataclass
class ChatMessage:
    """A buffered chat message from a channel or DM."""

    channel: str
    nick: str
    text: str
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


class ConsoleIRCClient:
    """Async IRC client for the console TUI.

    Maintains a persistent connection, buffers incoming PRIVMSG messages,
    and provides query methods for channel listing, WHO, and history.

    Does **not** negotiate ``CAP REQ :message-tags``. The TUI renders plain
    body text for mesh events; IRCv3 tags carry structured payloads that the
    console has no use for. Agent harness transports (see
    ``culture/clients/shared/irc_transport.py``) do negotiate the cap.
    """

    def __init__(
        self,
        host: str,
        port: int,
        nick: str,
        mode: str = "HC",
        icon: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.nick = nick
        self.mode = mode
        self.icon = icon

        self.connected: bool = False
        self.joined_channels: set[str] = set()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None

        # Buffer for incoming PRIVMSG messages
        self._message_buffer: list[ChatMessage] = []

        # Pending futures for single-response queries (keyed by command string)
        self._pending: dict[str, asyncio.Future[Any]] = {}

        # Accumulation buffers for multi-line query responses
        # keyed by query key (e.g. "LIST", "WHO #chan", "HISTORY #chan")
        self._collect_buffers: dict[str, list[Any]] = {}

        # Dispatch table for IRC message handling
        self._msg_handlers: dict[str, Callable[[Message], Any]] = {
            "PING": self._on_ping,
            "001": self._on_welcome,
            "PRIVMSG": self._on_privmsg_msg,
            "322": self._on_rpl_list,
            "323": self._on_rpl_listend,
            "352": self._on_rpl_whoreply,
            "315": self._on_rpl_endofwho,
            "HISTORY": self._on_history,
            "HISTORYEND": self._on_historyend,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open TCP connection, register nick, set user mode, send ICON."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=REGISTER_TIMEOUT,
        )

        try:
            await self._send_raw(f"NICK {self.nick}")
            await self._send_raw(f"USER {self.nick} 0 * :{self.nick}")

            # Wait for RPL_WELCOME (001) before proceeding
            welcome_future: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
            self._pending["001"] = welcome_future

            # Start the read loop so the future can be resolved
            self._read_task = asyncio.create_task(self._read_loop())

            try:
                await asyncio.wait_for(welcome_future, timeout=REGISTER_TIMEOUT)
            except asyncio.TimeoutError as e:
                raise ConnectionError("Timed out waiting for server welcome (001)") from e

            # Set user mode
            if self.mode:
                await self._send_raw(f"MODE {self.nick} +{self.mode}")

            # Send ICON if provided
            if self.icon:
                await self._send_raw(f"ICON {self.icon}")
        except BaseException:
            # Any failure after open_connection: tear down the half-open state.
            await self._teardown_connection()
            raise

    async def _teardown_connection(self) -> None:
        """Close writer, cancel reader, clear pending futures. Idempotent."""
        self._pending.clear()
        if self._read_task:
            self._read_task.cancel()
            await asyncio.gather(self._read_task, return_exceptions=True)
            self._read_task = None
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except OSError:
                pass
        self._writer = None
        self._reader = None

    async def disconnect(self) -> None:
        """Send QUIT and close the connection."""
        self.connected = False

        if self._read_task:
            self._read_task.cancel()
            await asyncio.gather(self._read_task, return_exceptions=True)
            self._read_task = None

        if self._writer:
            try:
                await self._send_raw("QUIT :console done")
            except OSError:
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass
            self._writer = None
            self._reader = None

    async def join(self, channel: str) -> None:
        """Join a channel and track it in joined_channels."""
        if not self.connected or self._writer is None:
            return
        await self._send_raw(f"JOIN {channel}")
        self.joined_channels.add(channel)

    async def part(self, channel: str) -> None:
        """Part a channel and remove it from joined_channels."""
        if not self.connected or self._writer is None:
            return
        await self._send_raw(f"PART {channel}")
        self.joined_channels.discard(channel)

    async def send_privmsg(self, target: str, text: str) -> None:
        """Send a PRIVMSG to a channel or nick."""
        await self._send_raw(f"PRIVMSG {target} :{text}")

    async def send_raw(self, line: str) -> None:
        """Send a raw IRC line. Public interface for custom commands."""
        await self._send_raw(line)

    def drain_messages(self) -> list[ChatMessage]:
        """Return and clear all buffered incoming messages."""
        msgs = list(self._message_buffer)
        self._message_buffer.clear()
        return msgs

    async def list_channels(self) -> list[str]:
        """Send LIST, collect RPL_LIST (322) responses, wait for RPL_LISTEND (323).

        Returns a sorted list of channel names.
        """
        key = "LIST"
        pending_key = "323"
        self._collect_buffers[key] = []
        end_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending[pending_key] = end_future

        try:
            await self._send_raw("LIST")
        except ConsoleConnectionLost:
            self._pending.pop(pending_key, None)
            self._collect_buffers.pop(key, None)
            raise

        try:
            await asyncio.wait_for(end_future, timeout=QUERY_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        finally:
            self._pending.pop(pending_key, None)

        channels = self._collect_buffers.pop(key, [])
        return sorted(channels)

    async def who(self, target: str) -> list[dict]:
        """Send WHO <target>, collect RPL_WHOREPLY (352) responses, wait for RPL_ENDOFWHO (315).

        Returns a list of dicts with nick, user, host, server, flags, realname.
        """
        key = f"WHO {target}"
        pending_key = f"315:{target}"
        self._collect_buffers[key] = []
        end_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending[pending_key] = end_future

        try:
            await self._send_raw(f"WHO {target}")
        except ConsoleConnectionLost:
            self._pending.pop(pending_key, None)
            self._collect_buffers.pop(key, None)
            raise

        try:
            await asyncio.wait_for(end_future, timeout=QUERY_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        finally:
            self._pending.pop(pending_key, None)

        entries = self._collect_buffers.pop(key, [])
        return entries

    async def history(self, channel: str, limit: int = 50) -> list[dict]:
        """Send HISTORY RECENT <channel> <limit>, collect HISTORY responses, wait for HISTORYEND.

        Returns a list of dicts with channel, nick, timestamp, text.
        """
        key = f"HISTORY {channel}"
        pending_key = f"HISTORYEND:{channel}"
        self._collect_buffers[key] = []
        end_future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._pending[pending_key] = end_future

        try:
            await self._send_raw(f"HISTORY RECENT {channel} {limit}")
        except ConsoleConnectionLost:
            self._pending.pop(pending_key, None)
            self._collect_buffers.pop(key, None)
            raise

        try:
            await asyncio.wait_for(end_future, timeout=QUERY_TIMEOUT)
        except asyncio.TimeoutError:
            pass
        finally:
            self._pending.pop(pending_key, None)

        entries = self._collect_buffers.pop(key, [])
        return entries

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_raw(self, line: str) -> None:
        """Write a raw IRC line to the socket."""
        if not self._writer:
            return
        try:
            self._writer.write(f"{line}\r\n".encode())
            await self._writer.drain()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as e:
            self.connected = False
            logger.warning("ConsoleIRCClient: send failed (%s)", e.__class__.__name__)
            raise ConsoleConnectionLost(str(e)) from e

    async def _read_loop(self) -> None:
        """Background task: read lines from socket and dispatch to _handle."""
        buf = ""
        try:
            while True:
                assert self._reader is not None
                data = await self._reader.read(4096)
                if not data:
                    break
                buf += data.decode("utf-8", errors="replace")
                buf = buf.replace("\r\n", "\n").replace("\r", "\n")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if line.strip():
                        msg = Message.parse(line)
                        await self._handle(msg)
        except asyncio.CancelledError:
            raise
        except OSError:
            logger.warning("ConsoleIRCClient: connection lost")
        finally:
            self.connected = False

    async def _handle(self, msg: Message) -> None:
        """Route a parsed IRC message to the appropriate handler."""
        handler = self._msg_handlers.get(msg.command)
        if handler:
            await maybe_await(handler(msg))

    # ------------------------------------------------------------------
    # IRC message handlers
    # ------------------------------------------------------------------

    def _on_welcome(self, msg: Message) -> None:
        """RPL_WELCOME (001) — mark connected and resolve the welcome future."""
        self.connected = True
        fut = self._pending.pop("001", None)
        if fut and not fut.done():
            fut.set_result(msg)

    def _on_privmsg_msg(self, msg: Message) -> None:
        """Buffer an incoming PRIVMSG message (dispatch wrapper)."""
        self._on_privmsg(msg)

    def _on_rpl_list(self, msg: Message) -> None:
        """RPL_LIST (322) — accumulate channel name."""
        if len(msg.params) >= 2:
            channel_name = msg.params[1]
            buf = self._collect_buffers.get("LIST")
            if buf is not None:
                buf.append(channel_name)

    def _on_rpl_listend(self, msg: Message) -> None:
        """RPL_LISTEND (323) — resolve the LIST future."""
        fut = self._pending.pop("323", None)
        if fut and not fut.done():
            fut.set_result(None)

    def _on_rpl_whoreply(self, msg: Message) -> None:
        """RPL_WHOREPLY (352) — accumulate WHO entry."""
        if len(msg.params) >= 6:
            entry = {
                "nick": msg.params[5],
                "user": msg.params[2],
                "host": msg.params[3],
                "server": msg.params[4],
                "flags": msg.params[6] if len(msg.params) > 6 else "",
                "realname": msg.params[7] if len(msg.params) > 7 else "",
            }
            target = msg.params[1]
            key = f"WHO {target}"
            buf = self._collect_buffers.get(key)
            if buf is not None:
                buf.append(entry)

    def _on_rpl_endofwho(self, msg: Message) -> None:
        """RPL_ENDOFWHO (315) — resolve the WHO future."""
        target = msg.params[1] if len(msg.params) >= 2 else ""
        fut_key = f"315:{target}"
        fut = self._pending.pop(fut_key, None)
        if fut and not fut.done():
            fut.set_result(None)

    def _on_history(self, msg: Message) -> None:
        """HISTORY response — accumulate history entry."""
        if len(msg.params) >= 4:
            channel = msg.params[0]
            entry = {
                "channel": channel,
                "nick": msg.params[1],
                "timestamp": msg.params[2],
                "text": msg.params[3],
            }
            key = f"HISTORY {channel}"
            buf = self._collect_buffers.get(key)
            if buf is not None:
                buf.append(entry)

    def _on_historyend(self, msg: Message) -> None:
        """HISTORYEND — resolve the HISTORY future."""
        channel = msg.params[0] if msg.params else ""
        fut_key = f"HISTORYEND:{channel}"
        fut = self._pending.pop(fut_key, None)
        if fut and not fut.done():
            fut.set_result(None)

    async def _on_ping(self, msg: Message) -> None:
        """Respond to PING with PONG."""
        token = msg.params[0] if msg.params else ""
        await self._send_raw(f"PONG :{token}")

    def _on_privmsg(self, msg: Message) -> None:
        """Buffer an incoming PRIVMSG message."""
        if len(msg.params) < 2:
            return
        target = msg.params[0]
        text = msg.params[1]
        sender = msg.prefix.split("!")[0] if msg.prefix else "unknown"
        if sender == self.nick:
            return  # don't buffer own messages
        channel = target if target.startswith("#") else f"DM:{sender}"
        self._message_buffer.append(ChatMessage(channel=channel, nick=sender, text=text))
