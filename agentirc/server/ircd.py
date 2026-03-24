# server/ircd.py
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

from agentirc.server.config import ServerConfig
from agentirc.server.channel import Channel
from agentirc.server.skill import Event, Skill

if TYPE_CHECKING:
    from agentirc.server.client import Client
    from agentirc.server.remote_client import RemoteClient
    from agentirc.server.server_link import ServerLink


class IRCd:
    """The agentirc IRC server."""

    def __init__(self, config: ServerConfig):
        self.config = config
        self.clients: dict[str, Client] = {}  # nick -> Client
        self.channels: dict[str, Channel] = {}  # name -> Channel
        self.skills: list[Skill] = []
        self._server: asyncio.Server | None = None
        # Federation
        self.links: dict[str, ServerLink] = {}  # peer_name -> ServerLink
        self.remote_clients: dict[str, RemoteClient] = {}  # nick -> RemoteClient
        self._seq: int = 0
        self._event_log: deque[tuple[int, Event]] = deque(maxlen=10000)
        self._peer_acked_seq: dict[str, int] = {}  # peer_name -> our _seq when link last dropped

    async def start(self) -> None:
        await self._register_default_skills()
        self._server = await asyncio.start_server(
            self._handle_connection,
            self.config.host,
            self.config.port,
        )

    async def _register_default_skills(self) -> None:
        from agentirc.server.skills.history import HistorySkill

        await self.register_skill(HistorySkill())

    async def register_skill(self, skill: Skill) -> None:
        self.skills.append(skill)
        await skill.start(self)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def emit_event(self, event: Event) -> None:
        # Log event with sequence number
        seq = self.next_seq()
        self._event_log.append((seq, event))

        for skill in self.skills:
            try:
                await skill.on_event(event)
            except Exception:
                logging.getLogger(__name__).exception(
                    "Skill %s failed on event %s", skill.name, event.type
                )

        # Relay to linked peers — only relay locally-originated events
        # (no mesh routing; scope is direct peers only)
        if not event.data.get("_origin"):
            for peer_name, link in list(self.links.items()):
                try:
                    await link.relay_event(event)
                except Exception:
                    logging.getLogger(__name__).exception(
                        "Failed to relay event to %s", peer_name
                    )

    def get_skill_for_command(self, command: str) -> Skill | None:
        for skill in self.skills:
            if command in skill.commands:
                return skill
        return None

    def get_client(self, nick: str) -> Client | RemoteClient | None:
        """Look up a client by nick, checking both local and remote."""
        return self.clients.get(nick) or self.remote_clients.get(nick)

    async def stop(self) -> None:
        for skill in self.skills:
            await skill.stop()
        # Close all S2S links
        for link in list(self.links.values()):
            try:
                link.writer.close()
            except Exception:
                pass
        self.links.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def connect_to_peer(
        self, host: str, port: int, password: str, trust: str = "full"
    ) -> ServerLink:
        """Initiate an outbound S2S connection."""
        from agentirc.server.server_link import ServerLink

        reader, writer = await asyncio.open_connection(host, port)
        link = ServerLink(reader, writer, self, password, initiator=True, trust=trust)
        asyncio.create_task(link.handle())
        return link

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Peek at first message to detect S2S vs C2S."""
        from agentirc.server.client import Client
        from agentirc.server.server_link import ServerLink
        from agentirc.protocol.message import Message

        # Read first line to detect connection type
        first_data = await reader.read(4096)
        if not first_data:
            writer.close()
            return

        text = first_data.decode("utf-8", errors="replace")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        first_line = text.split("\n", 1)[0].strip()
        msg = Message.parse(first_line)

        if msg.command == "PASS":
            # S2S connection - password validated after SERVER reveals peer name
            if not self.config.links:
                writer.write(b"ERROR :No links configured\r\n")
                await writer.drain()
                writer.close()
                return

            link = ServerLink(reader, writer, self, password=None, initiator=False, trust="restricted")
            try:
                await link.handle(initial_msg=text)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
        else:
            # C2S connection
            client = Client(reader, writer, self)
            try:
                await client.handle(initial_msg=text)
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                self._remove_client(client)
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, BrokenPipeError):
                    pass

    def _remove_client(self, client: Client) -> None:
        if client.nick and client.nick in self.clients:
            del self.clients[client.nick]
        for channel in list(client.channels):
            channel.remove(client)
            if not channel.members:
                del self.channels[channel.name]

    def _remove_link(self, link: ServerLink) -> None:
        """Remove a S2S link and all its remote clients."""
        from agentirc.protocol.message import Message
        from agentirc.server.remote_client import RemoteClient

        peer_name = link.peer_name
        if peer_name and peer_name in self.links:
            del self.links[peer_name]
            # Persist our current seq -- peer saw everything up to here via real-time relay
            self._peer_acked_seq[peer_name] = self._seq

        # Find all remote clients from this link
        to_remove = [
            nick for nick, rc in self.remote_clients.items()
            if rc.link is link
        ]

        for nick in to_remove:
            rc = self.remote_clients[nick]
            quit_msg = Message(
                prefix=rc.prefix, command="QUIT", params=["Server link closed"]
            )
            notified: set = set()
            for channel in list(rc.channels):
                for member in list(channel.members):
                    if not isinstance(member, RemoteClient) and member not in notified:
                        asyncio.ensure_future(member.send(quit_msg))
                        notified.add(member)
                channel.members.discard(rc)
                if not channel.members:
                    if channel.name in self.channels:
                        del self.channels[channel.name]
            rc.channels.clear()
            del self.remote_clients[nick]

    def get_or_create_channel(self, name: str) -> Channel:
        if name not in self.channels:
            self.channels[name] = Channel(name)
        return self.channels[name]
