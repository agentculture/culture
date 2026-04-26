from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Callable

from culture.aio import maybe_await
from culture.clients.acp.message_buffer import MessageBuffer
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

    from culture.clients.acp.telemetry import HarnessMetricsRegistry

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
        backend: str = "acp",
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
        # Inject W3C traceparent as an IRCv3 tag prefix when a span is active.
        # Only inject when we have a tracer configured (fast-path: no work if
        # self._tracer is None) and there is no tag block already on the line
        # (defensive guard — prevents double-tagging if a caller pre-tagged).
        if self._tracer is not None and not line.startswith("@"):
            tp = current_traceparent()
            if tp:
                line = f"@{TRACEPARENT_TAG}={tp} {line}"
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
        self._route_to_buffer(target, sender, text)
        self._detect_and_fire_mention(target, sender, text)

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

    def _on_roominvite(self, msg: Message) -> None:
        if len(msg.params) < 3:
            return
        channel = msg.params[0]
        meta_text = msg.params[2]
        if self.on_roominvite:
            self.on_roominvite(channel, meta_text)
