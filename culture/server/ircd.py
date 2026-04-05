# server/ircd.py
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import TYPE_CHECKING

from culture.server.channel import Channel
from culture.server.config import ServerConfig
from culture.server.skill import Event, Skill

if TYPE_CHECKING:
    from culture.bots.virtual_client import VirtualClient
    from culture.server.client import Client
    from culture.server.remote_client import RemoteClient
    from culture.server.server_link import ServerLink


class IRCd:
    """The culture IRC server."""

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
        self._link_retry_state: dict[str, dict] = (
            {}
        )  # peer_name -> {"delay": float, "task": asyncio.Task}
        self._stopping = False
        self._background_tasks: set[asyncio.Task] = set()
        # Bots
        self.bot_manager = None  # set in start() if webhook_port configured

    async def start(self) -> None:
        await self._register_default_skills()
        self._restore_persistent_rooms()

        # Initialize bot manager and webhook HTTP listener
        from culture.bots.bot_manager import BotManager
        from culture.bots.http_listener import HttpListener

        self.bot_manager = BotManager(self)
        await self.bot_manager.load_bots()

        self._server = await asyncio.start_server(
            self._handle_connection,
            self.config.host,
            self.config.port,
        )

        self._http_listener = HttpListener(
            self.bot_manager,
            "127.0.0.1",
            self.config.webhook_port,
        )
        try:
            await self._http_listener.start()
        except OSError:
            # Port unavailable (e.g. in tests using port 0 that got
            # assigned an in-use ephemeral port). Non-fatal — bots
            # still work, just without the HTTP endpoint.
            logging.getLogger(__name__).warning(
                "Could not start webhook listener on port %d",
                self.config.webhook_port,
            )

    async def _register_default_skills(self) -> None:
        from culture.server.skills.history import HistorySkill
        from culture.server.skills.rooms import RoomsSkill
        from culture.server.skills.threads import ThreadsSkill

        await self.register_skill(HistorySkill())
        await self.register_skill(RoomsSkill())
        await self.register_skill(ThreadsSkill())

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
                    logging.getLogger(__name__).exception("Failed to relay event to %s", peer_name)

    def get_skill_for_command(self, command: str) -> Skill | None:
        for skill in self.skills:
            if command in skill.commands:
                return skill
        return None

    def get_client(self, nick: str) -> Client | RemoteClient | VirtualClient | None:
        """Look up a client by nick, checking local, remote, and bots."""
        client = self.clients.get(nick) or self.remote_clients.get(nick)
        if client:
            return client
        if self.bot_manager:
            bot = self.bot_manager.get_bot(nick)
            if bot and bot.virtual_client:
                return bot.virtual_client
        return None

    async def stop(self) -> None:
        self._stopping = True
        # Stop bots and HTTP listener
        if self.bot_manager:
            await self.bot_manager.stop_all()
        if hasattr(self, "_http_listener") and self._http_listener:
            await self._http_listener.stop()
        for skill in self.skills:
            await skill.stop()
        # Cancel all pending retry tasks
        for peer_name in list(self._link_retry_state):
            self.cancel_link_retry(peer_name)
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
        from culture.server.server_link import ServerLink

        reader, writer = await asyncio.open_connection(host, port)
        link = ServerLink(reader, writer, self, password, initiator=True, trust=trust)
        task = asyncio.create_task(link.handle())
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return link

    def maybe_retry_link(self, peer_name: str) -> None:
        """Schedule auto-reconnect for a dropped peer link."""
        if self._stopping:
            return
        # Find matching LinkConfig
        link_config = None
        for lc in self.config.links:
            if lc.name == peer_name:
                link_config = lc
                break
        if link_config is None:
            return

        # Skip if already retrying
        if peer_name in self._link_retry_state:
            return

        state = {"delay": 5.0, "task": None}
        state["task"] = asyncio.create_task(self._retry_link_loop(peer_name, link_config, state))
        self._link_retry_state[peer_name] = state

    async def _retry_link_loop(self, peer_name: str, link_config, state: dict) -> None:
        """Retry connecting to a peer with exponential backoff."""
        logger = logging.getLogger(__name__)
        try:
            while True:
                await asyncio.sleep(state["delay"])

                # If peer already reconnected, stop retrying
                if peer_name in self.links:
                    break

                try:
                    link = await self.connect_to_peer(
                        link_config.host,
                        link_config.port,
                        link_config.password,
                        trust=link_config.trust,
                    )
                    # Wait for handshake to complete
                    for _ in range(50):
                        if peer_name in self.links:
                            break
                        await asyncio.sleep(0.1)

                    if peer_name in self.links:
                        logger.info("Reconnected to peer %s", peer_name)
                        break
                    else:
                        logger.warning("Handshake with %s did not complete, retrying", peer_name)
                        # Close the stale link to avoid leaked connections
                        try:
                            link.writer.close()
                        except Exception:
                            pass
                except Exception:
                    logger.debug(
                        "Retry connect to %s failed, next in %.0fs",
                        peer_name,
                        min(state["delay"] * 2, 120),
                    )

                # Exponential backoff, cap at 120s
                state["delay"] = min(state["delay"] * 2, 120)
        except asyncio.CancelledError:
            raise
        finally:
            # Cleanup retry state
            self._link_retry_state.pop(peer_name, None)

    def cancel_link_retry(self, peer_name: str) -> None:
        """Cancel any pending retry task for a peer."""
        state = self._link_retry_state.pop(peer_name, None)
        if state and state.get("task"):
            state["task"].cancel()

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Peek at first message to detect S2S vs C2S."""
        from culture.protocol.message import Message

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
            await self._accept_s2s_connection(reader, writer, text, msg)
        else:
            await self._accept_c2s_connection(reader, writer, text, msg)

    async def _accept_s2s_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        initial_text: str,
        msg,
    ) -> None:
        """Handle an inbound S2S (server-to-server) connection."""
        from culture.server.server_link import ServerLink

        # S2S connection - password validated after SERVER reveals peer name
        if not self.config.links:
            writer.write(b"ERROR :No links configured\r\n")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, BrokenPipeError):
                pass
            return

        link = ServerLink(reader, writer, self, password=None, initiator=False, trust="restricted")
        try:
            await link.handle(initial_msg=initial_text)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass

    async def _accept_c2s_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        initial_text: str,
        msg,
    ) -> None:
        """Handle an inbound C2S (client-to-server) connection."""
        from culture.server.client import Client

        # C2S connection
        client = Client(reader, writer, self)
        try:
            await client.handle(initial_msg=initial_text)
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
            if not channel.members and not channel.persistent:
                del self.channels[channel.name]

    def _disconnect_remote_clients(self, link: ServerLink) -> None:
        """Notify local clients and remove all remote clients that came from *link*."""
        from culture.protocol.message import Message
        from culture.server.remote_client import RemoteClient

        to_remove = [nick for nick, rc in self.remote_clients.items() if rc.link is link]

        for nick in to_remove:
            rc = self.remote_clients[nick]
            quit_msg = Message(prefix=rc.prefix, command="QUIT", params=["Server link closed"])
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

    def _remove_link(self, link: ServerLink, *, squit: bool = False) -> None:
        """Remove a S2S link and all its remote clients."""
        peer_name = link.peer_name
        if peer_name and peer_name in self.links:
            del self.links[peer_name]
            # Persist our current seq -- peer saw everything up to here via real-time relay
            self._peer_acked_seq[peer_name] = self._seq

        self._disconnect_remote_clients(link)

        # Schedule auto-reconnect if this was an unexpected drop (not SQUIT)
        if peer_name and not squit:
            self.maybe_retry_link(peer_name)

    def _restore_persistent_rooms(self) -> None:
        """Reload persistent rooms from disk on startup."""
        if not self.config.data_dir:
            return
        from culture.server.room_store import RoomStore

        store = RoomStore(self.config.data_dir)
        for data in store.load_all():
            name = data["name"]
            channel = self.get_or_create_channel(name)
            channel.room_id = data["room_id"]
            channel.creator = data.get("creator")
            channel.owner = data.get("owner")
            channel.purpose = data.get("purpose")
            channel.instructions = data.get("instructions")
            channel.tags = data.get("tags", [])
            channel.persistent = data.get("persistent", False)
            channel.agent_limit = data.get("agent_limit")
            channel.extra_meta = data.get("extra_meta", {})
            channel.archived = data.get("archived", False)
            channel.created_at = data.get("created_at")
            channel.topic = data.get("topic")

    def get_or_create_channel(self, name: str) -> Channel:
        if name not in self.channels:
            self.channels[name] = Channel(name)
        return self.channels[name]
