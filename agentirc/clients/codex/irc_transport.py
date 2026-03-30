from __future__ import annotations

import asyncio
import logging
from typing import Callable

from agentirc.protocol.message import Message
from agentirc.clients.codex.message_buffer import MessageBuffer

logger = logging.getLogger(__name__)


class IRCTransport:
    """Async IRC client for the daemon."""

    def __init__(self, host: str, port: int, nick: str, user: str,
                 channels: list[str], buffer: MessageBuffer,
                 on_mention: Callable[[str, str, str], None] | None = None,
                 tags: list[str] | None = None,
                 on_roominvite: Callable[[str, str], None] | None = None):
        self.host = host
        self.port = port
        self.nick = nick
        self.user = user
        self.channels = list(channels)
        self.buffer = buffer
        self.on_mention = on_mention
        self.tags = tags or []
        self.on_roominvite = on_roominvite
        self.connected = False
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._read_task: asyncio.Task | None = None
        self._reconnecting = False
        self._should_run = False

    async def connect(self) -> None:
        self._should_run = True
        await self._do_connect()

    async def _do_connect(self) -> None:
        try:
            self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        except (ConnectionRefusedError, OSError) as exc:
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
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                await self._send_raw("QUIT :daemon shutdown")
            except (ConnectionError, OSError):
                pass
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
        self.connected = False

    async def send_privmsg(self, target: str, text: str) -> None:
        await self._send_raw(f"PRIVMSG {target} :{text}")

    async def join_channel(self, channel: str) -> None:
        await self._send_raw(f"JOIN {channel}")
        if channel not in self.channels:
            self.channels.append(channel)

    async def part_channel(self, channel: str) -> None:
        await self._send_raw(f"PART {channel}")
        if channel in self.channels:
            self.channels.remove(channel)

    async def send_who(self, target: str) -> None:
        await self._send_raw(f"WHO {target}")

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
            return
        except (ConnectionError, OSError):
            logger.warning("IRC connection lost")
        finally:
            self.connected = False
            if self._should_run and not self._reconnecting:
                asyncio.create_task(self._reconnect())

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
            except (ConnectionError, OSError):
                delay = min(delay * 2, 60)

    async def _handle(self, msg: Message) -> None:
        if msg.command == "PING":
            token = msg.params[0] if msg.params else ""
            await self._send_raw(f"PONG :{token}")
        elif msg.command == "001":
            self.connected = True
            for channel in self.channels:
                await self._send_raw(f"JOIN {channel}")
            # Announce agent tags on connect
            if self.tags:
                tags_str = ",".join(self.tags)
                await self._send_raw(f"TAGS {self.nick} {tags_str}")
        elif msg.command == "PRIVMSG" and len(msg.params) >= 2:
            target = msg.params[0]
            text = msg.params[1]
            sender = msg.prefix.split("!")[0] if msg.prefix else "unknown"
            if sender == self.nick:
                return
            if target.startswith("#"):
                self.buffer.add(target, sender, text)
            else:
                self.buffer.add(f"DM:{sender}", sender, text)
            if self.on_mention and f"@{self.nick}" in text:
                self.on_mention(target, sender, text)
        elif msg.command == "NOTICE" and len(msg.params) >= 2:
            target = msg.params[0]
            text = msg.params[1]
            sender = msg.prefix.split("!")[0] if msg.prefix else "server"
            if target.startswith("#"):
                self.buffer.add(target, sender, text)
        elif msg.command == "ROOMINVITE" and len(msg.params) >= 3:
            channel = msg.params[0]
            meta_text = msg.params[2]
            if self.on_roominvite:
                self.on_roominvite(channel, meta_text)
