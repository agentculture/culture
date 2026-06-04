from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Callable

from culture.aio import maybe_await
from culture.clients.bridge.message_buffer import MessageBuffer
from culture.constants import SYSTEM_USER_PREFIX
from culture.protocol.message import Message
from culture.telemetry.context import (
    TRACEPARENT_TAG,
    context_from_traceparent,
    current_traceparent,
    extract_traceparent_from_tags,
)

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer

    # Qodo PR #50 #1: cite-don't-import; bridge owns its telemetry copy.
    from culture.clients.bridge.telemetry import (  # noqa: F401
        HarnessMetricsRegistry,
    )

logger = logging.getLogger(__name__)


class IRCTransport:
    """Async IRC client for the daemon.

    Optional kwargs ``tracer``, ``metrics``, and ``backend`` enable OTEL
    tracing and LLM metrics when an SDK provider is installed. Pass ``None``
    (the default) for all three to run without instrumentation.
    """

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
        tracer: Tracer | None = None,
        metrics: HarnessMetricsRegistry | None = None,
        backend: str = "bridge",
        on_welcome: Callable[[], None] | None = None,
        on_chathistory_entry: Callable[[dict], None] | None = None,
        on_chathistory_end: Callable[[str], None] | None = None,
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
        self._tracer = tracer
        # accepted for future per-message metrics (e.g. byte counters); unused in v1
        self._metrics = metrics
        self._backend = backend
        self._on_welcome_cb = on_welcome
        self.on_chathistory_entry = on_chathistory_entry
        self.on_chathistory_end = on_chathistory_end
        # CHATHISTORY parser state — accumulate entries for the
        # in-flight batch keyed by ``batch_id``.
        self._current_batch_id: str | None = None
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
            "TOPIC": self._on_topic,
            "331": self._on_numeric_topic,
            "332": self._on_numeric_topic,
            "HISTORY": self._on_history,
            "HISTORYEND": self._on_historyend,
            # v8.19.42 — confirmation-based channel tracking.
            "JOIN": self._on_join,
            "PART": self._on_part,
            "KICK": self._on_kick,
            "404": self._on_cannotsendtochan,  # ERR_CANNOTSENDTOCHAN
            "474": self._on_bannedfromchan,  # ERR_BANNEDFROMCHAN
            "ERROR": self._on_server_error,
            # Phase 3 — IRCv3 draft/chathistory DM-spool drain.
            "BATCH": self._on_batch,
            "CHATHISTORY": self._on_chathistory,
            # v9.1.7 — fail-loud on registration rejection. Pre-9.1.7
            # the bridge had NO 432 handler: if the IRCd rejected the
            # bridge's nick, the read loop kept spinning, the bridge
            # never reached _on_welcome, and the daemon log showed
            # nothing actionable. The 432 handler now surfaces the
            # error (operator sees what to fix) and triggers exit;
            # the bridge does NOT auto-recover because the daemon
            # holds session-level state (audit log, IPC socket,
            # owner_map keyed on nick) that would corrupt under nick
            # mutation. The observer auto-recovers per v9.1.7 because
            # it is ephemeral and holds no such state.
            "432": self._on_erroneous_nick,  # ERR_ERRONEUSNICKNAME
            "433": self._on_nick_in_use,  # ERR_NICKNAMEINUSE
        }

    def _span(self, name: str, attrs: dict | None = None) -> AbstractContextManager:
        """Return a real span context manager when tracing is enabled, else a no-op.

        Keeps the no-tracer fast path clean — all callers write the same
        ``with self._span(...):`` pattern regardless of whether a tracer is
        configured.

        Does NOT accept a ``context=`` argument; callers that need a span
        parented to a remote trace context (e.g. inbound message handling in
        ``_handle``) call ``self._tracer.start_as_current_span`` directly.
        """
        if self._tracer is not None:
            return self._tracer.start_as_current_span(name, attributes=attrs or {})
        return contextlib.nullcontext()

    async def connect(self) -> None:
        self._should_run = True
        await self._do_connect()

    async def _do_connect(self) -> None:
        with self._span(
            "harness.irc.connect",
            attrs={
                "harness.backend": self._backend,
                "harness.nick": self.nick,
                "harness.server": f"{self.host}:{self.port}",
            },
        ):
            try:
                self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
            except OSError as exc:
                raise ConnectionError(
                    f"Cannot connect to IRC server at {self.host}:{self.port} "
                    f"- is the server running?"
                ) from exc
            await self._send_raw("CAP REQ :message-tags")
            await self._send_raw(f"NICK {self.nick}")
            await self._send_raw(f"USER {self.user} 0 * :{self.user}")
            await self._send_raw("CAP END")
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
                if target.startswith("#"):
                    self.buffer.add(target, self.nick, line)
                else:
                    self.buffer.add(f"DM:{target}", self.nick, line)

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
        """Send a JOIN. v8.19.42: tracking is updated by the server's
        confirmation echo in ``_on_join``, NOT optimistically here.

        Why: prior to v8.19.42, ``self.channels`` was appended BEFORE the
        server confirmed the JOIN. When the server rejected the JOIN (e.g.
        the task-channel ACL refused a boss whose owner_map cache was
        stale), the transport still thought it had joined. Subsequent
        ``send_privmsg`` calls fired PRIVMSG into a channel the IRC server
        thought the client wasn't in, and the server returned
        ``ERR_CANNOTSENDTOCHAN`` (404) which the transport silently
        ignored — the brief was silently dropped from the worker's POV.

        Now: send JOIN, request HISTORY backfill, and let ``_on_join``
        populate ``self.channels`` when (and only when) the server echoes
        our own JOIN back to us. If the JOIN is rejected,
        ``_on_bannedfromchan`` logs a warning and tracking stays empty so
        the next ``join_channel`` call retries cleanly.
        """
        if not channel.startswith("#"):
            return
        if channel in self.channels:
            return  # already confirmed-joined — skip duplicate JOIN + HISTORY
        await self._send_raw(f"JOIN {channel}")
        # Backfill: request recent history so the buffer has pre-existing
        # messages.  The HISTORY responses flow through _on_history().
        await self._send_raw(f"HISTORY RECENT {channel} 200")

    async def part_channel(self, channel: str) -> None:
        """Send a PART. ``self.channels`` is updated by ``_on_part`` when
        the server echoes our own PART back to us — symmetric to the
        confirmation-based JOIN tracking in ``join_channel`` (v8.19.42).
        """
        if not channel.startswith("#"):
            return
        await self._send_raw(f"PART {channel}")

    async def send_who(self, target: str) -> None:
        await self._send_raw(f"WHO {target}")

    async def send_topic(self, channel: str, topic: str | None = None) -> None:
        if topic is not None:
            await self._send_raw(f"TOPIC {channel} :{topic}")
        else:
            await self._send_raw(f"TOPIC {channel}")

    async def send_raw(self, line: str) -> None:
        """Send a raw IRC line. Public for commands like HISTORY.

        When a tracer is configured and a span is active, prepends the W3C
        ``@culture.dev/traceparent=`` IRCv3 tag so all outbound paths — whether
        called via the internal ``_send_raw`` helper or directly by daemon code
        — carry trace context consistently.

        Includes a last-line CRLF-injection guard (Qodo PR #30 #2): even
        if every caller pre-validates inputs, ``assert_safe_irc_line``
        refuses any line that contains CR/LF/NUL. Refused lines are
        logged and dropped — they MUST NOT reach the wire.
        """
        from culture.agentirc.irc_targets import InvalidIRCTarget, assert_safe_irc_line

        try:
            assert_safe_irc_line(line)
        except InvalidIRCTarget as exc:
            logger.error("refusing to send unsafe IRC line: %s", exc)
            return
        if self._tracer is not None and not line.startswith("@"):
            tp = current_traceparent()
            if tp is not None:
                line = f"@{TRACEPARENT_TAG}={tp} {line}"
        if self._writer:
            self._writer.write(f"{line}\r\n".encode())
            await self._writer.drain()

    async def _send_raw(self, line: str) -> None:
        """Internal send helper; delegates to send_raw (injection lives there)."""
        await self.send_raw(line)

    # RFC 2812 caps IRC lines at 512 bytes including CR LF. We hold a
    # multi-line read buffer between dispatches, so the cap below is
    # large enough to span several full lines (16x the per-line max) yet
    # tight enough that a hostile or malformed peer streaming bytes
    # without a newline cannot exhaust memory.
    #
    # Qodo PR #50 #2: the prior read loop appended to ``buf`` with no
    # ceiling — a peer that sent 4096-byte non-newline chunks forever
    # would grow ``buf`` unbounded. AgentIRC's serverside client.py
    # uses the same 8192 cap with oldest-data discard; this matches.
    _READ_BUF_CAP: int = 8192

    async def _read_loop(self) -> None:
        buf = ""
        try:
            while True:
                data = await self._reader.read(4096)
                if not data:
                    break
                buf += data.decode("utf-8", errors="replace")
                buf = buf.replace("\r\n", "\n").replace("\r", "\n")
                # Discard-oldest cap on the read buffer (Qodo #50 #2).
                # If a malformed peer streams data without ``\n``, drop
                # the oldest bytes so the buffer cannot grow without
                # bound. We log once per overflow event so the operator
                # has a breadcrumb if a real peer keeps tripping it.
                if len(buf) > self._READ_BUF_CAP:
                    overflow = len(buf) - self._READ_BUF_CAP
                    logger.warning(
                        "IRC read buffer overflowed cap; " "discarding %d oldest byte(s) (cap=%d)",
                        overflow,
                        self._READ_BUF_CAP,
                    )
                    buf = buf[-self._READ_BUF_CAP :]
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
        # Extract inbound traceparent before opening any span so the new span
        # can be correctly parented to the remote trace context.
        result = extract_traceparent_from_tags(msg, peer=None)

        if self._tracer is not None:
            if result.status == "valid":
                ctx = context_from_traceparent(result.traceparent)
                span_cm = self._tracer.start_as_current_span(
                    "harness.irc.message.handle",
                    context=ctx,
                    attributes={
                        "irc.command": msg.command,
                        "irc.client.nick": self.nick,
                        "culture.trace.origin": "remote",
                    },
                )
            else:
                attrs = {
                    "irc.command": msg.command,
                    "irc.client.nick": self.nick,
                    "culture.trace.origin": ("local" if result.status == "missing" else "remote"),
                }
                if result.status in ("malformed", "too_long"):
                    attrs["culture.trace.dropped_reason"] = result.status
                span_cm = self._tracer.start_as_current_span(
                    "harness.irc.message.handle",
                    attributes=attrs,
                )
        else:
            span_cm = contextlib.nullcontext()

        with span_cm:
            handler = self._cmd_handlers.get(msg.command)
            if handler:
                await maybe_await(handler(msg))

    async def _on_ping(self, msg: Message) -> None:
        token = msg.params[0] if msg.params else ""
        await self._send_raw(f"PONG :{token}")

    async def _on_erroneous_nick(self, msg: Message) -> None:
        """v9.1.7 — IRCd rejected the bridge's nick with 432.

        Pre-9.1.7 the bridge had NO 432 handler: the read loop
        continued, ``self.connected`` stayed False, the bridge wedged
        silently. Now we log the IRCd's reason text verbatim, set a
        clear failure state, and CLOSE the writer so the read loop
        exits with a connection-closed condition the daemon's outer
        retry/backoff can observe.

        We do NOT auto-recover the nick (unlike the observer at
        ``culture/observer.py``). The bridge daemon holds session-
        level state — AuditWriter keyed on ``self.nick``, IPC socket
        symlink under ``culture-<nick>.sock``, owner_map / role_map
        lookups, DM routing — all of which would corrupt under nick
        mutation. The right move is to surface the error, exit, and
        let the operator restart with the correct nick after running
        ``culture server migrate-prefix``.

        Adversarial-critique-panel blockers from the design phase
        ruled out auto-recovery here for exactly these reasons
        (split-brain identity, AuditWriter / IPC desync, hostile
        IRCd attack surface, log-spam loops under flapping IRCd).
        """
        server_text = msg.params[-1] if msg.params else ""
        logger.error(
            "Bridge nick %r rejected by IRCd at %s:%s (432): %s. "
            "This is server.name drift — the IRCd was started with a "
            "different --name than the bridge expected. Fix: run "
            "`culture server migrate-prefix <old> <new>` AND restart "
            "the IRCd with the right --name, then `culture bridge start "
            "%s` again.",
            self.nick,
            self.host,
            self.port,
            server_text,
            self.nick,
        )
        # Close the writer so the read loop exits and the daemon can
        # observe failure rather than spin waiting for 001 that will
        # never arrive. ``self.connected`` was already False; the
        # daemon's reconnect logic will see the closed writer and
        # back off / exit per its policy.
        self._should_run = False
        try:
            if self._writer is not None:
                self._writer.close()
        except OSError:
            pass

    async def _on_nick_in_use(self, msg: Message) -> None:
        """v9.1.7 — IRCd rejected the bridge's nick with 433 (nick
        in use). For the bridge this almost always means a SECOND
        bridge process is already holding the same nick — racing
        spawns, stale process the operator didn't kill, or a
        leftover from a crashed-bridge reconnect. Auto-retrying
        with a random suffix would mint a fresh identity behind
        the operator's back, breaking every consumer of the bridge's
        IPC socket and audit log.

        Fail-loud: log the conflict + the actionable command,
        close the writer, let the daemon exit.
        """
        server_text = msg.params[-1] if msg.params else ""
        logger.error(
            "Bridge nick %r already in use on IRCd at %s:%s (433): %s. "
            "Another bridge or process is holding this nick. Fix: "
            "`culture bridge status` to find the rival, then "
            "`culture bridge stop %s` on the host that owns it before "
            "starting a new one.",
            self.nick,
            self.host,
            self.port,
            server_text,
            self.nick,
        )
        self._should_run = False
        try:
            if self._writer is not None:
                self._writer.close()
        except OSError:
            pass

    async def _on_welcome(self, msg: Message) -> None:
        self.connected = True
        for channel in self.channels:
            await self._send_raw(f"JOIN {channel}")
        if self.tags:
            tags_str = ",".join(self.tags)
            await self._send_raw(f"TAGS {self.nick} {tags_str}")
        if self.icon:
            await self._send_raw(f"ICON {self.icon}")
        await self._send_raw(f"MODE {self.nick} +A")
        # Phase 3 — drain the DM spool. Fires the daemon's
        # on_welcome callback so it can issue CHATHISTORY against
        # our own nick (the spooled DMs the server retained while
        # we were offline). The callback may be sync or async; we
        # invoke it via maybe_await downstream.
        if self._on_welcome_cb is not None:
            try:
                await maybe_await(self._on_welcome_cb())
            except Exception:  # noqa: BLE001
                logger.warning("on_welcome callback failed", exc_info=True)

    async def send_chathistory(self, target: str, limit: int = 100) -> None:
        """Issue a per-nick CHATHISTORY drain request. Used by the
        bridge to pull spooled DMs after a reconnect."""
        await self._send_raw(f"CHATHISTORY {target} {limit}")

    async def send_chathistory_delete(self, msg_id: str) -> None:
        """Mark a spooled DM delivered (Phase 3.5 two-phase drain)."""
        await self._send_raw(f"CHATHISTORY DELETE {msg_id}")

    def _on_topic(self, msg: Message) -> None:
        """Handle TOPIC broadcasts (someone changed the topic)."""
        if len(msg.params) < 2:
            return
        channel = msg.params[0]
        topic = msg.params[1]
        sender = msg.prefix.split("!")[0] if msg.prefix else "server"
        if channel.startswith("#"):
            self.buffer.add(channel, sender, f"* Topic changed: {topic}")

    def _on_numeric_topic(self, msg: Message) -> None:
        """Handle 331 (no topic) and 332 (topic is...) replies."""
        if len(msg.params) < 2:
            return
        channel = msg.params[1]
        if not channel.startswith("#"):
            return
        if msg.command == "331":
            self.buffer.add(channel, "server", "* No topic is set")
        elif msg.command == "332" and len(msg.params) >= 3:
            self.buffer.add(channel, "server", f"* Topic: {msg.params[2]}")

    def _on_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2:
            return
        target = msg.params[0]
        text = msg.params[1]
        sender = msg.prefix.split("!")[0] if msg.prefix else "unknown"
        if sender == self.nick:
            return
        # Filter out server-emitted event notifications from system-<server>.
        # These are surfaced PRIVMSGs that announce mesh events (user.join,
        # agent.connect, server.link, etc.) — they are not conversation and
        # should not enter the agent's message buffer or trigger the poll loop.
        if sender.startswith(SYSTEM_USER_PREFIX):
            return
        # IRCv3 chathistory: a PRIVMSG carrying a ``batch=`` tag belongs
        # to a CHATHISTORY drain. Route it via the chathistory callback
        # (so the daemon can ack with msg_id) and skip the normal
        # mention/buffer routing — these are replays of a prior live
        # event, not a fresh inbound DM.
        batch_id = msg.tags.get("batch")
        msg_id = msg.tags.get("msgid")
        if batch_id and msg_id and self.on_chathistory_entry is not None:
            entry = {
                "msg_id": msg_id,
                "sender": sender,
                "recipient": target,
                "text": text,
                "tags": dict(msg.tags),
                "batch_id": batch_id,
            }
            try:
                self.on_chathistory_entry(entry)
            except Exception:  # noqa: BLE001
                logger.warning("on_chathistory_entry callback failed", exc_info=True)
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
        # Filter event NOTICEs from system-<server> for the same reason as PRIVMSG.
        if sender.startswith(SYSTEM_USER_PREFIX):
            return
        if target.startswith("#"):
            self.buffer.add(target, sender, text)

    def _on_history(self, msg: Message) -> None:
        """Handle HISTORY replay lines and populate the message buffer.

        Server sends: HISTORY <channel> <nick> <timestamp> <text>
        These arrive after a HISTORY RECENT request (issued on join_channel)
        and backfill the buffer with pre-existing channel messages.
        """
        if len(msg.params) < 4:
            return
        channel = msg.params[0]
        nick = msg.params[1]
        # msg.params[2] is the timestamp (string); we don't use it — buffer
        # records its own arrival time, which is fine for cursor-based reads.
        text = msg.params[3]
        # Skip own messages so the agent does not re-process its prior output.
        if nick == self.nick:
            return
        # Skip system-user entries (same filter as _on_privmsg).
        if nick.startswith(SYSTEM_USER_PREFIX):
            return
        self.buffer.add(channel, nick, text)

    def _on_historyend(self, msg: Message) -> None:
        """HISTORYEND is a sentinel — no action needed."""
        pass

    # ------------------------------------------------------------------
    # v8.19.42 — confirmation-based channel membership tracking
    # ------------------------------------------------------------------

    def _own_nick_from_prefix(self, msg: Message) -> bool:
        """True iff the message's prefix is THIS client's own nick."""
        if not msg.prefix:
            return False
        nick = msg.prefix.split("!", 1)[0]
        return nick == self.nick

    def _on_join(self, msg: Message) -> None:
        """Server echoes a JOIN — record our own confirmed memberships.

        The IRC server forwards JOINs to every channel member after it
        has admitted the joiner. When the echo's prefix is OUR nick, the
        join was accepted; only then do we mark the channel as joined.
        Joins by other nicks are ignored here (they're tracked elsewhere
        when needed).
        """
        if not self._own_nick_from_prefix(msg):
            return
        if not msg.params:
            return
        channel = msg.params[0]
        if not channel.startswith("#"):
            return
        if channel not in self.channels:
            self.channels.append(channel)
            logger.debug("%s confirmed JOIN to %s", self.nick, channel)

    def _on_part(self, msg: Message) -> None:
        """Server echoes a PART — remove our own confirmed membership."""
        if not self._own_nick_from_prefix(msg):
            return
        if not msg.params:
            return
        channel = msg.params[0]
        if channel in self.channels:
            self.channels.remove(channel)
            logger.debug("%s confirmed PART from %s", self.nick, channel)

    def _on_kick(self, msg: Message) -> None:
        """Server kick — if the kicked nick is us, drop the channel."""
        if len(msg.params) < 2:
            return
        channel, kicked_nick = msg.params[0], msg.params[1]
        if kicked_nick != self.nick:
            return
        if channel in self.channels:
            self.channels.remove(channel)
            logger.warning("%s was KICKed from %s", self.nick, channel)

    def _on_cannotsendtochan(self, msg: Message) -> None:
        """404 ERR_CANNOTSENDTOCHAN — we tried to PRIVMSG a channel we
        aren't actually in. Pre-v8.19.42 this was silently ignored; now
        we drop the (likely-stale) optimistic membership and log loudly
        so the operator sees the silent-drop class of bug.
        """
        if len(msg.params) < 2:
            return
        # params: [our_nick, channel, "Cannot send to channel"]
        channel = msg.params[1]
        if channel in self.channels:
            self.channels.remove(channel)
        logger.warning(
            "%s sent PRIVMSG to %s but server returned ERR_CANNOTSENDTOCHAN — "
            "the message was DROPPED. The client was not a confirmed member. "
            "Re-JOIN the channel and retry.",
            self.nick,
            channel,
        )

    def _on_bannedfromchan(self, msg: Message) -> None:
        """474 ERR_BANNEDFROMCHAN — JOIN refused (e.g. task-channel ACL).
        Make sure the channel is NOT in our optimistic membership."""
        if len(msg.params) < 2:
            return
        channel = msg.params[1]
        if channel in self.channels:
            self.channels.remove(channel)
        logger.warning(
            "%s JOIN to %s refused by server (ERR_BANNEDFROMCHAN). "
            "Verify the task-channel ACL recognizes this nick as the "
            "channel's worker or its supervising boss.",
            self.nick,
            channel,
        )

    def _on_server_error(self, msg: Message) -> None:
        """Server-initiated ERROR (typically disconnect-impending).
        Log loudly so a silent drop isn't lost in the noise."""
        body = msg.params[0] if msg.params else "<no body>"
        logger.warning("%s received server ERROR: %s", self.nick, body)

    def _on_roominvite(self, msg: Message) -> None:
        if len(msg.params) < 3:
            return
        channel = msg.params[0]
        meta_text = msg.params[2]
        if self.on_roominvite:
            self.on_roominvite(channel, meta_text)

    # ------------------------------------------------------------------
    # IRCv3 draft/chathistory inbound handlers (Phase 3)
    # ------------------------------------------------------------------

    def _on_batch(self, msg: Message) -> None:
        """Track open/close of CHATHISTORY batches. ``BATCH +id type ...``
        opens, ``BATCH -id`` closes."""
        if not msg.params:
            return
        head = msg.params[0]
        if head.startswith("+"):
            self._current_batch_id = head[1:]
        elif head.startswith("-"):
            self._current_batch_id = None

    def _on_chathistory(self, msg: Message) -> None:
        """Sentinel handler for ``CHATHISTORY END <target>`` and
        ``CHATHISTORY DELETE <id> OK|FAIL``. PRIVMSG-shaped history
        entries are routed via the normal ``_on_privmsg`` plus the
        IRCv3 batch tag — but since this transport reuses ``_handle``
        for tag extraction without a per-tag projection, the IRCd's
        chathistory skill emits PRIVMSGs that look identical to live
        DMs. The skill includes a ``msgid=`` IRCv3 tag we forward to
        the daemon for the ack round trip.
        """
        if not msg.params:
            return
        head = msg.params[0].upper()
        if head == "END":
            target = msg.params[1] if len(msg.params) > 1 else ""
            if self.on_chathistory_end is not None:
                try:
                    self.on_chathistory_end(target)
                except Exception:  # noqa: BLE001
                    logger.warning("on_chathistory_end callback failed", exc_info=True)
        # DELETE replies are informational; ignore on the transport
        # layer (the daemon doesn't await per-delete responses today).
