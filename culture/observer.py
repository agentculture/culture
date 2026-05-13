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
# Timeout for draining JOIN responses before sending PRIVMSG
JOIN_DRAIN_TIMEOUT = 1.0


def _sanitize_for_irc(value: str) -> str:
    """Strip CR/LF and other ASCII control chars from an IRC field value.

    ``parent_nick`` originates in the ``CULTURE_NICK`` environment variable;
    a value containing ``\\r`` or ``\\n`` would close the current IRC
    protocol line and let an attacker smuggle a second line of arbitrary
    IRC commands into the registration handshake. Strip every C0 control
    character (0x00–0x1F) plus ``\\x7f`` (DEL) so the value is safe to
    interpolate into ``NICK`` and ``USER`` lines.
    """
    return "".join(ch for ch in value if 0x20 <= ord(ch) < 0x7F)


class IRCObserver:
    """Ephemeral IRC connection for read-only CLI commands.

    When ``parent_nick`` is provided and shares this observer's server
    prefix (e.g. ``spark-claude`` against an observer for ``spark``), the
    transient nick reveals attribution as ``<server>-<agent>__peek<hex>``
    so other agents can see who is acting on the mesh. Otherwise the
    legacy opaque ``<server>-_peek<hex>`` shape is used.
    """

    def __init__(
        self,
        host: str,
        port: int,
        server_name: str,
        parent_nick: str | None = None,
    ):
        self.host = host
        self.port = port
        self.server_name = server_name
        # CR/LF and other control chars in parent_nick (sourced from the
        # CULTURE_NICK env var) would let a malformed value inject extra
        # IRC protocol lines via the realname or NICK fields, so the
        # value is sanitized once at construction time. Empty strings
        # collapse to None so downstream attribution checks short-circuit.
        if parent_nick:
            cleaned = _sanitize_for_irc(parent_nick)
            self.parent_nick: str | None = cleaned or None
        else:
            self.parent_nick = None

    def _parent_suffix(self) -> str | None:
        """Return the parent's agent suffix iff it's safe to embed in a nick.

        Only attribute when the parent's server matches ours — a peek from
        a foreign server would otherwise produce a confusing
        ``<our-server>-<their-server>-<their-agent>__peek...`` mash-up
        that looks federated but isn't.

        Uses an exact ``<server>-`` prefix match (not ``partition('-')``)
        so server names that themselves contain hyphens — e.g.
        ``my-server`` paired with parent ``my-server-claude`` — still
        resolve to the right ``claude`` suffix.
        """
        if not self.parent_nick:
            return None
        expected_prefix = f"{self.server_name}-"
        if not self.parent_nick.startswith(expected_prefix):
            return None
        agent = self.parent_nick[len(expected_prefix) :]
        return agent or None

    def _temp_nick(self) -> str:
        """Generate a temporary nick with server prefix.

        Format: ``<server>-<agent>__peek<hex>`` when parent attribution is
        available, else ``<server>-_peek<hex>``. Both shapes contain the
        substring ``_peek`` (the legacy single-underscore is a substring
        of the new ``__peek``) — that is what bots filter on, via
        ``'_peek' in nick``, to avoid greeting transient peek joins.
        """
        suffix = secrets.token_hex(2)  # 4 hex chars
        agent = self._parent_suffix()
        if agent:
            return f"{self.server_name}-{agent}__peek{suffix}"
        return f"{self.server_name}-_peek{suffix}"

    async def _process_registration_line(
        self, line: str, writer: asyncio.StreamWriter, nick: str
    ) -> tuple[bool, str]:
        """Handle one line during registration. Returns (done, nick)."""
        msg = Message.parse(line)
        if msg.command == "001":
            return True, nick
        if msg.command == "433":
            nick = self._temp_nick()
            writer.write(f"NICK {nick}\r\n".encode())
            await writer.drain()
        return False, nick

    async def _connect_and_register(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
        """Open a TCP connection, register with a temp nick, and return the streams.

        Returns (reader, writer, nick).
        """
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=REGISTER_TIMEOUT,
        )

        nick = self._temp_nick()
        # Realname embeds the parent so WHOIS resolves attribution even
        # when the nick was forced into the opaque legacy shape (e.g. a
        # cross-server peek where _parent_suffix() declined to attribute).
        if self.parent_nick:
            realname = f"culture observer (parent={self.parent_nick})"
        else:
            realname = "culture observer"
        writer.write(f"NICK {nick}\r\n".encode())
        writer.write(f"USER _peek 0 * :{realname}\r\n".encode())
        await writer.drain()

        buffer = ""
        try:
            while True:
                data = await asyncio.wait_for(reader.read(4096), timeout=RECV_TIMEOUT)
                if not data:
                    raise ConnectionError("Connection closed during registration")
                buffer += data.decode(errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    done, nick = await self._process_registration_line(line, writer, nick)
                    if done:
                        return reader, writer, nick
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

    async def _recv_lines(self, reader: asyncio.StreamReader) -> list[Message]:
        """Drain all available lines from the reader for up to JOIN_DRAIN_TIMEOUT seconds."""
        messages: list[Message] = []
        buffer = ""
        try:
            async with asyncio.timeout(JOIN_DRAIN_TIMEOUT):
                while True:
                    data = await reader.read(4096)
                    if not data:
                        break
                    buffer += data.decode(errors="replace")
                    while "\r\n" in buffer:
                        line, buffer = buffer.split("\r\n", 1)
                        if line.strip():
                            messages.append(Message.parse(line))
        except asyncio.TimeoutError:
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

    async def _drain_query_buffer(
        self, buffer, end_numerics, parse_line, results, writer
    ) -> tuple[str, bool]:
        """Process complete lines from buffer. Returns (remainder, done)."""
        while "\r\n" in buffer:
            line, buffer = buffer.split("\r\n", 1)
            if not line.strip():
                continue
            msg = Message.parse(line)
            done = await self._process_query_line(msg, end_numerics, parse_line, results, writer)
            if done:
                return buffer, True
        return buffer, False

    async def _irc_query(self, command, end_numerics, parse_line):
        """Send an IRC command, collect parsed results until an end marker."""
        reader, writer, _ = await self._connect_and_register()
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
                buffer, done = await self._drain_query_buffer(
                    buffer, end_numerics, parse_line, results, writer
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

        ``text`` is split on real ``\\n`` bytes into one PRIVMSG per line,
        since an IRC PRIVMSG must be single-line per RFC 2812. Empty lines
        (and any embedded ``\\r``) are dropped — IRC can't carry an empty
        PRIVMSG body, and this keeps multi-line output from emitting no-op
        frames. If every line is empty, the method returns without
        connecting.

        Uses the same ephemeral connection pattern as the read commands.
        """
        # Strip CR and LF from the target to prevent IRC command injection
        # (a newline in the target would let an attacker smuggle a second
        # protocol line).
        target = target.replace("\r", "").replace("\n", "")
        # Split on real newlines; drop empty lines and strip CRs
        lines = [ln for ln in text.replace("\r", "").split("\n") if ln]
        if not lines:
            return

        reader, writer, _ = await self._connect_and_register()
        try:
            # If sending to a channel, join it first so the server accepts the PRIVMSG
            if target.startswith("#"):
                writer.write(f"JOIN {target}\r\n".encode())
                await writer.drain()
                # Drain join responses
                await self._recv_lines(reader)

            for line in lines:
                writer.write(f"PRIVMSG {target} :{line}\r\n".encode())
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
