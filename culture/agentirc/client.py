# server/client.py
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING

from opentelemetry import trace as _otel_trace
from opentelemetry.context import Context as _OtelContext
from opentelemetry.trace import Span as _OtelSpan

from culture.agentirc.channel import Channel
from culture.agentirc.skill import Event, EventType
from culture.aio import maybe_await
from culture.constants import SYSTEM_USER_PREFIX
from culture.protocol import replies
from culture.protocol.message import Message
from culture.telemetry.context import TRACEPARENT_TAG as _TP_TAG_NAME
from culture.telemetry.context import (
    context_from_traceparent,
    current_traceparent,
    extract_traceparent_from_tags,
)
from culture.telemetry.context import inject_traceparent as _inject_traceparent

# OTEL instrumentation name (must match `_CULTURE_TRACER_NAME` in
# culture/telemetry/tracing.py so trace consumers see one consistent value).
_TRACER_NAME = "culture.agentirc"
# Span attribute keys, defined once so a future rename / sanitization layer
# has one edit point.
_ATTR_BODY = "irc.message.body"
_ATTR_SIZE = "irc.message.size"
_ATTR_NICK = "irc.client.nick"
_ATTR_CHANNEL = "irc.channel"


if TYPE_CHECKING:
    from culture.agentirc.ircd import IRCd


class Client:
    """A connected IRC client."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        server: IRCd,
    ):
        self.reader = reader
        self.writer = writer
        self.server = server
        self.nick: str | None = None
        self.user: str | None = None
        self.realname: str | None = None
        self.host: str = writer.get_extra_info("peername", ("unknown", 0))[0]
        self.channels: set[Channel] = set()
        self._registered = False
        self.tags: list[str] = []
        self.caps: set[str] = set()
        self.modes: set[str] = set()
        self.icon: str | None = None
        self._session_span: _OtelSpan | None = None

    @property
    def prefix(self) -> str:
        return f"{self.nick}!{self.user}@{self.host}"

    async def send(self, message: Message) -> None:
        # Only inject trace context for clients that negotiated IRCv3
        # message-tags; otherwise older clients would see an unexpected @-tag
        # block and `send_tagged`'s tag-stripping for non-capable clients
        # would be undone here.
        if "message-tags" in self.caps:
            tp = current_traceparent()
            if tp is not None:
                _inject_traceparent(message, traceparent=tp, tracestate=None)
        try:
            wire = message.format().encode("utf-8")
            self.writer.write(wire)
            await self.writer.drain()
            # Record bytes after a successful drain so we don't count
            # writes that immediately faulted.
            self.server.metrics.irc_bytes_sent.add(len(wire), {"direction": "s2c"})
        except OSError:
            pass  # Client disconnected; cleanup happens in ircd._handle_connection

    async def send_raw(self, line: str) -> None:
        """Write a pre-formatted IRC line to the client socket.

        Appends CRLF internally, matching ServerLink.send_raw convention.
        Injects `culture.dev/traceparent` as an IRCv3 tag when a span is active
        AND the client negotiated the `message-tags` capability.
        """
        if "message-tags" in self.caps:
            tp = current_traceparent()
            if tp is not None:
                # send_raw takes a pre-formatted line without an existing tag
                # block; prefix a fresh @tag.
                line = f"@{_TP_TAG_NAME}={tp} {line}"
        try:
            wire = f"{line}\r\n".encode("utf-8")
            self.writer.write(wire)
            await self.writer.drain()
            self.server.metrics.irc_bytes_sent.add(len(wire), {"direction": "s2c"})
        except OSError:
            pass  # Client disconnected; cleanup happens in ircd._handle_connection

    async def send_tagged(self, msg: Message) -> None:
        """Send a Message, stripping tags for clients that haven't negotiated message-tags."""
        if msg.tags and "message-tags" not in self.caps:
            msg = Message(
                tags={},
                prefix=msg.prefix,
                command=msg.command,
                params=list(msg.params),
            )
        await self.send(msg)

    async def send_numeric(self, code: str, *params: str) -> None:
        target = self.nick or "*"
        msg = Message(
            prefix=self.server.config.name,
            command=code,
            params=[target, *params],
        )
        await self.send(msg)

    async def _process_buffer(self, buffer: str) -> str:
        """Parse and dispatch all complete lines from buffer, return remainder."""
        # Per-call get_tracer: test fixture swaps provider between tests.
        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.client.process_buffer"
        ) as span:
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = Message.parse(line)
                except Exception as exc:  # noqa: BLE001 -- widen for any parser failure
                    span.add_event(
                        "irc.parse_error",
                        attributes={
                            "line_preview": line[:64],
                            "error": type(exc).__name__,
                        },
                    )
                    continue
                # Record received bytes + message size for every successfully-parsed
                # line.  +2 accounts for the \r\n that was stripped during line-split.
                line_bytes = len(line.encode("utf-8")) + 2
                self.server.metrics.irc_bytes_received.add(line_bytes, {"direction": "c2s"})
                self.server.metrics.irc_message_size.record(
                    line_bytes, {"verb": msg.command, "direction": "c2s"}
                )
                if msg.command:
                    await self._dispatch(msg)
            return buffer

    async def handle(self, initial_msg: str | None = None) -> None:
        peer_info = self.writer.get_extra_info("peername")
        remote_addr = f"{peer_info[0]}:{peer_info[1]}" if peer_info else ""
        kind = "human"  # Plan 5/6 will refine to bot/harness
        self.server.metrics.clients_connected.add(1, {"kind": kind})
        session_started = time.perf_counter()
        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.client.session",
            attributes={"irc.client.remote_addr": remote_addr},
        ) as span:
            self._session_span = span
            try:
                buffer = ""
                if initial_msg:
                    buffer = initial_msg.replace("\r\n", "\n").replace("\r", "\n")
                    buffer = await self._process_buffer(buffer)
                while True:
                    data = await self.reader.read(4096)
                    if not data:
                        break
                    buffer += data.decode("utf-8", errors="replace")
                    # Cap buffer to prevent unbounded memory growth (512 bytes per RFC 2812)
                    if len(buffer) > 8192:
                        buffer = buffer[-4096:]
                    # Normalize all line endings to \n for simpler parsing
                    buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
                    buffer = await self._process_buffer(buffer)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                self.server.metrics.clients_connected.add(-1, {"kind": kind})
                self.server.metrics.client_session_duration.record(
                    time.perf_counter() - session_started, {"kind": kind}
                )

    async def _dispatch(self, msg: Message) -> None:
        extract = extract_traceparent_from_tags(msg, peer=None)
        self.server.metrics.trace_inbound.add(1, {"result": extract.status, "peer": ""})
        if extract.status == "valid":
            parent_ctx: _OtelContext | None = context_from_traceparent(extract.traceparent)
        else:
            parent_ctx = _OtelContext()  # force root: detach from session span

        verb = msg.command.upper()
        attrs = {
            "irc.command": verb,
            "irc.prefix_nick": (msg.prefix.split("!")[0] if msg.prefix else ""),
            "culture.trace.origin": "local" if extract.status == "missing" else "remote",
        }
        if extract.status in ("malformed", "too_long"):
            attrs["culture.trace.dropped_reason"] = extract.status

        # Per-call get_tracer: test fixture swaps provider between tests.
        cmd_started = time.perf_counter()
        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            f"irc.command.{verb}",
            context=parent_ctx,
            attributes=attrs,
        ):
            handler = getattr(self, f"_handle_{msg.command.lower()}", None)
            if handler:
                await maybe_await(handler(msg))
            else:
                skill = self.server.get_skill_for_command(msg.command)
                if skill and self._registered:
                    try:
                        await skill.on_command(self, msg)
                    except Exception:
                        logging.getLogger(__name__).exception(
                            "Skill %s failed on command %s", skill.name, msg.command
                        )
                else:
                    await self.send_numeric(
                        replies.ERR_UNKNOWNCOMMAND, msg.command, "Unknown command"
                    )
        self.server.metrics.client_command_duration.record(
            (time.perf_counter() - cmd_started) * 1000.0, {"verb": verb}
        )

    async def _handle_ping(self, msg: Message) -> None:
        token = msg.params[0] if msg.params else ""
        await self.send(
            Message(
                prefix=self.server.config.name,
                command="PONG",
                params=[self.server.config.name, token],
            )
        )

    def _handle_pong(self, msg: Message) -> None:
        pass  # Client responding to our ping

    async def _handle_cap(self, msg: Message) -> None:
        sub = msg.params[0].upper() if msg.params else ""
        if sub == "LS":
            await self.send_raw(
                f":{self.server.config.name} CAP {self.nick or '*'} LS :message-tags"
            )
        elif sub == "REQ":
            requested = msg.params[1].split() if len(msg.params) >= 2 else []
            supported = {"message-tags"}
            if all(cap in supported for cap in requested):
                self.caps.update(requested)
                await self.send_raw(
                    f":{self.server.config.name} CAP {self.nick or '*'}"
                    f" ACK :{' '.join(requested)}"
                )
            else:
                await self.send_raw(
                    f":{self.server.config.name} CAP {self.nick or '*'}"
                    f" NAK :{' '.join(requested)}"
                )
        elif sub == "END":
            pass  # no registration-gating in v1

    async def _handle_nick(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NONICKNAMEGIVEN, "No nickname given")
            return

        nick = msg.params[0]

        # Reject reserved system-* nick prefix
        if nick.startswith(SYSTEM_USER_PREFIX):
            await self.send_numeric(
                replies.ERR_ERRONEUSNICKNAME,
                nick,
                "Nickname prefix 'system-' is reserved",
            )
            return

        # Enforce server name prefix
        expected_prefix = f"{self.server.config.name}-"
        if not nick.startswith(expected_prefix):
            await self.send_numeric(
                replies.ERR_ERRONEUSNICKNAME,
                nick,
                f"Nickname must start with {expected_prefix}",
            )
            return

        if len(nick) <= len(expected_prefix):
            await self.send_numeric(
                replies.ERR_ERRONEUSNICKNAME,
                nick,
                f"Nickname must have an agent name after {expected_prefix}",
            )
            return

        if nick in self.server.clients:
            await self.send_numeric(replies.ERR_NICKNAMEINUSE, nick, "Nickname is already in use")
            return

        old_nick = self.nick
        if old_nick and old_nick in self.server.clients:
            del self.server.clients[old_nick]

        self.nick = nick
        self.server.clients[nick] = self
        if self._session_span is not None:
            self._session_span.set_attribute(_ATTR_NICK, nick)
        await self._try_register()

    async def _handle_user(self, msg: Message) -> None:
        if self._registered:
            await self.send_numeric(replies.ERR_ALREADYREGISTRED, "You may not reregister")
            return
        if len(msg.params) < 4:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "USER", replies.MSG_NEEDMOREPARAMS)
            return

        self.user = msg.params[0]
        self.realname = msg.params[3]
        await self._try_register()

    async def _try_register(self) -> None:
        if self.nick and self.user and not self._registered:
            self._registered = True
            await self._send_welcome()
            # Announce to linked peers
            for link in self.server.links.values():
                await link.send_raw(f"SNICK {self.nick} {self.user} {self.host} :{self.realname}")

    async def _send_welcome(self) -> None:
        await self.send_numeric(
            replies.RPL_WELCOME,
            f"Welcome to {self.server.config.name} IRC Network {self.prefix}",
        )
        await self.send_numeric(
            replies.RPL_YOURHOST,
            f"Your host is {self.server.config.name}, running culture",
        )
        await self.send_numeric(
            replies.RPL_CREATED,
            "This server was created today",
        )
        await self.send_numeric(
            replies.RPL_MYINFO,
            self.server.config.name,
            "culture",
            "o",
            "ov",
        )

    async def _handle_join(self, msg: Message) -> None:
        if not self._registered:
            return
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "JOIN", replies.MSG_NEEDMOREPARAMS)
            return

        channel_name = msg.params[0]
        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.join",
            attributes={_ATTR_CHANNEL: channel_name, _ATTR_NICK: self.nick or ""},
        ):
            if not channel_name.startswith("#"):
                return

            # Block joins to archived rooms
            existing = self.server.channels.get(channel_name)
            if existing and existing.archived:
                await self.send(
                    Message(
                        prefix=self.server.config.name,
                        command="NOTICE",
                        params=[self.nick, f"{channel_name} is archived and cannot be joined"],
                    )
                )
                return

            channel = self.server.get_or_create_channel(channel_name)
            if self in channel.members:
                return

            channel.add(self)
            self.channels.add(channel)

            # Notify all channel members (including self)
            join_msg = Message(prefix=self.prefix, command="JOIN", params=[channel_name])
            for member in list(channel.members):
                await member.send(join_msg)

            # Send topic if set
            if channel.topic:
                await self.send_numeric(replies.RPL_TOPIC, channel_name, channel.topic)

            # Send names list
            await self._send_names(channel)

            # Emit event AFTER delivering all join-related numerics (topic, NAMES)
            # so that the event PRIVMSG doesn't interleave with 353/366 in client buffers.
            await self.server.emit_event(
                Event(type=EventType.JOIN, channel=channel_name, nick=self.nick)
            )

    async def _handle_part(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "PART", replies.MSG_NEEDMOREPARAMS)
            return

        channel_name = msg.params[0]
        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.part",
            attributes={_ATTR_CHANNEL: channel_name, _ATTR_NICK: self.nick or ""},
        ):
            reason = msg.params[1] if len(msg.params) > 1 else ""

            channel = self.server.channels.get(channel_name)
            if not channel or self not in channel.members:
                await self.send_numeric(
                    replies.ERR_NOTONCHANNEL,
                    channel_name,
                    replies.MSG_NOTONCHANNEL,
                )
                return

            part_params = [channel_name, reason] if reason else [channel_name]
            part_msg = Message(prefix=self.prefix, command="PART", params=part_params)
            for member in list(channel.members):
                await member.send(part_msg)

            await self.server.emit_event(
                Event(
                    type=EventType.PART,
                    channel=channel_name,
                    nick=self.nick,
                    data={"reason": reason},
                )
            )

            channel.remove(self)
            self.channels.discard(channel)

            if not channel.members and not channel.persistent:
                del self.server.channels[channel_name]

    async def _handle_topic(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "TOPIC", replies.MSG_NEEDMOREPARAMS)
            return

        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel or self not in channel.members:
            await self.send_numeric(
                replies.ERR_NOTONCHANNEL,
                channel_name,
                replies.MSG_NOTONCHANNEL,
            )
            return

        if len(msg.params) == 1:
            # Query topic
            if channel.topic:
                await self.send_numeric(replies.RPL_TOPIC, channel_name, channel.topic)
            else:
                await self.send_numeric(replies.RPL_NOTOPIC, channel_name, "No topic is set")
        else:
            # Set topic
            channel.topic = msg.params[1]
            topic_msg = Message(
                prefix=self.prefix,
                command="TOPIC",
                params=[channel_name, channel.topic],
            )
            for member in list(channel.members):
                await member.send(topic_msg)
            await self.server.emit_event(
                Event(
                    type=EventType.TOPIC,
                    channel=channel_name,
                    nick=self.nick,
                    data={"topic": channel.topic},
                )
            )

    async def _handle_names(self, msg: Message) -> None:
        if not msg.params:
            return
        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if channel:
            await self._send_names(channel)

    async def _send_names(self, channel: Channel) -> None:
        nicks = " ".join(f"{channel.get_prefix(m)}{m.nick}" for m in channel.members)
        await self.send_numeric(replies.RPL_NAMREPLY, "=", channel.name, nicks)
        await self.send_numeric(replies.RPL_ENDOFNAMES, channel.name, "End of /NAMES list")

    async def _handle_list(self, msg: Message) -> None:
        for name, channel in self.server.channels.items():
            topic = channel.topic or ""
            await self.send_numeric(replies.RPL_LIST, name, str(len(channel.members)), topic)
        await self.send_numeric(replies.RPL_LISTEND, "End of LIST")

    async def _handle_mode(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.ERR_NEEDMOREPARAMS, "MODE", replies.MSG_NEEDMOREPARAMS)
            return

        target = msg.params[0]
        if target.startswith("#"):
            await self._handle_channel_mode(msg)
        else:
            await self._handle_user_mode(msg)

    def _apply_mode_r(self, channel, adding, applied_modes):
        if adding:
            channel.restricted = True
        else:
            channel.restricted = False
        applied_modes.append(("+" if adding else "-") + "R")

    def _apply_mode_s(self, channel, adding, param_value, applied_modes, applied_params):
        if adding:
            channel.shared_with.add(param_value)
        else:
            channel.shared_with.discard(param_value)
        applied_modes.append(("+" if adding else "-") + "S")
        applied_params.append(param_value)

    async def _apply_mode_membership(
        self, channel, channel_name, ch, adding, param_value, applied_modes, applied_params
    ):
        target_nick = param_value
        target_client = self.server.clients.get(target_nick)
        if not target_client or target_client not in channel.members:
            await self.send_numeric(
                replies.ERR_USERNOTINCHANNEL,
                target_nick,
                channel_name,
                "They aren't on that channel",
            )
            return
        if ch == "o":
            if adding:
                channel.operators.add(target_client)
            else:
                channel.operators.discard(target_client)
        elif ch == "v":
            if adding:
                channel.voiced.add(target_client)
            else:
                channel.voiced.discard(target_client)
        applied_modes.append(("+" if adding else "-") + ch)
        applied_params.append(target_nick)

    _PARAM_MODES = frozenset({"o", "v", "S"})

    async def _apply_mode_char(
        self,
        channel,
        channel_name: str,
        ch: str,
        adding: bool,
        param_queue: list[str],
        applied_modes: list[str],
        applied_params: list[str],
    ) -> None:
        """Apply a single mode character. Consumes one param from param_queue when needed."""
        if ch == "R":
            self._apply_mode_r(channel, adding, applied_modes)
            return
        if ch not in self._PARAM_MODES or not param_queue:
            return
        param_value = param_queue.pop(0)
        if ch == "S":
            self._apply_mode_s(channel, adding, param_value, applied_modes, applied_params)
        else:
            await self._apply_mode_membership(
                channel,
                channel_name,
                ch,
                adding,
                param_value,
                applied_modes,
                applied_params,
            )

    async def _broadcast_mode_change(
        self,
        channel,
        channel_name: str,
        applied_modes: list[str],
        applied_params: list[str],
    ) -> None:
        """Send the aggregated MODE message to all channel members."""
        if not applied_modes:
            return
        mode_msg = Message(
            prefix=self.prefix,
            command="MODE",
            params=[channel_name, "".join(applied_modes)] + applied_params,
        )
        for member in list(channel.members):
            await member.send(mode_msg)

    async def _handle_channel_mode(self, msg: Message) -> None:
        channel_name = msg.params[0]
        channel = self.server.channels.get(channel_name)
        if not channel:
            await self.send_numeric(
                replies.ERR_NOSUCHCHANNEL, channel_name, replies.MSG_NOSUCHCHANNEL
            )
            return

        if len(msg.params) == 1:
            await self.send_numeric(replies.RPL_CHANNELMODEIS, channel_name, "+")
            return

        if not channel.is_operator(self):
            await self.send_numeric(
                replies.ERR_CHANOPRIVSNEEDED,
                channel_name,
                "You're not channel operator",
            )
            return

        modestring = msg.params[1]
        param_queue = list(msg.params[2:])
        adding = True
        applied_modes: list[str] = []
        applied_params: list[str] = []
        for ch in modestring:
            if ch == "+":
                adding = True
            elif ch == "-":
                adding = False
            else:
                await self._apply_mode_char(
                    channel,
                    channel_name,
                    ch,
                    adding,
                    param_queue,
                    applied_modes,
                    applied_params,
                )

        # Auto-promote if no operators remain
        if not channel.operators and channel.members:
            channel.operators.add(min(channel.members, key=lambda m: m.nick))

        await self._broadcast_mode_change(channel, channel_name, applied_modes, applied_params)

    _VALID_USER_MODE_CHARS = frozenset("HABC")
    _USER_MODE_EDGE_EVENTS: dict[tuple[str, bool], EventType] = {
        ("A", True): EventType.AGENT_CONNECT,
        ("A", False): EventType.AGENT_DISCONNECT,
        ("C", True): EventType.CONSOLE_OPEN,
        ("C", False): EventType.CONSOLE_CLOSE,
    }

    def _apply_user_mode_char(self, ch: str, adding: bool) -> EventType | None:
        """Mutate ``self.modes`` for a single mode char and return the edge event, if any.

        Returns None if the char is unknown or the transition was a no-op
        (setting an already-set mode or clearing an already-clear one).
        """
        if ch not in self._VALID_USER_MODE_CHARS:
            return None
        had = ch in self.modes
        if adding:
            self.modes.add(ch)
        else:
            self.modes.discard(ch)
        if had == adding:
            return None
        return self._USER_MODE_EDGE_EVENTS.get((ch, adding))

    def _parse_mode_edges(self, modestring: str) -> list[EventType]:
        """Apply each char of ``modestring`` and collect the emitted edge events."""
        pending: list[EventType] = []
        adding = True
        for ch in modestring:
            if ch == "+":
                adding = True
            elif ch == "-":
                adding = False
            else:
                event = self._apply_user_mode_char(ch, adding)
                if event is not None:
                    pending.append(event)
        return pending

    async def _emit_user_mode_events(self, pending: list[EventType]) -> None:
        for event_type in pending:
            await self.server.emit_event(
                Event(
                    type=event_type,
                    channel=None,
                    nick=self.nick,
                    data={"nick": self.nick},
                )
            )

    async def _handle_user_mode(self, msg: Message) -> None:
        # Reject pre-registration so an unregistered socket cannot inject
        # agent.connect / console.open into #system by sending MODE after NICK
        # but before USER.
        if not self._registered:
            return
        target_nick = msg.params[0]
        if target_nick != self.nick:
            await self.send_numeric(
                replies.ERR_USERSDONTMATCH,
                "Can't change mode for other users",
            )
            return

        modestring = msg.params[1] if len(msg.params) > 1 else ""
        pending = self._parse_mode_edges(modestring)
        await self._emit_user_mode_events(pending)

        mode_str = "+" + "".join(sorted(self.modes)) if self.modes else "+"
        await self.send_numeric(replies.RPL_UMODEIS, mode_str)

    async def _send_to_channel(self, channel, target, relay, text, is_notice):
        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.privmsg.deliver.channel",
            attributes={
                "irc.channel": target,
                _ATTR_BODY: text,
                _ATTR_SIZE: len(text),
                "irc.notice": is_notice,
            },
        ):
            for member in list(channel.members):
                if member is not self:
                    await member.send(relay)
            self.server.metrics.privmsg_delivered.add(1, {"kind": "channel", "channel": target})
            event_data = {"text": text}
            if is_notice:
                event_data["notice"] = True
            await self.server.emit_event(
                Event(
                    type=EventType.MESSAGE,
                    channel=target,
                    nick=self.nick,
                    data=event_data,
                )
            )

    async def _send_to_client(self, target, relay, text, is_notice):
        from culture.agentirc.remote_client import RemoteClient

        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.privmsg.deliver.dm",
            attributes={
                "irc.target.nick": target,
                _ATTR_BODY: text,
                _ATTR_SIZE: len(text),
                "irc.notice": is_notice,
            },
        ):
            recipient = self.server.get_client(target)
            if not recipient:
                return False
            if isinstance(recipient, RemoteClient):
                s2s_cmd = "SNOTICE" if is_notice else "SMSG"
                await recipient.link.send_raw(
                    f":{self.server.config.name} {s2s_cmd} {target} {self.nick} :{text}"
                )
            else:
                await recipient.send(relay)
            self.server.metrics.privmsg_delivered.add(1, {"kind": "dm"})
            event_data = {"text": text, "target": target}
            if is_notice:
                event_data["notice"] = True
            await self.server.emit_event(
                Event(
                    type=EventType.MESSAGE,
                    channel=None,
                    nick=self.nick,
                    data=event_data,
                )
            )
            return True

    async def _handle_privmsg(self, msg: Message) -> None:
        if len(msg.params) < 2:
            await self.send_numeric(
                replies.ERR_NEEDMOREPARAMS, "PRIVMSG", replies.MSG_NEEDMOREPARAMS
            )
            return

        target = msg.params[0]
        text = msg.params[1]
        # Per-call get_tracer: test fixture swaps provider between tests.
        with _otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.privmsg.dispatch",
            attributes={
                "irc.target": target,
                _ATTR_BODY: text,
                _ATTR_SIZE: len(text),
            },
        ):
            relay = Message(prefix=self.prefix, command="PRIVMSG", params=[target, text])

            if target.startswith("#"):
                channel = self.server.channels.get(target)
                if not channel:
                    await self.send_numeric(
                        replies.ERR_NOSUCHCHANNEL, target, replies.MSG_NOSUCHCHANNEL
                    )
                    return
                if self not in channel.members:
                    await self.send_numeric(
                        replies.ERR_CANNOTSENDTOCHAN, target, "Cannot send to channel"
                    )
                    return
                await self._send_to_channel(channel, target, relay, text, False)
                await self._notify_mentions(target, text)
            else:
                found = await self._send_to_client(target, relay, text, False)
                if not found:
                    await self.send_numeric(replies.ERR_NOSUCHNICK, target, replies.MSG_NOSUCHNICK)
                    return
                await self._notify_mentions(None, text)

    async def _notify_mentions(self, channel_name: str | None, text: str) -> None:
        from culture.agentirc.remote_client import RemoteClient

        mentioned_nicks = re.findall(r"@(\S+)", text)
        if not mentioned_nicks:
            return
        seen: set[str] = set()
        channel = self.server.channels.get(channel_name) if channel_name else None
        source = channel_name or "a direct message"
        for raw_nick in mentioned_nicks:
            nick = raw_nick.rstrip(".,;:!?")
            if nick in seen or nick == self.nick:
                continue
            seen.add(nick)
            target_client = self.server.get_client(nick)
            if not target_client:
                continue
            if channel and target_client not in channel.members:
                continue
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[
                    nick,
                    f"{self.nick} mentioned you in {source}: {text}",
                ],
            )
            if isinstance(target_client, RemoteClient):
                # Send mention notice through S2S link
                await target_client.link.send_raw(
                    f":{self.server.config.name} SNOTICE {nick}"
                    f" {self.server.config.name}"
                    f" :{self.nick} mentioned you in {source}: {text}"
                )
            else:
                await target_client.send(notice)

    async def _handle_notice(self, msg: Message) -> None:
        # Same as PRIVMSG but no error replies per RFC 2812
        if len(msg.params) < 2:
            return

        target = msg.params[0]
        text = msg.params[1]
        relay = Message(prefix=self.prefix, command="NOTICE", params=[target, text])

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if not channel:
                return
            if self not in channel.members:
                return
            await self._send_to_channel(channel, target, relay, text, True)
        else:
            await self._send_to_client(target, relay, text, True)

    def _build_who_flags(self, member, channel) -> str:
        flags = "H"
        if channel and channel.is_operator(member):
            flags += "@"
        elif channel and channel.is_voiced(member):
            flags += "+"
        if hasattr(member, "modes") and member.modes:
            flags += "[" + "".join(sorted(member.modes)) + "]"
        if hasattr(member, "icon") and member.icon:
            flags += "{" + member.icon + "}"
        return flags

    async def _send_who_reply(self, member, channel_name: str, channel=None) -> None:
        from culture.agentirc.remote_client import RemoteClient  # noqa: F811

        flags = self._build_who_flags(member, channel)
        server_name = (
            member.server_name if isinstance(member, RemoteClient) else self.server.config.name
        )
        await self.send_numeric(
            replies.RPL_WHOREPLY,
            channel_name,
            member.user or "*",
            member.host,
            server_name,
            member.nick,
            flags,
            f"0 {member.realname or ''}",
        )

    async def _handle_who(self, msg: Message) -> None:
        if not msg.params:
            await self.send_numeric(replies.RPL_ENDOFWHO, "*", replies.MSG_ENDOFWHO)
            return

        target = msg.params[0]
        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if channel:
                for member in list(channel.members):
                    await self._send_who_reply(member, target, channel)
            await self.send_numeric(replies.RPL_ENDOFWHO, target, replies.MSG_ENDOFWHO)
        else:
            client = self.server.get_client(target)
            if client:
                chan_name = "*"
                chan_context = None
                for ch in client.channels:
                    chan_name = ch.name
                    chan_context = ch
                    break
                await self._send_who_reply(client, chan_name, chan_context)
            await self.send_numeric(replies.RPL_ENDOFWHO, target, replies.MSG_ENDOFWHO)

    async def _handle_whois(self, msg: Message) -> None:
        from culture.agentirc.remote_client import RemoteClient

        if not msg.params:
            await self.send_numeric(replies.ERR_NONICKNAMEGIVEN, "No nickname given")
            return

        target_nick = msg.params[0]
        target = self.server.get_client(target_nick)
        if not target:
            await self.send_numeric(replies.ERR_NOSUCHNICK, target_nick, "No such nick/channel")
            await self.send_numeric(replies.RPL_ENDOFWHOIS, target_nick, "End of WHOIS list")
            return

        await self.send_numeric(
            replies.RPL_WHOISUSER,
            target.nick,
            target.user or "*",
            target.host,
            "*",
            target.realname or "",
        )
        server_name = (
            target.server_name if isinstance(target, RemoteClient) else self.server.config.name
        )
        await self.send_numeric(
            replies.RPL_WHOISSERVER,
            target.nick,
            server_name,
            "culture",
        )
        if target.channels:
            chan_list = " ".join(f"{ch.get_prefix(target)}{ch.name}" for ch in target.channels)
            await self.send_numeric(replies.RPL_WHOISCHANNELS, target.nick, chan_list)
        await self.send_numeric(replies.RPL_ENDOFWHOIS, target.nick, "End of WHOIS list")

    async def _handle_quit(self, msg: Message) -> None:
        reason = msg.params[0] if msg.params else "Quit"
        quit_msg = Message(prefix=self.prefix, command="QUIT", params=[reason])

        notified: set[Client] = set()
        channel_names = [ch.name for ch in self.channels]
        for channel in list(self.channels):
            for member in list(channel.members):
                if member is not self and member not in notified:
                    await member.send(quit_msg)
                    notified.add(member)

        await self.server.emit_event(
            Event(
                type=EventType.QUIT,
                channel=None,
                nick=self.nick,
                data={"reason": reason, "channels": channel_names},
            )
        )

        raise ConnectionError("Client quit")
