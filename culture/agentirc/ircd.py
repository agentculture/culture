# server/ircd.py
from __future__ import annotations

import asyncio
import base64
import json
import logging
from collections import deque
from typing import TYPE_CHECKING

from opentelemetry import trace as _otel_trace

from culture.agentirc.channel import Channel
from culture.agentirc.config import ServerConfig
from culture.agentirc.events import NO_SURFACE_EVENT_TYPES, render_event
from culture.agentirc.skill import Event, EventType, Skill
from culture.bots.virtual_client import VirtualClient
from culture.constants import (
    EVENT_TAG_DATA,
    EVENT_TAG_TYPE,
    SYSTEM_CHANNEL,
    SYSTEM_USER_PREFIX,
)
from culture.protocol.message import Message

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from culture.agentirc.client import Client
    from culture.agentirc.remote_client import RemoteClient
    from culture.agentirc.server_link import ServerLink


class IRCd:
    """The culture IRC server."""

    def __init__(self, config: ServerConfig):
        from culture.telemetry import init_telemetry

        self.config = config
        self.tracer = init_telemetry(config)
        self.clients: dict[str, Client | VirtualClient] = {}  # nick -> Client
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
        self._stopped = asyncio.Event()
        self._background_tasks: set[asyncio.Task] = set()
        # Bots
        self.bot_manager = None  # set in start() if webhook_port configured
        self.system_client: VirtualClient | None = None

    async def start(self) -> None:
        logger.info("Registering default skills...")
        await self._register_default_skills()

        logger.info("Restoring persistent rooms...")
        self._restore_persistent_rooms()

        logger.info("Bootstrapping system identity...")
        self._bootstrap_system_identity()

        await self.emit_event(
            Event(
                type=EventType.SERVER_WAKE,
                channel=None,
                nick=f"{SYSTEM_USER_PREFIX}{self.config.name}",
                data={"server": self.config.name},
            )
        )
        logger.info("Server awake on %s", self.config.name)

        # Initialize bot manager and webhook HTTP listener
        from culture.bots.bot_manager import BotManager
        from culture.bots.http_listener import HttpListener

        logger.info("Loading bots...")
        self.bot_manager = BotManager(self)
        await self.bot_manager.load_bots()
        self.bot_manager.load_system_bots()

        logger.info(
            "Binding IRC socket on %s:%d...",
            self.config.host,
            self.config.port,
        )
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
            logger.info(
                "Starting webhook listener on port %d...",
                self.config.webhook_port,
            )
            await self._http_listener.start()
        except OSError:
            # Port unavailable (e.g. in tests using port 0 that got
            # assigned an in-use ephemeral port). Non-fatal — bots
            # still work, just without the HTTP endpoint.
            logger.warning(
                "Could not start webhook listener on port %d",
                self.config.webhook_port,
            )

        logger.info("Server ready")

    def _bootstrap_system_identity(self) -> None:
        """Create the system pseudo-user and #system channel at server start.

        Called AFTER _restore_persistent_rooms so any legacy persisted room
        named #system can't overwrite the bootstrap. We force-correct the
        channel's invariants (persistent=True, archived=False) in case it
        was loaded from disk.
        """
        from culture.constants import SYSTEM_CHANNEL, SYSTEM_USER_PREFIX, SYSTEM_USER_REALNAME

        system_nick = f"{SYSTEM_USER_PREFIX}{self.config.name}"
        system_client = VirtualClient(system_nick, "system", self)
        system_client.realname = SYSTEM_USER_REALNAME
        system_client.host = self.config.name
        system_client.tags = []
        self.clients[system_nick] = system_client
        self.system_client = system_client

        channel = self.get_or_create_channel(SYSTEM_CHANNEL)
        channel.persistent = True
        # Force-correct in case #system was persisted as a regular room
        if hasattr(channel, "archived"):
            channel.archived = False
        channel.add(system_client)
        system_client.channels.add(channel)
        # Defensive: VirtualClients are excluded from auto-op by Channel._local_members(),
        # but channel.add() may still grant op when the channel is empty on first join.
        channel.operators.discard(system_client)

        logger.info("System identity %s joined %s", system_nick, SYSTEM_CHANNEL)

    async def _register_default_skills(self) -> None:
        from culture.agentirc.skills.history import HistorySkill
        from culture.agentirc.skills.icon import IconSkill
        from culture.agentirc.skills.rooms import RoomsSkill
        from culture.agentirc.skills.threads import ThreadsSkill

        await self.register_skill(HistorySkill())
        await self.register_skill(IconSkill())
        await self.register_skill(RoomsSkill())
        await self.register_skill(ThreadsSkill())

    async def register_skill(self, skill: Skill) -> None:
        self.skills.append(skill)
        await skill.start(self)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    @staticmethod
    def _build_event_span_attrs(event: Event, origin_tag: str | None) -> dict[str, str]:
        # event.type may be an EventType enum OR a plain string — federated
        # events forward unknown types verbatim (see _parse_event_type in
        # server_link.py).
        event_type_str = event.type.value if hasattr(event.type, "value") else str(event.type)
        attrs: dict[str, str] = {
            "event.type": event_type_str,
            "event.origin": "federated" if origin_tag else "local",
        }
        if event.channel:
            attrs["event.channel"] = event.channel
        if origin_tag:
            attrs["culture.federation.peer"] = origin_tag
        return attrs

    async def _run_skill_hooks(self, event: Event) -> None:
        for skill in self.skills:
            try:
                await skill.on_event(event)
            except Exception:
                logger.exception("Skill %s failed on event %s", skill.name, event.type)

    async def _relay_to_peers(self, event: Event) -> None:
        for peer_name, link in list(self.links.items()):
            try:
                await link.relay_event(event)
            except Exception:
                logger.exception("Failed to relay event to %s", peer_name)

    async def _dispatch_to_bots(self, event: Event) -> None:
        if self.bot_manager is None:
            return
        try:
            await self.bot_manager.on_event(event)
        except Exception:
            logger.exception("bot_manager.on_event failed")

    async def emit_event(self, event: Event) -> None:
        origin_tag = event.data.get("_origin")
        attrs = self._build_event_span_attrs(event, origin_tag)
        # Per-call get_tracer: the `tracing_exporter` test fixture swaps the
        # global provider between tests; a cached Tracer would bind to the
        # first test's provider and stop delivering to later ones.
        with _otel_trace.get_tracer("culture.agentirc").start_as_current_span(
            "irc.event.emit", attributes=attrs
        ):
            seq = self.next_seq()
            self._event_log.append((seq, event))
            await self._run_skill_hooks(event)
            if not origin_tag:
                await self._relay_to_peers(event)
            await self._dispatch_to_bots(event)
            await self._surface_event_privmsg(event)

    _NO_SURFACE_TYPES = NO_SURFACE_EVENT_TYPES

    @staticmethod
    def _build_event_payload(event: Event) -> dict:
        """Build the public event payload, enriched with canonical actor/channel."""
        payload = {k: v for k, v in event.data.items() if not k.startswith("_")}
        # Emitters that only set Event.nick (not data['nick']) still get a
        # correct render + payload thanks to setdefault.
        if event.nick:
            payload.setdefault("nick", event.nick)
        if event.channel:
            payload.setdefault("channel", event.channel)
        return payload

    @staticmethod
    def _encode_event_data(payload: dict, type_wire: str) -> str:
        """Base64-encode the payload as JSON; fall back to '{}' on TypeError."""
        try:
            return base64.b64encode(
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            ).decode("ascii")
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Event %s payload not JSON-serializable, surfacing with empty payload: %s",
                type_wire,
                exc,
            )
            return base64.b64encode(b"{}").decode("ascii")

    async def _deliver_to_members(self, channel, msg: Message, type_wire: str) -> None:
        """Send the surfaced PRIVMSG to channel members (skipping VirtualClients)."""
        for member in channel.members:
            # VirtualClients (system user, bots) receive events via subscription,
            # not by re-broadcasting the PRIVMSG.
            if isinstance(member, VirtualClient):
                continue
            # RemoteClients lack send_tagged; federation will deliver via SEVENT
            # (Task 12) instead. Skip silently here.
            if not hasattr(member, "send_tagged"):
                continue
            try:
                await member.send_tagged(msg)
            except Exception:
                logger.exception(
                    "Failed to surface %s to %s", type_wire, getattr(member, "nick", "?")
                )

    async def _surface_event_privmsg(self, event: Event) -> None:
        """Render the event as a tagged PRIVMSG into the appropriate channel.

        Channel-scoped events (event.channel set) go to that channel; global
        events go to #system. The PRIVMSG carries the structured payload as
        IRCv3 message tags @event=<type>;@event-data=<base64-json>.

        For federated events, the prefix uses the origin server's system user
        so consumers can identify which mesh server emitted the event. The
        federated-prefix branch only fires once Task 12 (SEVENT federation
        relay) lands and SEVENT is producing _origin-tagged events on the
        receive side. Today, all events surface with this server's prefix.

        Events whose content is already delivered via the normal IRC path
        (see _NO_SURFACE_TYPES) are skipped to avoid double-delivery.

        HISTORY/surface invariant (consumed by Task 13): an event surfaces
        here if and only if it lands in channel history. Events in
        _NO_SURFACE_TYPES are delivered via their own IRC verbs and not
        re-surfaced; HistorySkill should follow the same rule.
        """
        type_wire = event.type.value if hasattr(event.type, "value") else str(event.type)
        if type_wire in self._NO_SURFACE_TYPES:
            return

        target = SYSTEM_CHANNEL
        channel = self.channels.get(target)
        if channel is None:
            return

        origin_server = event.data.get("_origin") or self.config.name
        system_nick = f"{SYSTEM_USER_PREFIX}{origin_server}"

        payload = self._build_event_payload(event)
        encoded = self._encode_event_data(payload, type_wire)
        body = event.data.get("_render") or render_event(type_wire, payload, event.channel)

        msg = Message(
            tags={EVENT_TAG_TYPE: type_wire, EVENT_TAG_DATA: encoded},
            prefix=f"{system_nick}!system@{origin_server}",
            command="PRIVMSG",
            params=[target, body],
        )

        await self._deliver_to_members(channel, msg, type_wire)

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
        """Shut down the server. Concurrent callers await the same teardown.

        The first caller runs the teardown path; subsequent callers block on
        ``_stopped`` until teardown finishes (success *or* failure) so they
        never return while shutdown is still in flight. The event is set in a
        ``finally`` so an exception during teardown still unblocks waiters.
        """
        if self._stopping:
            await self._stopped.wait()
            return
        self._stopping = True
        try:
            logger.info("Server going to sleep on %s", self.config.name)
            try:
                await self.emit_event(
                    Event(
                        type=EventType.SERVER_SLEEP,
                        channel=None,
                        nick=f"{SYSTEM_USER_PREFIX}{self.config.name}",
                        data={"server": self.config.name},
                    )
                )
            except Exception:
                logger.exception("failed to emit server.sleep")
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
        finally:
            self._stopped.set()

    async def connect_to_peer(
        self, host: str, port: int, password: str, trust: str = "full"
    ) -> ServerLink:
        """Initiate an outbound S2S connection."""
        from culture.agentirc.server_link import ServerLink

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
        try:
            while True:
                await asyncio.sleep(state["delay"])

                if peer_name in self.links:
                    break

                if await self._attempt_single_reconnect(peer_name, link_config, logger):
                    break

                state["delay"] = min(state["delay"] * 2, 120)
        except asyncio.CancelledError:
            raise
        finally:
            self._link_retry_state.pop(peer_name, None)

    async def _attempt_single_reconnect(self, peer_name: str, link_config, logger) -> bool:
        """Try one reconnection attempt. Return True if the peer is now linked."""
        try:
            link = await self.connect_to_peer(
                link_config.host,
                link_config.port,
                link_config.password,
                trust=link_config.trust,
            )
            for _ in range(50):
                if peer_name in self.links:
                    break
                await asyncio.sleep(0.1)

            if peer_name in self.links:
                logger.info("Reconnected to peer %s", peer_name)
                return True

            logger.warning("Handshake with %s did not complete, retrying", peer_name)
            try:
                link.writer.close()
            except Exception:
                pass
        except Exception:
            logger.debug(
                "Retry connect to %s failed, next in %.0fs",
                peer_name,
                min(self._link_retry_state.get(peer_name, {}).get("delay", 5) * 2, 120),
            )
        return False

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
        from culture.agentirc.server_link import ServerLink

        # S2S connection - password validated after SERVER reveals peer name
        if not self.config.links:
            writer.write(b"ERROR :No links configured\r\n")
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
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
        from culture.agentirc.client import Client

        # C2S connection
        client = Client(reader, writer, self)
        try:
            await client.handle(initial_msg=initial_text)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            await self._remove_client(client)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    async def _emit_disconnect_events(self, nick: str, modes: set) -> None:
        """Emit lifecycle events for agent disconnect and/or console close."""
        if "A" in modes:
            try:
                await self.emit_event(
                    Event(
                        type=EventType.AGENT_DISCONNECT,
                        channel=None,
                        nick=nick,
                        data={"nick": nick},
                    )
                )
            except Exception:
                logger.exception("Failed to emit agent.disconnect for %s", nick)
        if "C" in modes:
            try:
                await self.emit_event(
                    Event(
                        type=EventType.CONSOLE_CLOSE,
                        channel=None,
                        nick=nick,
                        data={"nick": nick},
                    )
                )
            except Exception:
                logger.exception("Failed to emit console.close for %s", nick)

    async def _remove_client(self, client: Client) -> None:
        if client.nick and client.nick in self.clients:
            del self.clients[client.nick]
        for channel in list(client.channels):
            channel.remove(client)
            if not channel.members and not channel.persistent:
                del self.channels[channel.name]

        nick = client.nick or "<unknown>"
        await self._emit_disconnect_events(nick, getattr(client, "modes", set()))

    def _notify_local_quit(self, rc, quit_msg, notified: set) -> None:
        """Notify local members of a remote client quit and clean up channels."""
        from culture.agentirc.remote_client import RemoteClient

        for channel in list(rc.channels):
            for member in list(channel.members):
                if not isinstance(member, RemoteClient) and member not in notified:
                    task = asyncio.create_task(member.send(quit_msg))
                    self._background_tasks.add(task)
                    task.add_done_callback(self._background_tasks.discard)
                    notified.add(member)
            channel.members.discard(rc)
            if not channel.members:
                if channel.name in self.channels:
                    del self.channels[channel.name]
        rc.channels.clear()

    def _disconnect_remote_clients(self, link: ServerLink) -> None:
        """Notify local clients and remove all remote clients that came from *link*."""
        from culture.protocol.message import Message

        to_remove = [nick for nick, rc in self.remote_clients.items() if rc.link is link]

        for nick in to_remove:
            rc = self.remote_clients[nick]
            quit_msg = Message(prefix=rc.prefix, command="QUIT", params=["Server link closed"])
            notified: set = set()
            self._notify_local_quit(rc, quit_msg, notified)
            del self.remote_clients[nick]

    async def _remove_link(self, link: ServerLink, *, squit: bool = False) -> None:
        """Remove a S2S link and all its remote clients."""
        peer_name = link.peer_name
        if peer_name and peer_name in self.links:
            # Remove peer FIRST so server.unlink relays only to remaining peers
            del self.links[peer_name]
            # Persist our current seq -- peer saw everything up to here via real-time relay
            self._peer_acked_seq[peer_name] = self._seq

            # Emit server.unlink after removing the peer from self.links so the
            # event does NOT relay back to the peer that just dropped.
            try:
                await self.emit_event(
                    Event(
                        type=EventType.SERVER_UNLINK,
                        channel=None,
                        nick=f"{SYSTEM_USER_PREFIX}{self.config.name}",
                        data={"peer": peer_name},
                    )
                )
            except Exception:
                logger.exception("Failed to emit server.unlink for %s", peer_name)

        self._disconnect_remote_clients(link)

        # Schedule auto-reconnect if this was an unexpected drop (not SQUIT)
        if peer_name and not squit:
            self.maybe_retry_link(peer_name)

    def _restore_persistent_rooms(self) -> None:
        """Reload persistent rooms from disk on startup."""
        if not self.config.data_dir:
            return
        from culture.agentirc.room_store import RoomStore

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
