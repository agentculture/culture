"""Collect mesh state via IRC Observer queries and daemon IPC."""

from __future__ import annotations

import asyncio
import glob
import logging
import os
import time

from culture_core.bots.config import BOT_CONFIG_FILE
from culture_core.cli.shared.constants import culture_runtime_dir
from culture_core.protocol.message import Message as IRCMessage

from .model import Agent, BotInfo, FailedRoom, MeshState, Message, Room

RECV_TIMEOUT = 5.0
REGISTER_TIMEOUT = 10.0

logger = logging.getLogger(__name__)


class _PhaseError(Exception):
    """A per-room query phase failed; carries the phase name and root cause."""

    def __init__(self, phase: str, cause: BaseException, timings: dict[str, float]):
        super().__init__(phase, cause, timings)
        self.phase = phase
        self.cause = cause
        self.timings = timings

    def __str__(self) -> str:
        return f"{type(self.cause).__name__} during {self.phase}"


async def _timed(phase: str, coro, timings: dict[str, float]):
    """Await *coro*, recording its duration under *phase* in *timings*.

    On failure the elapsed time is still recorded and the exception is
    re-raised wrapped in :class:`_PhaseError` so the caller can attribute
    the failure to a query phase.
    """
    start = time.monotonic()
    try:
        result = await coro
    except Exception as exc:
        timings[phase] = time.monotonic() - start
        raise _PhaseError(phase, exc, timings) from exc
    timings[phase] = time.monotonic() - start
    return result


def _temp_nick(server_name: str) -> str:
    return f"{server_name}-_overview{os.urandom(2).hex()}"


def _build_room_agent(member_nick, who_data, server_name, all_agents, ch_name):
    """Build or update an Agent for a room member, return (Agent, is_remote)."""
    server_of = who_data.get(member_nick, server_name)
    is_remote = server_of != server_name

    if member_nick not in all_agents:
        all_agents[member_nick] = Agent(
            nick=member_nick,
            status="remote" if is_remote else "active",
            activity="",
            channels=[],
            server=server_of,
        )
    agent = all_agents[member_nick]
    if ch_name not in agent.channels:
        agent.channels.append(ch_name)
    return agent, is_remote


async def _collect_room(
    reader, writer, nick, ch_name, ch_topic, server_name, all_agents, message_limit
):
    """Query IRC for a single channel and return a Room.

    Each query phase is timed; a failing phase raises :class:`_PhaseError`
    so the caller can attribute the failure.
    """
    timings: dict[str, float] = {}
    members, _ = await _timed("NAMES", _query_names(reader, writer, nick, ch_name), timings)
    who_data = await _timed("WHO", _query_who(reader, writer, nick, ch_name), timings)
    messages = await _timed(
        "HISTORY", _query_history(reader, writer, nick, ch_name, message_limit), timings
    )

    room_agents = []
    fed_servers: set[str] = set()
    for member_nick, _is_op in members:
        agent, is_remote = _build_room_agent(
            member_nick, who_data, server_name, all_agents, ch_name
        )
        if is_remote:
            fed_servers.add(agent.server)
        room_agents.append(agent)

    op_nicks = [n for n, is_op in members if is_op]
    room_meta = await _timed("ROOMMETA", _query_roommeta(reader, writer, nick, ch_name), timings)
    logger.debug("overview: %s query timings: %s", ch_name, timings)
    return Room(
        name=ch_name,
        topic=ch_topic,
        members=room_agents,
        operators=op_nicks,
        federation_servers=sorted(fed_servers),
        messages=messages,
        room_id=room_meta.get("room_id"),
        owner=room_meta.get("owner"),
        purpose=room_meta.get("purpose"),
        tags=room_meta.get("tags", []),
        persistent=room_meta.get("persistent", False),
        query_timings=timings,
    )


def _inject_stopped_agents(
    all_agents: dict[str, Agent],
    manifest_agents: list,
    server_name: str,
) -> None:
    """Add stopped/registered agents from manifest that aren't on IRC."""
    for agent_cfg in manifest_agents:
        if agent_cfg.nick in all_agents or getattr(agent_cfg, "archived", False):
            continue
        all_agents[agent_cfg.nick] = Agent(
            nick=agent_cfg.nick,
            status="stopped",
            activity="",
            channels=agent_cfg.channels if isinstance(agent_cfg.channels, list) else [],
            server=server_name,
            backend=getattr(agent_cfg, "backend", None),
            directory=getattr(agent_cfg, "directory", None),
        )


async def collect_mesh_state(
    host: str,
    port: int,
    server_name: str,
    message_limit: int = 4,
    ipc_enabled: bool = True,
    manifest_agents: list | None = None,
) -> MeshState:
    """Collect a full mesh snapshot.

    Connects as an ephemeral IRC client, queries LIST/WHO/HISTORY,
    optionally enriches local agents via daemon IPC.
    """
    reader, writer, nick = await _connect(host, port, server_name)
    try:
        list_start = time.monotonic()
        channels = await _query_list(reader, writer, nick)
        logger.debug(
            "overview: LIST took %.3fs (%d channels)",
            time.monotonic() - list_start,
            len(channels),
        )
        rooms: list[Room] = []
        failed_rooms: list[FailedRoom] = []
        all_agents: dict[str, Agent] = {}

        for ch_name, ch_topic in channels:
            # Snapshot the shared agent aggregation so a failed room can be
            # rolled back in place (object identity preserved): a room that
            # is not in the snapshot must not leave its members/channels in
            # the agent list either.
            before_nicks = set(all_agents)
            before_channels = {n: list(a.channels) for n, a in all_agents.items()}
            try:
                room = await _collect_room(
                    reader,
                    writer,
                    nick,
                    ch_name,
                    ch_topic,
                    server_name,
                    all_agents,
                    message_limit,
                )
            except Exception as exc:
                for added_nick in set(all_agents) - before_nicks:
                    del all_agents[added_nick]
                for existing_nick, chans in before_channels.items():
                    all_agents[existing_nick].channels[:] = chans
                phase = getattr(exc, "phase", "unknown")
                cause = getattr(exc, "cause", exc)
                failed_rooms.append(
                    FailedRoom(name=ch_name, error=type(cause).__name__, phase=phase)
                )
                logger.debug(
                    "overview: room %s failed during %s (%s); timings so far: %s",
                    ch_name,
                    phase,
                    type(cause).__name__,
                    getattr(exc, "timings", {}),
                )
                continue
            rooms.append(room)

        fed_links = sorted({a.server for a in all_agents.values() if a.server != server_name})

        # Enrich local agents via daemon IPC
        if ipc_enabled:
            await _enrich_via_ipc(all_agents, server_name)

        # Enrich local agents with TAGS
        tags_start = time.monotonic()
        for agent_nick, agent in all_agents.items():
            if agent.server == server_name:
                agent.tags = await _query_tags(reader, writer, nick, agent_nick)
        logger.debug("overview: TAGS took %.3fs", time.monotonic() - tags_start)

        # Add stopped/registered agents from manifest
        if manifest_agents:
            _inject_stopped_agents(all_agents, manifest_agents, server_name)

        # Collect bot info from disk
        bots = _collect_bots()

        return MeshState(
            server_name=server_name,
            rooms=rooms,
            agents=sorted(all_agents.values(), key=lambda a: a.nick),
            federation_links=fed_links,
            bots=bots,
            failed_rooms=failed_rooms,
        )
    finally:
        await _disconnect(writer)


async def _handle_registration_line(
    msg: IRCMessage,
    writer: asyncio.StreamWriter,
    nick: str,
    server_name: str,
) -> tuple[bool, str]:
    """Process a single line during registration.

    Returns (is_done, current_nick).  *is_done* is True when RPL_WELCOME
    (001) has been received and the connection is ready.
    """
    if msg.command == "PING":
        writer.write(f"PONG :{msg.params[0]}\r\n".encode())
        await writer.drain()
        return False, nick
    if msg.command == "001":
        return True, nick
    if msg.command == "433":
        nick = _temp_nick(server_name)
        writer.write(f"NICK {nick}\r\n".encode())
        await writer.drain()
        return False, nick
    return False, nick


async def _connect(
    host: str,
    port: int,
    server_name: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    """Connect and register as an ephemeral observer."""
    async with asyncio.timeout(REGISTER_TIMEOUT):
        reader, writer = await asyncio.open_connection(host, port)
    nick = _temp_nick(server_name)
    try:
        writer.write(f"NICK {nick}\r\nUSER overview 0 * :overview\r\n".encode())
        await writer.drain()

        deadline = asyncio.get_event_loop().time() + REGISTER_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError("Registration timed out")
            try:
                async with asyncio.timeout(remaining):
                    data = await reader.readline()
            except asyncio.TimeoutError:
                raise TimeoutError("Registration timed out") from None
            line = data.decode().strip()
            if not line:
                continue
            msg = IRCMessage.parse(line)
            done, nick = await _handle_registration_line(msg, writer, nick, server_name)
            if done:
                return reader, writer, nick
    except BaseException:
        await _disconnect(writer)
        raise


async def _disconnect(writer: asyncio.StreamWriter) -> None:
    try:
        writer.write(b"QUIT :overview done\r\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _recv_until(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    stop_commands: set[str],
    *,
    require_stop: bool = False,
) -> list[IRCMessage]:
    """Read IRC messages until a stop command is seen or RECV_TIMEOUT elapses.

    With ``require_stop=True`` a missing stop marker is an error:
    TimeoutError when RECV_TIMEOUT elapsed, ConnectionError on EOF. Core
    IRC queries (NAMES/WHO) have guaranteed end numerics, so silence
    means a hung or dead room — not completion. Extension queries
    (HISTORY/ROOMMETA/TAGS) stay lenient (the default): a server without
    the extension simply never replies, and that is normal.
    """
    messages: list[IRCMessage] = []
    completed = False
    eof = False
    try:
        async with asyncio.timeout(RECV_TIMEOUT):
            while True:
                data = await reader.readline()
                if not data:
                    eof = True
                    break
                line = data.decode().strip()
                if not line:
                    continue
                msg = IRCMessage.parse(line)
                if msg.command == "PING":
                    writer.write(f"PONG :{msg.params[0]}\r\n".encode())
                    await writer.drain()
                    continue
                messages.append(msg)
                if msg.command in stop_commands:
                    completed = True
                    break
    except asyncio.TimeoutError:
        pass
    if require_stop and not completed:
        markers = "/".join(sorted(stop_commands))
        if eof:
            raise ConnectionError(f"connection closed before {markers}")
        raise TimeoutError(f"no {markers} reply within {RECV_TIMEOUT}s")
    return messages


async def _query_list(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    _nick: str,
) -> list[tuple[str, str]]:
    """Query LIST and return [(channel_name, topic)]."""
    writer.write(b"LIST\r\n")
    await writer.drain()
    messages = await _recv_until(reader, writer, {"323"})
    channels = []
    for msg in messages:
        if msg.command == "322" and len(msg.params) >= 4:
            ch_name = msg.params[1]
            topic = msg.params[3] if len(msg.params) > 3 else ""
            channels.append((ch_name, topic))
    return channels


async def _query_names(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    _nick: str,
    channel: str,
) -> tuple[list[tuple[str, bool]], list[str]]:
    """Query NAMES and return [(nick, is_operator)] and [operator_nicks]."""
    writer.write(f"NAMES {channel}\r\n".encode())
    await writer.drain()
    messages = await _recv_until(reader, writer, {"366"}, require_stop=True)
    members = []
    operators = []
    for msg in messages:
        if msg.command == "353" and len(msg.params) >= 4:
            names_str = msg.params[3] if len(msg.params) > 3 else msg.params[-1]
            for name in names_str.split():
                is_op = name.startswith("@")
                clean = name.lstrip("@+")
                members.append((clean, is_op))
                if is_op:
                    operators.append(clean)
    return members, operators


async def _query_who(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    _nick: str,
    channel: str,
) -> dict[str, str]:
    """Query WHO and return {nick: server_name}."""
    writer.write(f"WHO {channel}\r\n".encode())
    await writer.drain()
    messages = await _recv_until(reader, writer, {"315"}, require_stop=True)
    result = {}
    for msg in messages:
        if msg.command == "352" and len(msg.params) >= 6:
            member_nick = msg.params[5]
            member_server = msg.params[4]
            result[member_nick] = member_server
    return result


async def _query_history(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    _nick: str,
    channel: str,
    limit: int,
) -> list[Message]:
    """Query HISTORY RECENT and return Message objects."""
    writer.write(f"HISTORY RECENT {channel} {limit}\r\n".encode())
    await writer.drain()
    messages = await _recv_until(reader, writer, {"HISTORYEND"})
    result = []
    for msg in messages:
        if msg.command == "HISTORY" and len(msg.params) >= 4:
            result.append(
                Message(
                    nick=msg.params[1],
                    text=msg.params[3],
                    timestamp=float(msg.params[2]),
                    channel=channel,
                )
            )
    return result


async def _query_roommeta(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    _nick: str,
    channel: str,
) -> dict:
    """Query ROOMMETA and return a dict with room metadata fields."""
    writer.write(f"ROOMMETA {channel}\r\n".encode())
    await writer.drain()
    messages = await _recv_until(
        reader, writer, {"ROOMETAEND", "ERR_NOSUCHCHANNEL", "ERR_UNKNOWNCOMMAND"}
    )
    result: dict = {}
    for msg in messages:
        if msg.command == "ROOMMETA" and len(msg.params) >= 3:
            # Server sends: ROOMMETA <channel> <key> <value>
            key = msg.params[1].strip().lower()
            value = msg.params[2]
            if key == "room_id":
                result["room_id"] = value
            elif key == "owner":
                result["owner"] = value
            elif key == "purpose":
                result["purpose"] = value
            elif key == "tags":
                result["tags"] = [t.strip() for t in value.split(",") if t.strip()]
            elif key == "persistent":
                result["persistent"] = value.lower() in ("1", "true", "yes")
    return result


async def _query_tags(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    _nick: str,
    target_nick: str,
) -> list[str]:
    """Query TAGS for an agent and return a list of tag strings."""
    writer.write(f"TAGS {target_nick}\r\n".encode())
    await writer.drain()
    messages = await _recv_until(
        reader, writer, {"TAGSEND", "ERR_NOSUCHNICK", "ERR_UNKNOWNCOMMAND"}
    )
    for msg in messages:
        if msg.command == "TAGS" and len(msg.params) >= 2:
            # Expected format: TAGS <nick> <tag1,tag2,...>
            tags_str = msg.params[-1]
            return [t.strip() for t in tags_str.split(",") if t.strip()]
    return []


def _collect_bots() -> list[BotInfo]:
    """Read bot configs from ~/.culture/bots/ on disk."""
    from culture_core.bots.config import BOTS_DIR, load_bot_config

    bots = []
    if not BOTS_DIR.is_dir():
        return bots

    for bot_dir in sorted(BOTS_DIR.iterdir()):
        yaml_path = bot_dir / BOT_CONFIG_FILE
        if not yaml_path.is_file():
            continue
        try:
            config = load_bot_config(yaml_path)
            bots.append(
                BotInfo(
                    name=config.name,
                    owner=config.owner,
                    trigger_type=config.trigger_type,
                    channels=config.channels,
                    status="configured",  # disk-only; live status requires IPC
                    description=config.description,
                    mention=config.mention,
                    archived=config.archived,
                )
            )
        except Exception:
            continue
    return bots


async def _enrich_via_ipc(agents: dict[str, Agent], server_name: str) -> None:
    """Enrich local agents with daemon IPC status data."""
    from culture_core.clients.shared.ipc import decode_message, encode_message, make_request

    socket_pattern = os.path.join(culture_runtime_dir(), "culture-*.sock")

    for sock_path in glob.glob(socket_pattern):
        # Extract nick from socket filename: culture-<nick>.sock
        basename = os.path.basename(sock_path)
        agent_nick = basename[len("culture-") : -len(".sock")]

        if agent_nick not in agents:
            continue

        agent = agents[agent_nick]
        if agent.server != server_name:
            continue

        try:
            async with asyncio.timeout(3.0):
                r, w = await asyncio.open_unix_connection(sock_path)
            req = make_request("status")
            w.write(encode_message(req))
            await w.drain()

            async with asyncio.timeout(3.0):
                data = await r.readline()
            resp = decode_message(data)

            if resp and resp.get("type") == "response" and resp.get("ok"):
                info = resp.get("data", {})
                agent.activity = info.get("description", "")
                agent.turns = info.get("turn_count")
                if info.get("circuit_open"):
                    agent.status = "circuit-open"
                elif info.get("paused"):
                    agent.status = "paused"

            w.close()
            await w.wait_closed()
        except Exception:
            pass
