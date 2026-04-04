"""Collect mesh state via IRC Observer queries and daemon IPC."""

from __future__ import annotations

import asyncio
import glob
import os

from agentirc.protocol.message import Message as IRCMessage

from .model import Agent, BotInfo, MeshState, Message, Room

RECV_TIMEOUT = 5.0
REGISTER_TIMEOUT = 10.0


def _temp_nick(server_name: str) -> str:
    return f"{server_name}-_overview{os.urandom(2).hex()}"


async def collect_mesh_state(
    host: str,
    port: int,
    server_name: str,
    message_limit: int = 4,
    ipc_enabled: bool = True,
) -> MeshState:
    """Collect a full mesh snapshot.

    Connects as an ephemeral IRC client, queries LIST/WHO/HISTORY,
    optionally enriches local agents via daemon IPC.
    """
    reader, writer, nick = await _connect(host, port, server_name)
    try:
        channels = await _query_list(reader, writer, nick)
        rooms: list[Room] = []
        all_agents: dict[str, Agent] = {}

        for ch_name, ch_topic in channels:
            members, _ = await _query_names(reader, writer, nick, ch_name)
            who_data = await _query_who(reader, writer, nick, ch_name)
            messages = await _query_history(reader, writer, nick, ch_name, message_limit)

            room_agents = []
            fed_servers: set[str] = set()
            for member_nick, is_op in members:
                server_of = who_data.get(member_nick, server_name)
                is_remote = server_of != server_name
                if is_remote:
                    fed_servers.add(server_of)

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
                room_agents.append(agent)

            op_nicks = [n for n, is_op in members if is_op]
            room_meta = await _query_roommeta(reader, writer, nick, ch_name)
            rooms.append(
                Room(
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
                )
            )

        fed_links = sorted({a.server for a in all_agents.values() if a.server != server_name})

        # Enrich local agents via daemon IPC
        if ipc_enabled:
            await _enrich_via_ipc(all_agents, server_name)

        # Enrich local agents with TAGS
        for agent_nick, agent in all_agents.items():
            if agent.server == server_name:
                agent.tags = await _query_tags(reader, writer, nick, agent_nick)

        # Collect bot info from disk
        bots = _collect_bots()

        return MeshState(
            server_name=server_name,
            rooms=rooms,
            agents=sorted(all_agents.values(), key=lambda a: a.nick),
            federation_links=fed_links,
            bots=bots,
        )
    finally:
        await _disconnect(writer)


async def _connect(
    host: str,
    port: int,
    server_name: str,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    """Connect and register as an ephemeral observer."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port),
        timeout=REGISTER_TIMEOUT,
    )
    nick = _temp_nick(server_name)
    writer.write(f"NICK {nick}\r\nUSER overview 0 * :overview\r\n".encode())
    await writer.drain()

    deadline = asyncio.get_event_loop().time() + REGISTER_TIMEOUT
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("Registration timed out")
        data = await asyncio.wait_for(reader.readline(), timeout=remaining)
        line = data.decode().strip()
        if not line:
            continue
        msg = IRCMessage.parse(line)
        if msg.command == "PING":
            writer.write(f"PONG :{msg.params[0]}\r\n".encode())
            await writer.drain()
        elif msg.command == "001":
            return reader, writer, nick
        elif msg.command == "433":
            nick = _temp_nick(server_name)
            writer.write(f"NICK {nick}\r\n".encode())
            await writer.drain()


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
    timeout: float = RECV_TIMEOUT,
) -> list[IRCMessage]:
    """Read IRC messages until a stop command is seen."""
    messages = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        try:
            data = await asyncio.wait_for(reader.readline(), timeout=remaining)
        except asyncio.TimeoutError:
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
            break
    return messages


async def _query_list(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    nick: str,
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
    nick: str,
    channel: str,
) -> tuple[list[tuple[str, bool]], list[str]]:
    """Query NAMES and return [(nick, is_operator)] and [operator_nicks]."""
    writer.write(f"NAMES {channel}\r\n".encode())
    await writer.drain()
    messages = await _recv_until(reader, writer, {"366"})
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
    nick: str,
    channel: str,
) -> dict[str, str]:
    """Query WHO and return {nick: server_name}."""
    writer.write(f"WHO {channel}\r\n".encode())
    await writer.drain()
    messages = await _recv_until(reader, writer, {"315"})
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
    nick: str,
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
    nick: str,
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
    nick: str,
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
    """Read bot configs from ~/.agentirc/bots/ on disk."""
    from agentirc.bots.config import BOTS_DIR, load_bot_config

    bots = []
    if not BOTS_DIR.is_dir():
        return bots

    for bot_dir in sorted(BOTS_DIR.iterdir()):
        yaml_path = bot_dir / "bot.yaml"
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
                    status="active",  # from disk we assume active; live status requires IPC
                    description=config.description,
                    mention=config.mention,
                )
            )
        except Exception:
            continue
    return bots


async def _enrich_via_ipc(agents: dict[str, Agent], server_name: str) -> None:
    """Enrich local agents with daemon IPC status data."""
    from agentirc.clients.claude.ipc import decode_message, encode_message, make_request

    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    socket_pattern = os.path.join(runtime_dir, "agentirc-*.sock")

    for sock_path in glob.glob(socket_pattern):
        # Extract nick from socket filename: agentirc-<nick>.sock
        basename = os.path.basename(sock_path)
        agent_nick = basename[len("agentirc-") : -len(".sock")]

        if agent_nick not in agents:
            continue

        agent = agents[agent_nick]
        if agent.server != server_name:
            continue

        try:
            r, w = await asyncio.wait_for(
                asyncio.open_unix_connection(sock_path),
                timeout=3.0,
            )
            req = make_request("status")
            w.write(encode_message(req))
            await w.drain()

            data = await asyncio.wait_for(r.readline(), timeout=3.0)
            resp = decode_message(data)

            if resp and resp.get("type") == "response" and resp.get("ok"):
                info = resp.get("data", {})
                agent.activity = info.get("description", "")
                agent.turns = info.get("turn_count")
                if info.get("paused"):
                    agent.status = "paused"

            w.close()
            await w.wait_closed()
        except Exception:
            pass
