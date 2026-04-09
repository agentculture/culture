from __future__ import annotations

import asyncio
import logging
import re
from typing import Callable

from culture.aio import maybe_await
from culture.clients.acp.message_buffer import MessageBuffer
from culture.protocol.message import Message

logger = logging.getLogger(__name__)


class IRCTransport:
    """Async IRC client for the daemon."""

    def __init__(
        self,
        host: str,
        port: int,
        nick: str,
        user: str,
        channels: list[str],
        buffer: MessageBuffer,
        on_mention: Callable[[str, str, str], None] | None = None,
        tags: list[str] | None = None,
        on_roominvite: Callable[[str, str], None] | None = None,
        icon: str | None = None,
    ):
        self.host = host
        self.port = port
        self.nick = nick
        self.user = user
        self.channels = list(channels)
        self.buffer = buffer
        self.on_mention = on_mention
        self.tags = tags or []
        self.on_roominvite = on_roominvite
        self.icon = icon
        self.connected = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._reconnecting = False
        self._should_run = False
        self._background_tasks: set[asyncio.Task] = set()
        self._cmd_handlers: dict[str, Callable] = {
            "PING": self._on_ping,
            "001": self._on_welcome,
            "PRIVMSG": self._on_privmsg,
            "NOTICE": self._on_notice,
            "ROOMINVITE": self._on_roominvite,
        }

    async def connect(self) -> None:
        self._should_run = True
        await self._do_connect()

    async def _do_connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except OSError as exc:
            raise ConnectionError(
                f"Cannot connect to IRC server at {self.host}:{self.port} "
                f"- is the server running?"
            ) from exc
        await self._send_raw(f"NICK {self.nick}")
        await self._send_raw(f"USER {self.user} 0 * :{self.user}")
        self._read_task = asyncio.create_task(self._read_loop())

    async def disconnect(self) -> None:
        self._should_run = False
        if self._read_task:
            self._read_task.cancel()
            await asyncio.gather(self._read_task, return_exceptions=True)
        if self._writer:
            try:
                await self._send_raw("QUIT :daemon shutdown")
            except OSError:
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except ConnectionError:
                pass
        self.connected = False

    async def send_privmsg(self, target: str, text: str) -> None:
        for line in text.splitlines():
            if line:
                await self._send_raw(f"PRIVMSG {target} :{line}")
                self._route_to_buffer(target, self.nick, line)

    async def send_thread_create(self, channel: str, thread_name: str, text: str) -> None:
        lines = [l for l in text.splitlines() if l]
        if not lines:
            return
        await self._send_raw(f"THREAD CREATE {channel} {thread_name} :{lines[0]}")
        for line in lines[1:]:
            await self._send_raw(f"THREAD REPLY {channel} {thread_name} :{line}")

    async def send_thread_reply(self, channel: str, thread_name: str, text: str) -> None:
        for line in text.splitlines():
            if line:
                await self._send_raw(f"THREAD REPLY {channel} {thread_name} :{line}")

    async def send_thread_close(self, channel: str, thread_name: str, summary: str) -> None:
        clean = " ".join(summary.splitlines()).strip()
        await self._send_raw(f"THREADCLOSE {channel} {thread_name} :{clean}")

    async def send_threads_list(self, channel: str) -> None:
        await self._send_raw(f"THREADS {channel}")

    async def join_channel(self, channel: str) -> None:
        if not channel.startswith("#"):
            return
        await self._send_raw(f"JOIN {channel}")
        if channel not in self.channels:
            self.channels.append(channel)

    async def part_channel(self, channel: str) -> None:
        if not channel.startswith("#"):
            return
        await self._send_raw(f"PART {channel}")
        if channel in self.channels:
            self.channels.remove(channel)

    async def send_who(self, target: str) -> None:
        await self._send_raw(f"WHO {target}")

    async def send_topic(self, channel: str, topic: str | None = None) -> None:
        if topic is not None:
            await self._send_raw(f"TOPIC {channel} :{topic}")
        else:
            await self._send_raw(f"TOPIC {channel}")

    async def send_raw(self, line: str) -> None:
        """Send a raw IRC line. Public for commands like HISTORY."""
        if self._writer:
            self._writer.write(f"{line}\r\n".encode())
            await self._writer.drain()

    async def _send_raw(self, line: str) -> None:
        await self.send_raw(line)

    async def _read_loop(self) -> None:
        buf = ""
        try:
            while True:
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
            logger.warning("IRC connection lost")
        finally:
            self.connected = False
            if self._should_run and not self._reconnecting:
                task = asyncio.create_task(self._reconnect())
                self._background_tasks.add(task)
                task.add_done_callback(self._background_tasks.discard)

    async def _reconnect(self) -> None:
        self._reconnecting = True
        delay = 1
        while self._should_run:
            logger.info("Reconnecting to IRC in %ds...", delay)
            await asyncio.sleep(delay)
            try:
                await self._do_connect()
                logger.info("Reconnected to IRC")
                self._reconnecting = False
                return
            except OSError:
                delay = min(delay * 2, 60)

    async def _handle(self, msg: Message) -> None:
        handler = self._cmd_handlers.get(msg.command)
        if handler:
            await maybe_await(handler(msg))

    async def _on_ping(self, msg: Message) -> None:
        token = msg.params[0] if msg.params else ""
        await self._send_raw(f"PONG :{token}")

    async def _on_welcome(self, msg: Message) -> None:
        self.connected = True
        for channel in self.channels:
            await self._send_raw(f"JOIN {channel}")
        if self.tags:
            tags_str = ",".join(self.tags)
            await self._send_raw(f"TAGS {self.nick} {tags_str}")
        if self.icon:
            await self._send_raw(f"ICON {self.icon}")

    def _on_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2:
            return
        target = msg.params[0]
        text = msg.params[1]
        sender = msg.prefix.split("!")[0] if msg.prefix else "unknown"
        if sender == self.nick:
            return
        self._route_to_buffer(target, sender, text)
        self._detect_and_fire_mention(target, sender, text)

    def _route_to_buffer(self, target: str, sender: str, text: str) -> None:
        """Insert the message into the appropriate buffer (channel or DM)."""
        if target.startswith("#"):
            self.buffer.add(target, sender, text)
        else:
            self.buffer.add(f"DM:{sender}", sender, text)

    def _detect_and_fire_mention(self, target: str, sender: str, text: str) -> None:
        """Check if the message mentions this agent and fire the callback."""
        if not self.on_mention:
            return
        # DMs always activate (target is the agent's own nick)
        if target == self.nick:
            self.on_mention(target, sender, text)
            return
        short = self.nick.split("-", 1)[1] if "-" in self.nick else None
        if re.search(rf"@{re.escape(self.nick)}\b", text) or (
            short and re.search(rf"@{re.escape(short)}\b", text)
        ):
            self.on_mention(target, sender, text)

    def _on_notice(self, msg: Message) -> None:
        if len(msg.params) < 2:
            return
        target = msg.params[0]
        text = msg.params[1]
        sender = msg.prefix.split("!")[0] if msg.prefix else "server"
        if target.startswith("#"):
            self.buffer.add(target, sender, text)

    def _on_roominvite(self, msg: Message) -> None:
        if len(msg.params) < 3:
            return
        channel = msg.params[0]
        meta_text = msg.params[2]
        if self.on_roominvite:
            self.on_roominvite(channel, meta_text)
