# server/server_link.py
from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import TYPE_CHECKING

from opentelemetry import trace as otel_trace

from culture.agentirc.remote_client import RemoteClient
from culture.agentirc.skill import Event, EventType
from culture.aio import maybe_await
from culture.bots.virtual_client import VirtualClient
from culture.constants import SYSTEM_USER_PREFIX
from culture.protocol.message import Message
from culture.telemetry import context_from_traceparent, current_traceparent
from culture.telemetry.context import TRACEPARENT_TAG, extract_traceparent_from_tags

# OTEL instrumentation name (must match `_CULTURE_TRACER_NAME` in
# culture/telemetry/tracing.py so all spans go through one tracer instance).
_TRACER_NAME = "culture.agentirc"

if TYPE_CHECKING:
    from culture.agentirc.ircd import IRCd

logger = logging.getLogger(__name__)


def _prepend_trace_tags(line: str, tp: str) -> str:
    """Inject *tp* as the ``culture.dev/traceparent`` IRCv3 tag on *line*.

    - Empty line → returned unchanged (defensive no-op).
    - Line with no existing tag block (does not start with ``@``) → prepend
      ``@culture.dev/traceparent=<tp> `` before the rest of the line.
    - Line that already has a tag block (starts with ``@``) → merge the tag
      into the existing block.  If the block already contains
      ``culture.dev/traceparent``, its value is replaced with *tp*; otherwise
      the new tag is appended with a ``;`` separator.

    The helper is intentionally lenient: if the tag block is somehow
    ill-formed the existing text is preserved and the new tag is appended —
    tagging is best-effort, never load-bearing.

    .. note:: IRCv3-escape limitation

        This helper assumes the existing tag block contains no IRCv3-escaped
        ``;`` or ``=`` characters in tag *values* (RFC-compliant values use
        ``\\:`` and ``\\=`` escapes rather than bare characters).  This
        assumption is safe for all current ``send_raw`` callers, which today
        emit no tags of their own.  Revisit if a caller begins authoring tags
        whose values contain literal ``;`` or ``=`` sequences.
    """
    if not line:
        return line

    if not line.startswith("@"):
        return f"@{TRACEPARENT_TAG}={tp} {line}"

    # Split off the tag block: everything up to the first space, then the rest.
    space_idx = line.find(" ")
    if space_idx == -1:
        # Malformed: entire line is a tag block with no message body.
        # Append the tag and move on.
        return f"{line};{TRACEPARENT_TAG}={tp}"

    tag_block = line[1:space_idx]  # strip leading '@'
    rest = line[space_idx + 1 :]

    # Split existing tags on ';', drop empty entries (e.g. from an empty tag
    # block like "@ :rest"), then replace or append the traceparent tag.
    tags = [t for t in tag_block.split(";") if t]
    replaced = False
    new_tags = []
    for tag in tags:
        key = tag.split("=", 1)[0]
        if key == TRACEPARENT_TAG:
            new_tags.append(f"{TRACEPARENT_TAG}={tp}")
            replaced = True
        else:
            new_tags.append(tag)
    if not replaced:
        new_tags.append(f"{TRACEPARENT_TAG}={tp}")

    return f"@{';'.join(new_tags)} {rest}"


class ServerLink:
    """A server-to-server link to a peer IRCd."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        server: IRCd,
        password: str | None,
        *,
        initiator: bool = False,
        trust: str = "full",
    ):
        self.reader = reader
        self.writer = writer
        self.server = server
        self.password = password
        self.initiator = initiator
        self.trust = trust
        self.peer_name: str | None = None
        self.peer_description: str = ""
        self._authenticated = False
        self._got_pass = False
        self._got_server = False
        self._peer_pass: str | None = None
        self.last_seen_seq: int = 0
        self._squit_received: bool = False
        self._session_span: otel_trace.Span | None = None

    def should_relay(self, channel_name: str) -> bool:
        """Check if a channel event should be relayed over this link."""
        channel = self.server.channels.get(channel_name)
        if channel is None:
            return False
        if channel.restricted:
            return False
        if self.trust == "full":
            return True
        if self.trust == "restricted":
            return self.peer_name in channel.shared_with
        return False

    async def send_raw(self, line: str) -> None:
        tp = current_traceparent()
        if tp:
            try:
                line = _prepend_trace_tags(line, tp)
            except Exception:  # noqa: BLE001 - telemetry must never break the link
                logger.debug("traceparent injection failed; sending untagged", exc_info=True)
        try:
            self.writer.write(f"{line}\r\n".encode("utf-8"))
            await self.writer.drain()
        except OSError:
            pass

    async def send(self, message: Message) -> None:
        try:
            self.writer.write(message.format().encode("utf-8"))
            await self.writer.drain()
        except OSError:
            pass

    async def _process_buffer(self, buffer: str) -> str:
        """Parse and dispatch all complete lines from *buffer*, return remainder."""
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            if line.strip():
                msg = Message.parse(line)
                if msg.command:
                    await self._dispatch(msg)
        return buffer

    async def handle(self, initial_msg: str | None = None) -> None:
        """Main S2S connection loop."""
        tracer = otel_trace.get_tracer(_TRACER_NAME)
        direction = "outbound" if self.initiator else "inbound"
        with tracer.start_as_current_span(
            "irc.s2s.session",
            attributes={"s2s.direction": direction},
        ) as span:
            self._session_span = span
            try:
                if self.initiator:
                    await self._send_handshake()

                buffer = ""
                if initial_msg:
                    buffer = initial_msg + "\n"
                    buffer = await self._process_buffer(buffer)

                while True:
                    data = await self.reader.read(4096)
                    if not data:
                        break
                    buffer += data.decode("utf-8", errors="replace")
                    buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")
                    buffer = await self._process_buffer(buffer)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                await self.server._remove_link(self, squit=self._squit_received)
                self.writer.close()
                try:
                    await self.writer.wait_closed()
                except ConnectionError:
                    pass

    async def _send_handshake(self) -> None:
        await self.send_raw(f"PASS {self.password}")
        await self.send_raw(f"SERVER {self.server.config.name} 1 :{self.server.config.name} IRC")

    async def _dispatch(self, msg: Message) -> None:
        verb = msg.command.upper()
        handler = getattr(self, f"_handle_{msg.command.lower()}", None)

        extracted = extract_traceparent_from_tags(msg, peer=self.peer_name)
        parent_ctx = None
        if extracted.status == "valid":
            parent_ctx = context_from_traceparent(extracted.traceparent)

        attrs = {
            "irc.command": verb,
            "culture.trace.origin": "remote",
            "culture.federation.peer": self.peer_name or "",
        }
        if extracted.status in ("malformed", "too_long"):
            attrs["culture.trace.dropped_reason"] = extracted.status

        with otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            f"irc.s2s.{verb}",
            context=parent_ctx,
            attributes=attrs,
        ):
            if handler:
                await maybe_await(handler(msg))

    # --- Handshake handlers ---

    async def _handle_pass(self, msg: Message) -> None:
        if not msg.params:
            return
        self._peer_pass = msg.params[0]
        self._got_pass = True
        await self._try_complete_handshake()

    async def _handle_server(self, msg: Message) -> None:
        if not msg.params:
            return
        self.peer_name = msg.params[0]
        if len(msg.params) >= 3:
            self.peer_description = msg.params[2]
        self._got_server = True
        await self._try_complete_handshake()

    async def _find_link_config(self, peer_name: str) -> None:
        """Look up link config for an inbound peer and apply password/trust.

        Sends ERROR and raises ConnectionError if no config is found.
        """
        link_config = None
        for lc in self.server.config.links:
            if lc.name == peer_name:
                link_config = lc
                break
        if not link_config:
            logger.warning("No link config for peer %s", peer_name)
            await self.send_raw(f"ERROR :No link configured for {peer_name}")
            raise ConnectionError(f"No link config for {peer_name}")
        self.password = link_config.password
        self.trust = link_config.trust

    async def _validate_peer_credentials(self) -> None:
        """Check password, duplicate name, and self-link.

        Sends ERROR and raises ConnectionError on any failure.
        """
        if self._peer_pass != self.password:
            logger.warning("Bad password from peer %s", self.peer_name)
            await self.send_raw("ERROR :Bad password")
            raise ConnectionError("Bad S2S password")

        # Check for duplicate server name
        if self.peer_name in self.server.links:
            logger.warning("Duplicate server name %s", self.peer_name)
            await self.send_raw(f"ERROR :Server name {self.peer_name} already linked")
            raise ConnectionError("Duplicate server name")

        if self.peer_name == self.server.config.name:
            logger.warning("Peer has same name as us: %s", self.peer_name)
            await self.send_raw("ERROR :Cannot link to self")
            raise ConnectionError("Cannot link to self")

    async def _try_complete_handshake(self) -> None:
        if not (self._got_pass and self._got_server):
            return

        # For inbound links, look up expected password and trust by peer name
        if not self.initiator and self.password is None:
            await self._find_link_config(self.peer_name)

        await self._validate_peer_credentials()

        self._authenticated = True
        if self._session_span is not None:
            self._session_span.set_attribute("s2s.peer", self.peer_name)
        self.server.links[self.peer_name] = self
        self.server.cancel_link_retry(self.peer_name)

        # Restore last seen seq from previous link sessions
        self.last_seen_seq = self.server._peer_acked_seq.get(self.peer_name, 0)

        if not self.initiator:
            await self._send_handshake()

        await self.send_burst()
        await self._send_backfill_request()

        # Emit server.link so the mesh knows this link is established.
        # v1 assumes all peers support SEVENT; cap negotiation is deferred — see plan task 12.
        await self.server.emit_event(
            Event(
                type=EventType.SERVER_LINK,
                channel=None,
                nick=f"{SYSTEM_USER_PREFIX}{self.server.config.name}",
                data={"peer": self.peer_name, "trust": self.trust},
            )
        )

    # --- Burst handlers ---

    async def _send_burst_topics(self, channels: list) -> None:
        """Send channel topics for relayable channels."""
        for channel in channels:
            if channel.topic:
                local_members = [m for m in channel.members if not isinstance(m, RemoteClient)]
                setter = local_members[0].nick if local_members else self.server.config.name
                await self.send_raw(f"STOPIC {channel.name} {setter} :{channel.topic}")

    async def _send_burst_metadata(self, channels: list) -> None:
        """Send room metadata for managed relayable channels."""
        import json

        for channel in channels:
            if channel.is_managed:
                meta = json.dumps(
                    {
                        "room_id": channel.room_id,
                        "name": channel.name,
                        "creator": channel.creator,
                        "owner": channel.owner,
                        "purpose": channel.purpose,
                        "instructions": channel.instructions,
                        "tags": channel.tags,
                        "persistent": channel.persistent,
                        "agent_limit": channel.agent_limit,
                        "extra_meta": channel.extra_meta,
                        "created_at": channel.created_at,
                    }
                )
                await self.send_raw(f"SROOMMETA {channel.name} :{meta}")

    async def send_burst(self) -> None:
        """Send our local state to the peer."""
        # Send all local clients (skip pseudo-users — they are server-local)
        for client in self.server.clients.values():
            if isinstance(client, VirtualClient):
                continue
            await self.send_raw(
                f"SNICK {client.nick} {client.user} {client.host} :{client.realname}"
            )

        # Collect relayable channels once
        relayable = [ch for ch in self.server.channels.values() if self.should_relay(ch.name)]

        # Send channel membership
        for channel in relayable:
            local_nicks = [m.nick for m in channel.members if not isinstance(m, RemoteClient)]
            if local_nicks:
                nicks_str = " ".join(local_nicks)
                await self.send_raw(f"SJOIN {channel.name} {nicks_str}")

        # Send channel topics
        await self._send_burst_topics(relayable)

        # Send room metadata for managed rooms
        await self._send_burst_metadata(relayable)

    def _handle_snick(self, msg: Message) -> None:
        if len(msg.params) < 4:
            return
        nick, user, host = msg.params[0], msg.params[1], msg.params[2]
        realname = msg.params[3]

        # Reject reserved nick prefix
        if nick.startswith(SYSTEM_USER_PREFIX):
            logger.warning("Rejecting reserved nick %r from peer %s", nick, self.peer_name)
            return

        # Validate nick conforms to <peer_name>-<agent> format
        expected_prefix = f"{self.peer_name}-"
        if not nick.startswith(expected_prefix):
            logger.warning("Rejected remote nick %s: must start with %s", nick, expected_prefix)
            return

        if nick in self.server.clients or nick in self.server.remote_clients:
            return  # Already known

        rc = RemoteClient(
            nick=nick,
            user=user,
            host=host,
            realname=realname,
            server_name=self.peer_name,
            link=self,
        )
        self.server.remote_clients[nick] = rc

    def _check_incoming_trust(self, channel_name: str) -> bool:
        """Return True if we should accept incoming data for this channel."""
        existing = self.server.channels.get(channel_name)
        if existing:
            if existing.restricted:
                return False
            if self.trust == "restricted" and self.peer_name not in existing.shared_with:
                return False
        else:
            if self.trust == "restricted":
                return False
        return True

    async def _join_remote_nick(self, nick: str, channel, channel_name: str) -> None:
        """Process a single remote nick joining a channel."""
        rc = self.server.remote_clients.get(nick)
        if not rc or rc in channel.members:
            return
        channel.members.add(rc)
        rc.channels.add(channel)

        if self._authenticated:
            join_msg = Message(prefix=rc.prefix, command="JOIN", params=[channel_name])
            for member in list(channel.members):
                if not isinstance(member, RemoteClient):
                    await member.send(join_msg)

    async def _handle_sjoin(self, msg: Message) -> None:
        if len(msg.params) < 2:
            return

        channel_name = msg.params[0]
        nicks = msg.params[1:]

        if not self._check_incoming_trust(channel_name):
            return

        channel = self.server.get_or_create_channel(channel_name)

        for nick in nicks:
            await self._join_remote_nick(nick, channel, channel_name)

    def _handle_stopic(self, msg: Message) -> None:
        if len(msg.params) < 3:
            return
        channel_name = msg.params[0]
        _nick = msg.params[1]
        topic = msg.params[2]

        channel = self.server.channels.get(channel_name)
        if channel:
            # Check incoming trust
            if channel.restricted:
                return
            if self.trust == "restricted" and self.peer_name not in channel.shared_with:
                return
            channel.topic = topic

    # --- Real-time relay handlers (incoming from peer) ---

    async def _relay_to_channel(
        self,
        channel,
        relay: Message,
        target: str,
        sender_nick: str,
        text: str,
        *,
        notify_mentions: bool = False,
    ) -> None:
        """Deliver a relayed peer message to local channel members and emit events."""
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(relay)
        await self.server.emit_event(
            Event(
                type=EventType.MESSAGE,
                channel=target,
                nick=sender_nick,
                data={"text": text, "_origin": self.peer_name},
            )
        )
        if notify_mentions:
            await self._notify_remote_mentions(target, sender_nick, text)

    async def _relay_to_dm(
        self,
        relay: Message,
        target: str,
        sender_nick: str,
        text: str,
        *,
        notify_mentions: bool = False,
        emit_dm_event: bool = False,
    ) -> None:
        """Deliver a relayed peer message to a local DM recipient and emit events."""
        local = self.server.clients.get(target)
        if not local:
            return
        await local.send(relay)
        if emit_dm_event:
            await self.server.emit_event(
                Event(
                    type=EventType.MESSAGE,
                    channel=None,
                    nick=sender_nick,
                    data={"text": text, "_origin": self.peer_name},
                )
            )
        if notify_mentions:
            await self._notify_remote_mentions(None, sender_nick, text)

    async def _relay_peer_message(
        self,
        target: str,
        sender_nick: str,
        text: str,
        command: str,
        *,
        notify_mentions: bool = False,
        emit_dm_event: bool = False,
    ) -> None:
        """Route an incoming peer message/notice to local clients and emit events."""
        # Check incoming trust for channel targets
        if target.startswith("#") and not self._check_incoming_trust(target):
            return

        rc = self.server.remote_clients.get(sender_nick)
        prefix = rc.prefix if rc else f"{sender_nick}!*@*"
        relay = Message(prefix=prefix, command=command, params=[target, text])

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if channel:
                await self._relay_to_channel(
                    channel,
                    relay,
                    target,
                    sender_nick,
                    text,
                    notify_mentions=notify_mentions,
                )
        else:
            await self._relay_to_dm(
                relay,
                target,
                sender_nick,
                text,
                notify_mentions=notify_mentions,
                emit_dm_event=emit_dm_event,
            )

    async def _handle_smsg(self, msg: Message) -> None:
        """Handle relayed PRIVMSG from peer."""
        if len(msg.params) < 3:
            return
        await self._relay_peer_message(
            msg.params[0],
            msg.params[1],
            msg.params[2],
            "PRIVMSG",
            notify_mentions=True,
            emit_dm_event=True,
        )

    async def _handle_snotice(self, msg: Message) -> None:
        """Handle relayed NOTICE from peer."""
        if len(msg.params) < 3:
            return
        await self._relay_peer_message(
            msg.params[0],
            msg.params[1],
            msg.params[2],
            "NOTICE",
            notify_mentions=False,
        )

    async def _handle_spart(self, msg: Message) -> None:
        """Handle relayed PART from peer."""
        if len(msg.params) < 2:
            return
        channel_name = msg.params[0]
        nick = msg.params[1]
        reason = msg.params[2] if len(msg.params) > 2 else ""

        rc = self.server.remote_clients.get(nick)
        if not rc:
            return

        channel = self.server.channels.get(channel_name)
        if not channel:
            return

        if not self._check_incoming_trust(channel_name):
            return

        # Notify local members
        part_params = [channel_name, reason] if reason else [channel_name]
        part_msg = Message(prefix=rc.prefix, command="PART", params=part_params)
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(part_msg)

        channel.members.discard(rc)
        rc.channels.discard(channel)

        if not channel.members:
            del self.server.channels[channel_name]

    async def _cleanup_remote_client_channels(self, rc, quit_msg: Message) -> None:
        """Notify local members of rc's quit and remove rc from channels."""
        notified: set = set()
        for channel in list(rc.channels):
            for member in list(channel.members):
                if not isinstance(member, RemoteClient) and member not in notified:
                    await member.send(quit_msg)
                    notified.add(member)
            channel.members.discard(rc)
            if not channel.members:
                self.server.channels.pop(channel.name, None)
        rc.channels.clear()

    async def _handle_squituser(self, msg: Message) -> None:
        """Handle relayed client QUIT from peer."""
        if len(msg.params) < 1:
            return
        nick = msg.params[0]
        reason = msg.params[1] if len(msg.params) > 1 else "Remote client quit"

        rc = self.server.remote_clients.get(nick)
        if not rc:
            return

        quit_msg = Message(prefix=rc.prefix, command="QUIT", params=[reason])
        await self._cleanup_remote_client_channels(rc, quit_msg)
        del self.server.remote_clients[nick]

    def _handle_squit(self, msg: Message) -> None:
        """Handle peer announcing it's delinking."""
        self._squit_received = True
        raise ConnectionError("Peer sent SQUIT")

    def _handle_sroommeta(self, msg: Message) -> None:
        """Receive room metadata from peer and apply to local channel."""
        import json

        if len(msg.params) < 2:
            return
        channel_name = msg.params[0]
        meta_json = msg.params[1]

        # Trust check: for SROOMMETA we accept metadata for channels that the
        # sender already filtered via should_relay(). Don't block new channel
        # creation for metadata-only (no membership changes).
        existing = self.server.channels.get(channel_name)
        if existing and existing.restricted:
            return
        # For restricted trust, only accept if channel exists and is shared
        if self.trust == "restricted" and existing and self.peer_name not in existing.shared_with:
            return

        try:
            meta = json.loads(meta_json)
        except (json.JSONDecodeError, ValueError):
            return

        channel = self.server.get_or_create_channel(channel_name)
        self._merge_room_metadata(channel, meta)

    @staticmethod
    def _merge_room_metadata(channel, meta: dict) -> None:
        """Apply metadata fields from a peer to a local channel object."""
        channel.room_id = meta.get("room_id") or channel.room_id
        channel.creator = meta.get("creator") or channel.creator
        channel.owner = meta.get("owner") or channel.owner
        channel.purpose = meta.get("purpose") or channel.purpose
        channel.instructions = meta.get("instructions") or channel.instructions
        if isinstance(meta.get("tags"), list):
            channel.tags = meta["tags"]
        channel.persistent = bool(meta.get("persistent", channel.persistent))
        if meta.get("agent_limit") is not None:
            channel.agent_limit = meta["agent_limit"]
        if isinstance(meta.get("extra_meta"), dict):
            channel.extra_meta.update(meta["extra_meta"])
        if meta.get("created_at") is not None:
            channel.created_at = meta["created_at"]

    def _handle_stags(self, msg: Message) -> None:
        """Receive agent tags from peer and apply to remote client."""
        if len(msg.params) < 2:
            return
        nick = msg.params[0]
        tags_str = msg.params[1]

        rc = self.server.remote_clients.get(nick)
        if rc is None:
            return

        rc.tags = [t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else []

    async def _part_local_members(self, channel, channel_name: str, reason: str) -> None:
        """Notify local members of an archive and part them from the channel."""
        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=["*", f"Room {channel_name} has been archived"],
        )
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(notice)
                part_msg = Message(
                    prefix=member.prefix,
                    command="PART",
                    params=[channel_name, reason],
                )
                await member.send(part_msg)
            if hasattr(member, "channels"):
                member.channels.discard(channel)

    def _rename_channel_to_archive(self, channel, channel_name: str, archive_name: str) -> None:
        """Clear membership and rename a channel to its archive name."""
        channel.members.clear()
        channel.operators.clear()
        channel.voiced.clear()
        del self.server.channels[channel_name]
        channel.name = archive_name
        channel.archived = True
        self.server.channels[archive_name] = channel

    async def _handle_sroomarchive(self, msg: Message) -> None:
        """Receive archive event from peer — rename channel and mark archived."""
        if len(msg.params) < 2:
            return
        channel_name = msg.params[0]
        archive_name = msg.params[1]

        channel = self.server.channels.get(channel_name)
        if channel is None:
            return

        if not self._check_incoming_trust(channel_name):
            return

        await self._part_local_members(channel, channel_name, "Room archived")
        self._rename_channel_to_archive(channel, channel_name, archive_name)

    async def _handle_sthread(self, msg: Message) -> None:
        """Handle inbound S2S STHREAD — deliver thread message to local clients."""
        if len(msg.params) < 4:
            return
        channel_name = msg.params[0]
        sender_nick = msg.params[1]
        thread_name = msg.params[2]
        text = msg.params[3]

        channel = self.server.channels.get(channel_name)
        if channel is None:
            return
        if not self.should_relay(channel_name):
            return

        # Deliver prefixed PRIVMSG to local members
        relay = Message(
            prefix=f"{sender_nick}!{sender_nick}@{self.peer_name}",
            command="PRIVMSG",
            params=[channel_name, text],
        )
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(relay)

        # Emit locally with _origin to prevent re-relay
        await self.server.emit_event(
            Event(
                type=EventType.THREAD_MESSAGE,
                channel=channel_name,
                nick=sender_nick,
                data={"text": text, "thread": thread_name, "_origin": self.peer_name},
            )
        )

        # Notify @mentions for remote thread messages
        await self._notify_remote_mentions(channel_name, sender_nick, text)

    async def _handle_sthreadclose(self, msg: Message) -> None:
        """Handle inbound S2S STHREADCLOSE — deliver thread close notice."""
        if len(msg.params) < 4:
            return
        channel_name = msg.params[0]
        sender_nick = msg.params[1]
        thread_name = msg.params[2]
        close_data = msg.params[3]

        channel = self.server.channels.get(channel_name)
        if channel is None:
            return
        if not self.should_relay(channel_name):
            return

        notice = Message(
            prefix=self.server.config.name,
            command="NOTICE",
            params=[channel_name, f"[Thread {thread_name} closed] {close_data}"],
        )
        for member in list(channel.members):
            if not isinstance(member, RemoteClient):
                await member.send(notice)

        await self.server.emit_event(
            Event(
                type=EventType.THREAD_CLOSE,
                channel=channel_name,
                nick=sender_nick,
                data={"thread": thread_name, "summary": close_data, "_origin": self.peer_name},
            )
        )

    # --- Generic SEVENT federation ---

    @staticmethod
    def _decode_sevent_payload(b64: str, peer_name: str) -> dict | None:
        """Decode and validate a base64-JSON SEVENT payload."""
        try:
            data = json.loads(base64.b64decode(b64))
        except (ValueError, TypeError) as exc:
            logger.warning("SEVENT bad payload from %s: %s", peer_name, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("SEVENT non-dict payload from %s", peer_name)
            return None
        return data

    @staticmethod
    def _parse_event_type(type_str: str):
        """Map a wire type string to EventType enum, or keep as string."""
        try:
            return EventType(type_str)
        except ValueError:
            return type_str

    async def _handle_sevent(self, msg: Message) -> None:
        """Ingest a generic federated event from a peer.

        Wire format: SEVENT <origin-server> <seq> <type> <channel_or_*> :<b64-json-data>
        """
        if len(msg.params) < 5:
            return
        origin, _seq, type_str, target, b64 = msg.params[:5]
        channel = None if target == "*" else target

        if origin != self.peer_name:
            logger.warning("SEVENT origin %s != peer %s", origin, self.peer_name)

        if channel is not None and not self._check_incoming_trust(channel):
            return

        data = self._decode_sevent_payload(b64, self.peer_name)
        if data is None:
            return

        data["_origin"] = origin
        type_enum = self._parse_event_type(type_str)

        ev = Event(
            type=type_enum,
            channel=channel,
            nick=data.get("nick", f"{SYSTEM_USER_PREFIX}{origin}"),
            data=data,
        )
        await self.server.emit_event(ev)

    # --- Backfill ---

    async def _send_backfill_request(self) -> None:
        """Request missed events from peer since our last known seq."""
        await self.send_raw(f"BACKFILL {self.server.config.name} {self.last_seen_seq}")

    @staticmethod
    def _should_replay_event(seq: int, event: Event, effective_seq: int) -> bool:
        """Return True if event should be replayed to a peer."""
        if seq <= effective_seq:
            return False
        if event.data.get("_origin"):
            return False
        return True

    async def _handle_backfill(self, msg: Message) -> None:
        """Peer is requesting backfill from a given sequence."""
        if len(msg.params) < 2:
            return
        try:
            from_seq = int(msg.params[1])
        except ValueError:
            return

        # Use the higher of: what peer claims, or what we know they acked
        # (during real-time relay, peer saw everything up to our _seq at link drop)
        acked = self.server._peer_acked_seq.get(self.peer_name, 0)
        effective_seq = max(from_seq, acked)

        for seq, event in self.server._event_log:
            if self._should_replay_event(seq, event, effective_seq):
                await self._replay_event(seq, event)

        await self.send_raw(f":{self.server.config.name} BACKFILLEND {self.server._seq}")

    def _handle_backfillend(self, msg: Message) -> None:
        """Peer finished backfilling."""
        if msg.params:
            try:
                self.last_seen_seq = int(msg.params[0])
            except ValueError:
                pass

    async def _replay_event(self, seq: int, event: Event) -> None:
        """Replay a single event to the peer as S2S wire format."""
        origin = self.server.config.name
        if event.type == EventType.MESSAGE:
            target = event.channel or event.data.get("target", "")
            text = event.data.get("text", "")
            # Filter channel messages through trust check
            if target.startswith("#") and not self.should_relay(target):
                return
            cmd = event.data.get("notice") and "SNOTICE" or "SMSG"
            await self.send_raw(f":{origin} {cmd} {target} {event.nick} :{text}")

    # --- Relay outbound ---

    _RELAY_DISPATCH: dict = {}  # populated after class body

    async def relay_event(self, event: Event) -> None:
        """Relay a local event to the peer in S2S wire format."""
        event_type_str = event.type.value if hasattr(event.type, "value") else str(event.type)
        attrs = {
            "event.type": event_type_str,
            "s2s.peer": self.peer_name or "",
        }
        with otel_trace.get_tracer(_TRACER_NAME).start_as_current_span(
            "irc.s2s.relay", attributes=attrs
        ):
            origin = self.server.config.name
            handler = self._RELAY_DISPATCH.get(event.type)
            if handler:
                await maybe_await(handler(self, event, origin))
                return

            # If no typed relay exists, fall back to generic SEVENT.
            # v1 assumes all peers support SEVENT; cap negotiation is deferred — see plan task 12.
            payload = self.server._build_event_payload(event)
            encoded = self.server._encode_event_data(payload, event_type_str)
            target = event.channel or "*"
            # Egress trust check: channel-scoped events respect should_relay; global events always relay
            if event.channel is not None and not self.should_relay(event.channel):
                return
            seq = self.server._seq  # current local seq; peer stores but doesn't re-sequence
            await self.send_raw(
                f":{origin} SEVENT {origin} {seq} {event_type_str} {target} :{encoded}"
            )

    async def _relay_message(self, event: Event, origin: str) -> None:
        target = event.channel or event.data.get("target", "")
        text = event.data.get("text", "")
        # Filter channel messages through trust check
        if target.startswith("#") and not self.should_relay(target):
            return
        if event.data.get("notice"):
            await self.send_raw(f":{origin} SNOTICE {target} {event.nick} :{text}")
        else:
            await self.send_raw(f":{origin} SMSG {target} {event.nick} :{text}")

    async def _relay_join(self, event: Event, origin: str) -> None:
        channel_name = event.channel
        if not self.should_relay(channel_name):
            return
        await self.send_raw(f":{origin} SJOIN {channel_name} {event.nick}")

    async def _relay_part(self, event: Event, origin: str) -> None:
        channel_name = event.channel
        if not self.should_relay(channel_name):
            return
        reason = event.data.get("reason", "")
        await self.send_raw(f":{origin} SPART {channel_name} {event.nick} :{reason}")

    async def _relay_quit(self, event: Event, origin: str) -> None:
        reason = event.data.get("reason", "Quit")
        await self.send_raw(f":{origin} SQUITUSER {event.nick} :{reason}")

    async def _relay_topic(self, event: Event, origin: str) -> None:
        channel_name = event.channel
        if not self.should_relay(channel_name):
            return
        topic = event.data.get("topic", "")
        await self.send_raw(f":{origin} STOPIC {channel_name} {event.nick} :{topic}")

    async def _relay_room_metadata(self, event: Event, origin: str) -> None:
        channel_name = event.channel
        if not self.should_relay(channel_name):
            return
        meta = event.data.get("meta", "")
        await self.send_raw(f":{origin} SROOMMETA {channel_name} :{meta}")

    async def _relay_tags(self, event: Event, origin: str) -> None:
        tags_str = ",".join(event.data.get("tags", []))
        await self.send_raw(f":{origin} STAGS {event.nick} :{tags_str}")

    async def _relay_room_archive(self, event: Event, origin: str) -> None:
        channel_name = event.channel
        archive_name = event.data.get("archive_name", "")
        # The channel has already been renamed — check relay using the archive name
        if not self.should_relay(archive_name):
            return
        await self.send_raw(f":{origin} SROOMARCHIVE {channel_name} {archive_name}")

    async def _relay_thread_message(self, event: Event, origin: str) -> None:
        channel_name = event.channel
        if not self.should_relay(channel_name):
            return
        thread_name = event.data.get("thread", "")
        text = event.data.get("text", "")  # This is the prefixed text
        await self.send_raw(f":{origin} STHREAD {channel_name} {event.nick} {thread_name} :{text}")

    async def _relay_thread_close(self, event: Event, origin: str) -> None:
        channel_name = event.channel
        if not self.should_relay(channel_name):
            return
        thread_name = event.data.get("thread", "")
        summary = event.data.get("summary", "")
        promoted_to = event.data.get("promoted_to", "")
        close_data = summary
        if promoted_to:
            close_data = f"PROMOTE {promoted_to} {summary}"
        await self.send_raw(
            f":{origin} STHREADCLOSE {channel_name} {event.nick} {thread_name} :{close_data}"
        )

    # --- Mention notifications for remote messages ---

    def _resolve_mention_target(self, raw_nick, sender_nick, seen, channel):
        """Resolve a raw @mention to a notifiable local Client, or None."""
        nick = raw_nick.rstrip(".,;:!?")
        if nick in seen or nick == sender_nick:
            return None
        seen.add(nick)
        target_client = self.server.clients.get(nick)
        if not target_client:
            return None
        if channel and target_client not in channel.members:
            return None
        return target_client

    async def _notify_remote_mentions(
        self, channel_name: str | None, sender_nick: str, text: str
    ) -> None:
        """Check for @mentions in remote messages and notify local clients."""
        import re

        mentioned_nicks = re.findall(r"@(\S+)", text)
        if not mentioned_nicks:
            return
        seen: set[str] = set()
        channel = self.server.channels.get(channel_name) if channel_name else None
        source = channel_name or "a direct message"
        for raw_nick in mentioned_nicks:
            target_client = self._resolve_mention_target(raw_nick, sender_nick, seen, channel)
            if not target_client:
                continue
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[
                    target_client.nick,
                    f"{sender_nick} mentioned you in {source}: {text}",
                ],
            )
            await target_client.send(notice)


# Populate the relay dispatch table after the class is fully defined so that
# the unbound method references resolve correctly.
ServerLink._RELAY_DISPATCH = {
    EventType.MESSAGE: ServerLink._relay_message,
    EventType.JOIN: ServerLink._relay_join,
    EventType.PART: ServerLink._relay_part,
    EventType.QUIT: ServerLink._relay_quit,
    EventType.TOPIC: ServerLink._relay_topic,
    EventType.ROOMMETA: ServerLink._relay_room_metadata,
    EventType.TAGS: ServerLink._relay_tags,
    EventType.ROOMARCHIVE: ServerLink._relay_room_archive,
    EventType.THREAD_CREATE: ServerLink._relay_thread_message,
    EventType.THREAD_MESSAGE: ServerLink._relay_thread_message,
    EventType.THREAD_CLOSE: ServerLink._relay_thread_close,
}
