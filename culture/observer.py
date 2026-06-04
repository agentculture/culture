"""Ephemeral and persistent IRC observer clients.

``IRCObserver`` — opens a fresh TCP connection, registers with a peek
nick, issues one query, disconnects. CLI-shaped, no shared state.

``PersistentObserver`` (v8.19.17) — one long-lived TCP connection that
the dashboard reuses across every chat-read poll. Channels are joined
lazily on first read and stay joined for the dashboard's lifetime;
auto-reconnect re-JOINs the membership set after a server bounce.
Replaces 24 ephemeral peek connections per minute (every 2.5 s in
chat mode) with one persistent connection. Compatible with the
v8.19.13 server-side event suppression: the persistent observer's nick
still begins with ``_peek``, so its lazy JOINs don't fire ``user.join``
events into channel buffers.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import deque

from culture.protocol.message import Message

logger = logging.getLogger(__name__)

# Timeout for individual recv operations
RECV_TIMEOUT = 5.0
# Timeout for the full connect + register cycle
REGISTER_TIMEOUT = 10.0

# IRC numerics that mean "registration cannot proceed" — surface them
# verbatim instead of letting the registration loop wedge to RECV_TIMEOUT.
# v9.1.6: the observer pre-9.1.6 only handled 001 (welcome) and 433 (nick
# in use). Any other rejection numeric — most importantly 432
# (ERR_ERRONEUSNICKNAME) which the IRCd sends when the observer's nick
# doesn't match the server's expected ``<server_name>-`` prefix — was
# silently dropped. The loop then read no further data, hit
# RECV_TIMEOUT, and surfaced as "Timed out waiting for server welcome"
# with zero diagnostic value (the actual cause: server.yaml's
# ``server.name`` drifted from what the running IRCd was started with).
_FATAL_REGISTRATION_NUMERICS = frozenset(
    {
        "432",  # ERR_ERRONEUSNICKNAME — bad nick shape (prefix mismatch lives here)
        "464",  # ERR_PASSWDMISMATCH — server requires PASS
        "465",  # ERR_YOUREBANNEDCREEP — banned
        "466",  # ERR_YOUWILLBEBANNED
        "421",  # ERR_UNKNOWNCOMMAND for NICK/USER (some IRCds use this)
    }
)


class RegistrationRejected(ConnectionError):
    """The IRCd rejected the observer's registration with an
    actionable error numeric. Raised instead of letting the loop wedge
    to RECV_TIMEOUT, which previously masked server-name drift.

    Attributes:
        numeric: the IRC numeric the server sent (e.g. ``"432"``).
        server_text: the server's reason text (e.g. ``"Nickname must
            start with plenty-"``).
        attempted_nick: the nick the observer tried to register.
        server_name: the server name the observer used to mint the
            nick (read from ``server.yaml``).
        host: TCP host the observer connected to.
        port: TCP port.
    """

    def __init__(
        self,
        numeric: str,
        server_text: str,
        attempted_nick: str,
        server_name: str,
        host: str,
        port: int,
    ) -> None:
        self.numeric = numeric
        self.server_text = server_text
        self.attempted_nick = attempted_nick
        self.server_name = server_name
        self.host = host
        self.port = port
        super().__init__(
            f"IRC server rejected observer registration: "
            f"{numeric} {server_text!r}. "
            f"Attempted nick {attempted_nick!r} against {host}:{port} "
            f"with server_name={server_name!r} (read from server.yaml). "
            f"If the running IRCd was started with a different --name, "
            f"this is in-place server.name drift — run "
            f"`culture migrate boss-prefix <old> <new>` to fix worker yamls "
            f"and restart the IRCd with the correct --name."
        )


# How long to wait for the response to a HISTORY query before giving up.
# IRC HISTORY replies are interleaved with PRIVMSGs from other channels;
# we keep reading until HISTORYEND or this deadline.
PERSISTENT_HISTORY_TIMEOUT = 2.0
# How long to wait for the JOIN reply (RPL_ENDOFNAMES) before assuming the
# server accepted us. Short — JOIN is cheap and the client-may-read-history
# membership gate only needs the channel registered, not the NAMES list.
PERSISTENT_JOIN_TIMEOUT = 1.5


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

    async def _process_registration_line(
        self, line: str, writer: asyncio.StreamWriter, nick: str
    ) -> tuple[bool, str]:
        """Handle one line during registration. Returns (done, nick).

        v9.1.6: also raises :class:`RegistrationRejected` when the
        server sends a fatal registration numeric (see
        ``_FATAL_REGISTRATION_NUMERICS``). Previously these numerics
        were silently dropped — the loop continued reading and the
        observer wedged to ``RECV_TIMEOUT`` reporting only "Timed out
        waiting for server welcome", which gave operators zero
        diagnostic value when the actual cause was in-place
        ``server.name`` drift (BUG 1).
        """
        msg = Message.parse(line)
        if msg.command == "001":
            return True, nick
        if msg.command == "433":
            nick = self._temp_nick()
            writer.write(f"NICK {nick}\r\n".encode())
            await writer.drain()
            return False, nick
        if msg.command in _FATAL_REGISTRATION_NUMERICS:
            # The server's "reason" text is typically the trailing
            # param (everything after the last `:`). For 432 the IRCd
            # sends e.g. ``432 <nick> :Nickname must start with plenty-``.
            server_text = msg.params[-1] if msg.params else ""
            raise RegistrationRejected(
                numeric=msg.command,
                server_text=server_text,
                attempted_nick=nick,
                server_name=self.server_name,
                host=self.host,
                port=self.port,
            )
        return False, nick

    async def _connect_and_register(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
        """Open a TCP connection, register with a temp nick, and return the streams.

        Returns (reader, writer, nick).

        v9.1.6: the ``TimeoutError`` path now reports the last few
        lines actually received so an operator who hits a NOVEL
        rejection numeric (one not in ``_FATAL_REGISTRATION_NUMERICS``)
        still gets a clue rather than a bare "timed out" message.
        """
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=REGISTER_TIMEOUT,
        )

        nick = self._temp_nick()
        writer.write(f"NICK {nick}\r\n".encode())
        writer.write("USER _peek 0 * :culture observer\r\n".encode())
        await writer.drain()

        buffer = ""
        # Keep a bounded ring of the last 16 lines we received so the
        # timeout path can report what actually came over the wire.
        received_tail: deque[str] = deque(maxlen=16)
        try:
            while True:
                data = await asyncio.wait_for(reader.read(4096), timeout=RECV_TIMEOUT)
                if not data:
                    raise ConnectionError(
                        f"Connection to {self.host}:{self.port} closed during "
                        f"registration. Attempted nick {nick!r}. "
                        f"Last lines received: {list(received_tail)!r}"
                    )
                buffer += data.decode(errors="replace")
                while "\r\n" in buffer:
                    line, buffer = buffer.split("\r\n", 1)
                    if line.strip():
                        received_tail.append(line)
                    done, nick = await self._process_registration_line(line, writer, nick)
                    if done:
                        return reader, writer, nick
        except asyncio.TimeoutError:
            writer.close()
            raise ConnectionError(
                f"Timed out waiting for server welcome from "
                f"{self.host}:{self.port}. Attempted nick {nick!r} "
                f"(server_name={self.server_name!r} from server.yaml). "
                f"Last lines received: {list(received_tail)!r}. "
                f"If the running IRCd was started with a different "
                f"--name, that's in-place server.name drift."
            )

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

        Joins the channel before issuing HISTORY because the server-side
        membership gate (v8.18.3 ``_client_may_read_history``) refuses
        non-members. Without the pre-JOIN, this endpoint silently
        returns empty for any channel the observer wasn't already in —
        which is all of them, since each observer connection is
        short-lived. Per Qodo PR #28 #3 (Correctness).

        Returns list of formatted strings: "<nick> message" with
        timestamp info.
        """
        reader, writer, _nick = await self._connect_and_register()
        try:
            # JOIN to clear the membership gate; drain its reply (353/366).
            writer.write(f"JOIN {channel}\r\n".encode())
            await writer.drain()
            await self._recv_lines(reader, timeout=1.0)

            # Now HISTORY against a channel we're in.
            writer.write(f"HISTORY RECENT {channel} {limit}\r\n".encode())
            await writer.drain()
            results: list[str] = []
            messages = await self._recv_lines(reader, timeout=2.0)
            for msg in messages:
                if msg.command == "HISTORYEND":
                    break
                parsed = self._parse_history_line(msg)
                if parsed is not None:
                    results.append(parsed)
            return results
        finally:
            await self._disconnect(writer)

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

        reader, writer, nick = await self._connect_and_register()
        try:
            # If sending to a channel, join it first so the server accepts the PRIVMSG
            if target.startswith("#"):
                writer.write(f"JOIN {target}\r\n".encode())
                await writer.drain()
                # Drain join responses
                await self._recv_lines(reader, timeout=1.0)

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

    @staticmethod
    def _parse_history_message(msg: Message, channel: str) -> str | None:
        """Static helper used by both IRCObserver and PersistentObserver."""
        from culture.formatting import relative_time

        if msg.command != "HISTORY":
            return None
        if len(msg.params) >= 4 and msg.params[0] != channel:
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

    async def archive_channel(self, channel: str) -> bool:
        """Archive a channel by sending CHANARCHIVE.

        Returns True ONLY if the server explicitly acknowledged the
        archive. Returns False on permission denial, non-existent
        channel, server error, or timeout. Per Qodo PR #27 #7
        (Reliability): the prior implementation joined-first (creating
        the channel as a side-effect) then returned True regardless of
        the server reply — callers couldn't distinguish "archived" from
        "silently failed".
        """
        reader, writer, nick = await self._connect_and_register()
        try:
            # We need operator status to archive. JOIN grants it for new
            # channels we create — but if the channel ALREADY exists and
            # we're not in it, we can't archive someone else's. The CLI
            # caller should ensure the channel exists; we don't pre-create
            # it here (would mask the "no such channel" failure mode).
            writer.write(f"JOIN {channel}\r\n".encode())
            await writer.drain()
            # Drain JOIN response so it doesn't pollute the CHANARCHIVE reply.
            await self._recv_lines(reader, timeout=1.0)

            writer.write(f"CHANARCHIVE {channel}\r\n".encode())
            await writer.drain()

            # ``_recv_lines`` returns parsed ``Message`` objects, not raw
            # strings. The acknowledgement text lives in the trailing
            # NOTICE param; join all params with spaces so we can search
            # the textual body case-insensitively.
            messages = await self._recv_lines(reader, timeout=2.0)
            for msg in messages:
                body = " ".join(str(p) for p in msg.params).lower()
                if "has been archived" in body:
                    return True
                # Server-side refusal patterns surface as NOTICE: missing
                # channel, lacking operator, malformed args. None of these
                # should be reported as success.
                if "no such channel" in body or "do not have permission" in body:
                    return False
            # No explicit ack within the timeout window → treat as failure.
            # We'd rather a CLI say "unknown" than falsely claim success.
            return False
        finally:
            await self._disconnect(writer)


class PersistentObserver:
    """Long-lived IRC observer for the dashboard (v8.19.17).

    Holds one TCP connection + IRC registration across the dashboard's
    entire lifetime. ``read_channel`` lazy-joins each channel on first
    request and the membership stays open thereafter — so 24 polls/min
    in chat mode cost one register + one JOIN per channel, not 24 of
    each. Auto-reconnects and re-JOINs the membership set when the
    connection drops.

    Nick prefix is ``_peek`` so the server-side suppression added in
    v8.19.13 (``Client._handle_join`` / ``_handle_part``) keeps this
    observer's JOINs from emitting ``user.join`` events into other
    channel members' buffers.

    Concurrency: a single asyncio.Lock serializes requests, since the
    IRC response stream demuxes by channel only on HISTORY replies
    (other server traffic — pings, NOTICEs — has to be drained between
    user-facing reads). Dashboard chat polls are infrequent (2.5 s
    cadence) and reads are sub-second, so serialization is not a
    bottleneck.
    """

    def __init__(self, host: str, port: int, server_name: str):
        self.host = host
        self.port = port
        self.server_name = server_name
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._nick: str | None = None
        self._joined: set[str] = set()
        self._lock = asyncio.Lock()
        self._buffer = ""

    @property
    def nick(self) -> str | None:
        return self._nick

    @property
    def joined_channels(self) -> frozenset[str]:
        return frozenset(self._joined)

    def _new_nick(self) -> str:
        # Keep the ``_peek`` prefix so v8.19.13 event suppression
        # continues to silence our JOINs; the ``DASH`` infix makes the
        # observer identifiable in connection lists.
        return f"{self.server_name}-_peekDASH{secrets.token_hex(2)}"

    def _is_connected(self) -> bool:
        return (
            self._writer is not None and not self._writer.is_closing() and self._reader is not None
        )

    async def _ensure_connected(self) -> None:
        if self._is_connected():
            return
        await self._connect()

    async def _connect(self) -> None:
        """Open the connection, register, and re-JOIN every channel in the set."""
        # Reset any half-open state from a prior connection drop.
        self._buffer = ""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port),
            timeout=REGISTER_TIMEOUT,
        )
        self._nick = self._new_nick()
        self._writer.write(f"NICK {self._nick}\r\n".encode())
        self._writer.write(b"USER _peek 0 * :culture dashboard observer\r\n")
        await self._writer.drain()
        # Wait for RPL_WELCOME (001); handle a NICK collision by re-rolling.
        await self._await_registration()
        # Re-JOIN every channel we used to be in. Failures are non-fatal —
        # the next read_channel will JOIN on demand.
        previous = list(self._joined)
        self._joined.clear()
        for channel in previous:
            try:
                await self._join(channel)
            except (OSError, asyncio.TimeoutError):
                logger.warning("re-JOIN of %s failed after reconnect", channel)

    async def _await_registration(self) -> None:
        assert self._reader is not None and self._writer is not None
        deadline_loop = asyncio.get_running_loop()
        deadline = deadline_loop.time() + REGISTER_TIMEOUT
        while True:
            remaining = deadline - deadline_loop.time()
            if remaining <= 0:
                raise ConnectionError("registration timed out")
            msg = await self._next_message(timeout=remaining)
            if msg is None:
                continue
            if msg.command == "001":
                return
            if msg.command == "433":
                self._nick = self._new_nick()
                self._writer.write(f"NICK {self._nick}\r\n".encode())
                await self._writer.drain()
            elif msg.command == "PING":
                token = msg.params[0] if msg.params else ""
                self._writer.write(f"PONG :{token}\r\n".encode())
                await self._writer.drain()

    async def _next_message(self, timeout: float) -> Message | None:
        """Read one IRC line from the connection, parsed. Returns None on a partial read."""
        assert self._reader is not None
        while "\r\n" not in self._buffer:
            try:
                data = await asyncio.wait_for(self._reader.read(4096), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            if not data:
                raise ConnectionError("server closed connection")
            self._buffer += data.decode(errors="replace")
        line, self._buffer = self._buffer.split("\r\n", 1)
        line = line.strip()
        if not line:
            return None
        return Message.parse(line)

    async def _send_raw(self, line: str) -> None:
        assert self._writer is not None
        self._writer.write((line + "\r\n").encode())
        await self._writer.drain()

    async def _join(self, channel: str) -> None:
        """JOIN a channel and wait for RPL_ENDOFNAMES (366) or RPL_NAMREPLY (353)."""
        await self._send_raw(f"JOIN {channel}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PERSISTENT_JOIN_TIMEOUT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                logger.debug(
                    "JOIN %s — no end-of-names within %ss", channel, PERSISTENT_JOIN_TIMEOUT
                )
                self._joined.add(channel)
                return
            msg = await self._next_message(timeout=remaining)
            if msg is None:
                continue
            if msg.command == "PING":
                token = msg.params[0] if msg.params else ""
                await self._send_raw(f"PONG :{token}")
                continue
            if msg.command in ("366", "353"):
                self._joined.add(channel)
                return
            if msg.command in ("403", "473", "474", "475"):
                # No such channel / invite-only / banned / +k — give up.
                logger.info("JOIN %s rejected by server (%s)", channel, msg.command)
                return

    async def read_channel(self, channel: str, limit: int = 50) -> list[str]:
        """Return up to ``limit`` recent messages from ``channel`` via HISTORY RECENT.

        On a dropped connection the request is retried once with a fresh
        reconnect + re-JOIN of the membership set. A second failure
        returns ``[]`` rather than raising — the dashboard renders an
        empty channel rather than a 500.
        """
        async with self._lock:
            try:
                await self._ensure_connected()
                if channel not in self._joined:
                    await self._join(channel)
                return await self._read_history(channel, limit)
            except (OSError, asyncio.IncompleteReadError, ConnectionError) as exc:
                logger.info("persistent observer reconnecting after %s", exc)
                await self._close_quietly()
                try:
                    await self._ensure_connected()
                    if channel not in self._joined:
                        await self._join(channel)
                    return await self._read_history(channel, limit)
                except (OSError, ConnectionError, asyncio.TimeoutError) as exc2:
                    logger.warning("persistent observer read_channel %s failed: %s", channel, exc2)
                    return []

    async def _read_history(self, channel: str, limit: int) -> list[str]:
        await self._send_raw(f"HISTORY RECENT {channel} {limit}")
        results: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PERSISTENT_HISTORY_TIMEOUT
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return results
            msg = await self._next_message(timeout=remaining)
            if msg is None:
                continue
            if msg.command == "HISTORYEND":
                return results
            if msg.command == "PING":
                token = msg.params[0] if msg.params else ""
                await self._send_raw(f"PONG :{token}")
                continue
            if msg.command == "HISTORY":
                parsed = IRCObserver._parse_history_message(msg, channel)
                if parsed is not None:
                    results.append(parsed)

    async def send_message(self, target: str, text: str) -> None:
        """Send a PRIVMSG over the persistent connection.

        Mirrors ``IRCObserver.send_message`` semantics: real-newline split
        into one PRIVMSG per line, drop empty lines, strip CRLF from the
        target. Goes through the same channel-JOIN gate as ``read_channel``
        for channel targets (we lazy-JOIN once and stay joined).
        """
        target = target.replace("\r", "").replace("\n", "")
        lines = [ln for ln in text.replace("\r", "").split("\n") if ln]
        if not lines:
            return
        async with self._lock:
            try:
                await self._ensure_connected()
                if target.startswith("#") and target not in self._joined:
                    await self._join(target)
                for line in lines:
                    await self._send_raw(f"PRIVMSG {target} :{line}")
            except (OSError, ConnectionError) as exc:
                logger.warning("persistent observer send to %s failed: %s", target, exc)
                await self._close_quietly()

    async def close(self) -> None:
        async with self._lock:
            await self._close_quietly()

    async def _close_quietly(self) -> None:
        if self._writer is None:
            return
        try:
            self._writer.write(b"QUIT :dashboard observer shutdown\r\n")
            await self._writer.drain()
        except OSError:
            pass
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except OSError:
            pass
        self._reader = None
        self._writer = None
        self._buffer = ""
