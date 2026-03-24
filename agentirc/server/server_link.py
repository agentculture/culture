# server/server_link.py
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from agentirc.protocol.message import Message
from agentirc.server.remote_client import RemoteClient
from agentirc.server.skill import Event, EventType

if TYPE_CHECKING:
    from agentirc.server.ircd import IRCd

logger = logging.getLogger(__name__)


class ServerLink:
    """A server-to-server link to a peer IRCd."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        server: IRCd,
        password: str,
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
        try:
            self.writer.write(f"{line}\r\n".encode("utf-8"))
            await self.writer.drain()
        except (ConnectionError, BrokenPipeError, OSError):
            pass

    async def send(self, message: Message) -> None:
        try:
            self.writer.write(message.format().encode("utf-8"))
            await self.writer.drain()
        except (ConnectionError, BrokenPipeError, OSError):
            pass

    async def handle(self, initial_msg: str | None = None) -> None:
        """Main S2S connection loop."""
        try:
            if self.initiator:
                await self._send_handshake()

            buffer = ""
            if initial_msg:
                buffer = initial_msg + "\n"

            while True:
                if "\n" not in buffer:
                    data = await self.reader.read(4096)
                    if not data:
                        break
                    buffer += data.decode("utf-8", errors="replace")
                    buffer = buffer.replace("\r\n", "\n").replace("\r", "\n")

                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        msg = Message.parse(line)
                        if msg.command:
                            await self._dispatch(msg)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.server._remove_link(self)
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass

    async def _send_handshake(self) -> None:
        await self.send_raw(f"PASS {self.password}")
        await self.send_raw(
            f"SERVER {self.server.config.name} 1 :{self.server.config.name} IRC"
        )

    async def _dispatch(self, msg: Message) -> None:
        handler = getattr(self, f"_handle_{msg.command.lower()}", None)
        if handler:
            await handler(msg)

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

    async def _try_complete_handshake(self) -> None:
        if not (self._got_pass and self._got_server):
            return

        # For inbound links, look up expected password and trust by peer name
        if not self.initiator and self.password is None:
            link_config = None
            for lc in self.server.config.links:
                if lc.name == self.peer_name:
                    link_config = lc
                    break
            if not link_config:
                logger.warning("No link config for peer %s", self.peer_name)
                await self.send_raw(f"ERROR :No link configured for {self.peer_name}")
                raise ConnectionError(f"No link config for {self.peer_name}")
            self.password = link_config.password
            self.trust = link_config.trust

        if self._peer_pass != self.password:
            logger.warning("Bad password from peer %s", self.peer_name)
            await self.send_raw(f"ERROR :Bad password")
            raise ConnectionError("Bad S2S password")

        # Check for duplicate server name
        if self.peer_name in self.server.links:
            logger.warning("Duplicate server name %s", self.peer_name)
            await self.send_raw(f"ERROR :Server name {self.peer_name} already linked")
            raise ConnectionError("Duplicate server name")

        if self.peer_name == self.server.config.name:
            logger.warning("Peer has same name as us: %s", self.peer_name)
            await self.send_raw(f"ERROR :Cannot link to self")
            raise ConnectionError("Cannot link to self")

        self._authenticated = True
        self.server.links[self.peer_name] = self

        # Restore last seen seq from previous link sessions
        self.last_seen_seq = self.server._peer_acked_seq.get(self.peer_name, 0)

        if not self.initiator:
            await self._send_handshake()

        await self.send_burst()
        await self._send_backfill_request()

    # --- Burst handlers ---

    async def send_burst(self) -> None:
        """Send our local state to the peer."""
        # Send all local clients
        for client in self.server.clients.values():
            await self.send_raw(
                f"SNICK {client.nick} {client.user} {client.host} :{client.realname}"
            )

        # Send channel membership (filtered by trust)
        for channel in self.server.channels.values():
            if not self.should_relay(channel.name):
                continue
            local_nicks = [
                m.nick for m in channel.members
                if not isinstance(m, RemoteClient)
            ]
            if local_nicks:
                nicks_str = " ".join(local_nicks)
                await self.send_raw(f"SJOIN {channel.name} {nicks_str}")

        # Send channel topics (filtered by trust)
        for channel in self.server.channels.values():
            if not self.should_relay(channel.name):
                continue
            if channel.topic:
                # Find who set it (use first local member as setter)
                local_members = [
                    m for m in channel.members
                    if not isinstance(m, RemoteClient)
                ]
                setter = local_members[0].nick if local_members else self.server.config.name
                await self.send_raw(
                    f"STOPIC {channel.name} {setter} :{channel.topic}"
                )

    async def _handle_snick(self, msg: Message) -> None:
        if len(msg.params) < 4:
            return
        nick, user, host = msg.params[0], msg.params[1], msg.params[2]
        realname = msg.params[3]

        # Validate nick conforms to <peer_name>-<agent> format
        expected_prefix = f"{self.peer_name}-"
        if not nick.startswith(expected_prefix):
            logger.warning(
                "Rejected remote nick %s: must start with %s", nick, expected_prefix
            )
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

    async def _handle_sjoin(self, msg: Message) -> None:
        if len(msg.params) < 2:
            return

        channel_name = msg.params[0]
        nicks = msg.params[1:]

        # Check incoming trust: if we have a restricted trust for this peer,
        # only accept channel data for channels that have +S <peer>
        existing = self.server.channels.get(channel_name)
        if existing:
            if existing.restricted:
                return
            if self.trust == "restricted" and self.peer_name not in existing.shared_with:
                return
        else:
            # Channel doesn't exist locally yet — restricted links cannot create new channels
            if self.trust == "restricted":
                return

        channel = self.server.get_or_create_channel(channel_name)

        for nick in nicks:
            rc = self.server.remote_clients.get(nick)
            if rc and rc not in channel.members:
                channel.members.add(rc)
                rc.channels.add(channel)

                if self._authenticated:
                    # Notify local members about the join
                    join_msg = Message(
                        prefix=rc.prefix, command="JOIN", params=[channel_name]
                    )
                    for member in list(channel.members):
                        if not isinstance(member, RemoteClient):
                            await member.send(join_msg)

    async def _handle_stopic(self, msg: Message) -> None:
        if len(msg.params) < 3:
            return
        channel_name = msg.params[0]
        nick = msg.params[1]
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

    async def _handle_smsg(self, msg: Message) -> None:
        """Handle relayed PRIVMSG from peer."""
        if len(msg.params) < 3:
            return
        target = msg.params[0]
        sender_nick = msg.params[1]
        text = msg.params[2]

        # Check incoming trust for channel messages
        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if channel:
                if channel.restricted:
                    return
                if self.trust == "restricted" and self.peer_name not in channel.shared_with:
                    return

        relay = Message(
            prefix=f"{sender_nick}!*@*",
            command="PRIVMSG",
            params=[target, text],
        )

        # Build the sender prefix from remote client if available
        rc = self.server.remote_clients.get(sender_nick)
        if rc:
            relay = Message(
                prefix=rc.prefix,
                command="PRIVMSG",
                params=[target, text],
            )

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if channel:
                for member in list(channel.members):
                    if not isinstance(member, RemoteClient):
                        await member.send(relay)
                # Emit event for skills (e.g., history) with _origin to prevent re-relay
                await self.server.emit_event(
                    Event(
                        type=EventType.MESSAGE,
                        channel=target,
                        nick=sender_nick,
                        data={"text": text, "_origin": self.peer_name},
                    )
                )
                # Notify mentions for remote messages
                await self._notify_remote_mentions(target, sender_nick, text)
        else:
            # DM to a local client
            local = self.server.clients.get(target)
            if local:
                await local.send(relay)
                await self.server.emit_event(
                    Event(
                        type=EventType.MESSAGE,
                        channel=None,
                        nick=sender_nick,
                        data={"text": text, "_origin": self.peer_name},
                    )
                )
                await self._notify_remote_mentions(None, sender_nick, text)

    async def _handle_snotice(self, msg: Message) -> None:
        """Handle relayed NOTICE from peer."""
        if len(msg.params) < 3:
            return
        target = msg.params[0]
        sender_nick = msg.params[1]
        text = msg.params[2]

        # Check incoming trust for channel notices
        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if channel:
                if channel.restricted:
                    return
                if self.trust == "restricted" and self.peer_name not in channel.shared_with:
                    return

        rc = self.server.remote_clients.get(sender_nick)
        prefix = rc.prefix if rc else f"{sender_nick}!*@*"
        relay = Message(prefix=prefix, command="NOTICE", params=[target, text])

        if target.startswith("#"):
            channel = self.server.channels.get(target)
            if channel:
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
        else:
            local = self.server.clients.get(target)
            if local:
                await local.send(relay)

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

        # Check incoming trust
        if channel.restricted:
            return
        if self.trust == "restricted" and self.peer_name not in channel.shared_with:
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

        # Notify local members in shared channels
        notified = set()
        for channel in list(rc.channels):
            for member in list(channel.members):
                if not isinstance(member, RemoteClient) and member not in notified:
                    await member.send(quit_msg)
                    notified.add(member)
            channel.members.discard(rc)
            if not channel.members:
                del self.server.channels[channel.name]

        rc.channels.clear()
        del self.server.remote_clients[nick]

    async def _handle_squit(self, msg: Message) -> None:
        """Handle peer announcing it's delinking."""
        raise ConnectionError("Peer sent SQUIT")

    # --- Backfill ---

    async def _send_backfill_request(self) -> None:
        """Request missed events from peer since our last known seq."""
        await self.send_raw(
            f"BACKFILL {self.server.config.name} {self.last_seen_seq}"
        )

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

        # Replay events from our log that are after effective_seq
        for seq, event in self.server._event_log:
            if seq <= effective_seq:
                continue
            # Only replay events that originated locally
            if event.data.get("_origin"):
                continue
            await self._replay_event(seq, event)

        await self.send_raw(
            f":{self.server.config.name} BACKFILLEND {self.server._seq}"
        )

    async def _handle_backfillend(self, msg: Message) -> None:
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
            await self.send_raw(
                f":{origin} {cmd} {target} {event.nick} :{text}"
            )

    # --- Relay outbound ---

    async def relay_event(self, event: Event) -> None:
        """Relay a local event to the peer in S2S wire format."""
        origin = self.server.config.name
        seq = self.server._seq

        if event.type == EventType.MESSAGE:
            target = event.channel or event.data.get("target", "")
            text = event.data.get("text", "")
            # Filter channel messages through trust check
            if target.startswith("#") and not self.should_relay(target):
                return
            if event.data.get("notice"):
                await self.send_raw(
                    f":{origin} SNOTICE {target} {event.nick} :{text}"
                )
            else:
                await self.send_raw(
                    f":{origin} SMSG {target} {event.nick} :{text}"
                )
        elif event.type == EventType.JOIN:
            channel_name = event.channel
            if not self.should_relay(channel_name):
                return
            await self.send_raw(
                f":{origin} SJOIN {channel_name} {event.nick}"
            )
        elif event.type == EventType.PART:
            channel_name = event.channel
            if not self.should_relay(channel_name):
                return
            reason = event.data.get("reason", "")
            await self.send_raw(
                f":{origin} SPART {channel_name} {event.nick} :{reason}"
            )
        elif event.type == EventType.QUIT:
            reason = event.data.get("reason", "Quit")
            await self.send_raw(
                f":{origin} SQUITUSER {event.nick} :{reason}"
            )
        elif event.type == EventType.TOPIC:
            channel_name = event.channel
            if not self.should_relay(channel_name):
                return
            topic = event.data.get("topic", "")
            await self.send_raw(
                f":{origin} STOPIC {channel_name} {event.nick} :{topic}"
            )

    # --- Mention notifications for remote messages ---

    async def _notify_remote_mentions(
        self, channel_name: str | None, sender_nick: str, text: str
    ) -> None:
        """Check for @mentions in remote messages and notify local clients."""
        import re
        mentioned_nicks = re.findall(r"@(\S+)", text)
        if not mentioned_nicks:
            return
        seen: set[str] = set()
        channel = (
            self.server.channels.get(channel_name) if channel_name else None
        )
        source = channel_name or "a direct message"
        for raw_nick in mentioned_nicks:
            nick = raw_nick.rstrip(".,;:!?")
            if nick in seen or nick == sender_nick:
                continue
            seen.add(nick)
            # Only notify local clients
            target_client = self.server.clients.get(nick)
            if not target_client:
                continue
            if channel and target_client not in channel.members:
                continue
            notice = Message(
                prefix=self.server.config.name,
                command="NOTICE",
                params=[
                    nick,
                    f"{sender_nick} mentioned you in {source}: {text}",
                ],
            )
            await target_client.send(notice)
