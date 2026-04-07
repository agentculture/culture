"""Ephemeral IRC client for read-only observation commands.

Connects to the IRC server, registers with a temporary nick, executes
a single query, collects the response, and disconnects. Designed for
CLI use — no persistent state, no daemon required.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

from culture.protocol.message import Message

logger = logging.getLogger(__name__)

# Timeout for individual recv operations
RECV_TIMEOUT = 5.0
# Timeout for the full connect + register cycle
REGISTER_TIMEOUT = 10.0


class IRCObserver:
    """Ephemeral IRC connection for read-only CLI commands."""

    def __init__(self, host: str, port: int, server_name: str):
        self.host = host
        self.port = port
        self.server_name = server_name

    def _temp_nick(self) -> str:
        """Generate a temporary nick with server prefix."""
        suffix = secrets.token_hex(2)  # 4 hex chars
        return f"{self.server_name}-_peek{suffix}"

    async def _connect_and_register(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
        """Open a TCP connection, register with a temp nick, and return the streams.

        Returns (reader, writer, nick).
        """
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=REGISTER_TIMEOUT,
        )

        nick = self._temp_nick()
        writer.write(f"NICK {nick}\r\n".encode())
        writer.write("USER _peek 0 * :culture observer\r\n".encode())
        await writer.drain()

        # Wait for RPL_WELCOME (001) to confirm registration
        buffer = ""
        try:
            while True:
                data = await asyncio.wait_for(reader.read(4096), timeout=RECV_TIMEOUT)
                if not data:
                    raise ConnectionError("Connection closed during registration")
                buffer += data.decode(errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    msg = Message.parse(line)
                    if msg.command == "001":
                        return reader, writer, nick
                    # If nick is in use, try another
                    if msg.command == "433":
                        nick = self._temp_nick()
                        writer.write(f"NICK {nick}\r\n".encode())
                        await writer.drain()
        except asyncio.TimeoutError:
            writer.close()
            raise ConnectionError("Timed out waiting for server welcome")

    async def _disconnect(self, writer: asyncio.StreamWriter) -> None:
        """Send QUIT and close."""
        try:
            writer.write(b"QUIT :observer done\r\n")
            await writer.drain()
        except OSError:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def _recv_lines(
        self, reader: asyncio.StreamReader, timeout: float = RECV_TIMEOUT
    ) -> list[Message]:
        """Read all available lines from the reader until timeout."""
        messages: list[Message] = []
        buffer = ""
        try:
            while True:
                data = await asyncio.wait_for(reader.read(4096), timeout=timeout)
                if not data:
                    break
                buffer += data.decode(errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if line.strip():
                        messages.append(Message.parse(line))
        except asyncio.TimeoutError:
            # Parse anything remaining in buffer
            if buffer.strip():
                messages.append(Message.parse(buffer.strip()))
        return messages

    @staticmethod
    async def _process_query_line(msg, end_numerics, parse_line, results, writer):
        """Handle a single parsed IRC line during a query.

        Returns True when an end marker is reached and collection should stop.
        """
        if msg.command in end_numerics:
            return True
        if msg.command == "PING":
            token = msg.params[0] if msg.params else ""
            writer.write(f"PONG :{token}\r\n".encode())
            await writer.drain()
            return False
        parsed = parse_line(msg)
        if parsed is not None:
            results.append(parsed)
        return False

    async def _irc_query(self, command, end_numerics, parse_line):
        """Send an IRC command, collect parsed results until an end marker."""
        reader, writer, nick = await self._connect_and_register()
        results = []
        try:
            writer.write(f"{command}\r\n".encode())
            await writer.drain()

            buffer = ""
            while True:
                data = await asyncio.wait_for(reader.read(4096), timeout=RECV_TIMEOUT)
                if not data:
                    break
                buffer += data.decode(errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if not line.strip():
                        continue
                    msg = Message.parse(line)
                    done = await self._process_query_line(
                        msg, end_numerics, parse_line, results, writer
                    )
                    if done:
                        return results
            return results
        except asyncio.TimeoutError:
            return results
        finally:
            await self._disconnect(writer)

    async def read_channel(self, channel: str, limit: int = 50) -> list[str]:
        """Read recent messages from a channel using HISTORY RECENT.

        Returns list of formatted strings: "<nick> message" with timestamp info.
        """
        return await self._irc_query(
            f"HISTORY RECENT {channel} {limit}",
            {"HISTORYEND"},
            self._parse_history_line,
        )

    @staticmethod
    def _parse_history_line(msg):
        from culture.formatting import relative_time

        if msg.command != "HISTORY":
            return None
        if len(msg.params) >= 4:
            entry_nick, ts, text = msg.params[1], msg.params[2], msg.params[3]
            try:
                label = relative_time(float(ts))
            except (ValueError, TypeError):
                label = ts
            return f"[{label}] <{entry_nick}> {text}"
        if len(msg.params) >= 3:
            return f"<{msg.params[1]}> {msg.params[2]}"
        return None

    @staticmethod
    def _parse_who_line(msg):
        if msg.command == "352" and len(msg.params) >= 6:
            return msg.params[5]
        return None

    @staticmethod
    def _parse_list_line(msg):
        if msg.command == "322" and len(msg.params) >= 2:
            return msg.params[1]
        return None

    async def who(self, target: str) -> list[str]:
        """WHO query -- returns list of nicks in a channel or matching a target."""
        return await self._irc_query(
            f"WHO {target}",
            {"315"},
            self._parse_who_line,
        )

    async def send_message(self, target: str, text: str) -> None:
        """Send a PRIVMSG to a channel or nick, then disconnect.

        Uses the same ephemeral connection pattern as the read commands.
        """
        # Sanitize CR/LF to prevent IRC command injection
        target = target.replace("\r", "").replace("\n", "")
        text = text.replace("\r", "").replace("\n", " ")

        reader, writer, nick = await self._connect_and_register()
        try:
            # If sending to a channel, join it first so the server accepts the PRIVMSG
            if target.startswith("#"):
                writer.write(f"JOIN {target}\r\n".encode())
                await writer.drain()
                # Drain join responses
                await self._recv_lines(reader, timeout=1.0)

            writer.write(f"PRIVMSG {target} :{text}\r\n".encode())
            await writer.drain()
        finally:
            await self._disconnect(writer)

    async def list_channels(self) -> list[str]:
        """List active channels using the LIST command.

        Returns sorted list of channel names.
        """
        channels = await self._irc_query(
            "LIST",
            {"323"},
            self._parse_list_line,
        )
        return sorted(channels)
