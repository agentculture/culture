"""Tests for overview collector against a real IRC server."""

import asyncio
import os
from unittest.mock import patch

import pytest

from culture.constants import SYSTEM_CHANNEL, SYSTEM_USER_PREFIX
from culture.overview import collector as collector_mod
from culture.overview.collector import (
    _collect_bots,
    _enrich_via_ipc,
    _handle_registration_line,
    _inject_stopped_agents,
    _query_roommeta,
    _query_tags,
    _recv_until,
    _temp_nick,
    collect_mesh_state,
)
from culture.overview.model import Agent, MeshState
from culture.protocol.message import Message as IRCMessage


@pytest.mark.asyncio
async def test_collect_empty_server(server):
    """Collecting from an empty server returns no user rooms or agents.

    #system is always present (auto-created at IRCd startup) but is a
    server-internal channel — it is filtered out of the user-visible
    room/agent lists here.
    """
    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    assert isinstance(mesh, MeshState)
    assert mesh.server_name == server.config.name
    user_rooms = [r for r in mesh.rooms if r.name != SYSTEM_CHANNEL]
    user_agents = [a for a in mesh.agents if not a.nick.startswith(SYSTEM_USER_PREFIX)]
    assert user_rooms == []
    assert user_agents == []


@pytest.mark.asyncio
async def test_collect_with_agent_in_channel(server, make_client):
    """Collecting sees agents and channels (excluding the server-internal #system room)."""
    client = await make_client(nick="testserv-agent1", user="agent1")
    await client.send("JOIN #testing")
    await client.recv_all(timeout=0.5)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    user_rooms = [r for r in mesh.rooms if r.name != SYSTEM_CHANNEL]
    assert len(user_rooms) == 1
    testing_room = user_rooms[0]
    assert testing_room.name == "#testing"
    assert len(testing_room.members) >= 1
    found = any(a.nick == "testserv-agent1" for a in testing_room.members)
    assert found


@pytest.mark.asyncio
async def test_collect_sees_topic(server, make_client):
    """Collecting includes channel topics."""
    client = await make_client(nick="testserv-agent1", user="agent1")
    await client.send("JOIN #testing")
    await client.recv_all(timeout=0.5)
    await client.send("TOPIC #testing :Hello world topic")
    await client.recv_all(timeout=0.5)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    testing_room = next(r for r in mesh.rooms if r.name == "#testing")
    assert testing_room.topic == "Hello world topic"


@pytest.mark.asyncio
async def test_collect_sees_messages(server, make_client):
    """Collecting includes recent messages via HISTORY."""
    client = await make_client(nick="testserv-agent1", user="agent1")
    await client.send("JOIN #testing")
    await client.recv_all(timeout=0.5)
    await client.send("PRIVMSG #testing :test message one")
    await client.send("PRIVMSG #testing :test message two")
    await asyncio.sleep(0.3)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    testing_room = next(r for r in mesh.rooms if r.name == "#testing")
    # History may also include lifecycle entries; assert that the sent PRIVMSG
    # texts are present in the collected message texts.
    texts = [m.text for m in testing_room.messages]
    assert "test message one" in texts
    assert "test message two" in texts


@pytest.mark.asyncio
async def test_collect_multiple_rooms(server, make_client):
    """Collecting sees all rooms."""
    c1 = await make_client(nick="testserv-a", user="a")
    c2 = await make_client(nick="testserv-b", user="b")
    await c1.send("JOIN #room1")
    await c2.send("JOIN #room2")
    await c1.recv_all(timeout=0.5)
    await c2.recv_all(timeout=0.5)

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
    )
    room_names = sorted(r.name for r in mesh.rooms)
    assert "#room1" in room_names
    assert "#room2" in room_names


def test_collect_bots_passes_archived_flag(tmp_path):
    """Issue #184: _collect_bots should populate BotInfo.archived from config."""
    # Create a bot directory with an archived bot config (nested YAML format)
    bot_dir = tmp_path / "test-bot"
    bot_dir.mkdir()
    (bot_dir / "bot.yaml").write_text(
        "bot:\n"
        "  name: test-bot\n"
        "  owner: spark\n"
        "  archived: true\n"
        "  archived_at: '2026-01-01'\n"
        "  archived_reason: testing\n"
        "trigger:\n"
        "  type: webhook\n"
        "output:\n"
        "  channels:\n"
        "    - '#general'\n"
    )

    # Create a non-archived bot
    active_dir = tmp_path / "active-bot"
    active_dir.mkdir()
    (active_dir / "bot.yaml").write_text(
        "bot:\n"
        "  name: active-bot\n"
        "  owner: spark\n"
        "trigger:\n"
        "  type: mention\n"
        "output:\n"
        "  channels:\n"
        "    - '#general'\n"
    )

    with patch("culture.bots.config.BOTS_DIR", tmp_path):
        bots = _collect_bots()

    assert len(bots) == 2
    archived_bot = next(b for b in bots if b.name == "test-bot")
    active_bot = next(b for b in bots if b.name == "active-bot")
    assert archived_bot.archived is True
    assert active_bot.archived is False


# ---------------------------------------------------------------------------
# Phase 4b additions — pure helpers, registration handler, IPC enrichment
# ---------------------------------------------------------------------------


def test_temp_nick_shape():
    nick = _temp_nick("spark")
    assert nick.startswith("spark-_overview")
    assert len(nick) == len("spark-_overview") + 4


def test_inject_stopped_agents_adds_missing_with_manifest_metadata():
    all_agents: dict[str, Agent] = {}

    class _Cfg:
        def __init__(self, nick, channels, archived=False, backend=None, directory=None):
            self.nick = nick
            self.channels = channels
            self.archived = archived
            self.backend = backend
            self.directory = directory

    manifest = [
        _Cfg("srv-claude", ["#general"], backend="claude", directory="/tmp/c"),
        _Cfg("srv-archived", ["#general"], archived=True),  # skipped
    ]
    _inject_stopped_agents(all_agents, manifest, "srv")
    assert "srv-claude" in all_agents
    assert all_agents["srv-claude"].status == "stopped"
    assert all_agents["srv-claude"].backend == "claude"
    assert all_agents["srv-claude"].directory == "/tmp/c"
    assert all_agents["srv-claude"].channels == ["#general"]
    assert "srv-archived" not in all_agents


def test_inject_stopped_agents_skips_when_already_present():
    all_agents = {
        "srv-claude": Agent(
            nick="srv-claude", status="active", activity="", channels=[], server="srv"
        ),
    }

    class _Cfg:
        def __init__(self):
            self.nick = "srv-claude"
            self.channels = []
            self.archived = False
            self.backend = "claude"
            self.directory = None

    _inject_stopped_agents(all_agents, [_Cfg()], "srv")
    assert all_agents["srv-claude"].status == "active"  # not overwritten


def test_inject_stopped_agents_non_list_channels_falls_back():
    all_agents: dict[str, Agent] = {}

    class _Cfg:
        def __init__(self):
            self.nick = "srv-bot"
            self.channels = None  # not a list
            self.archived = False
            self.backend = None
            self.directory = None

    _inject_stopped_agents(all_agents, [_Cfg()], "srv")
    assert all_agents["srv-bot"].channels == []


# ---- _handle_registration_line direct unit tests ----------------------------


class _CollectorWriter:
    def __init__(self):
        self.sent: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.sent.append(data)

    async def drain(self):
        return None


@pytest.mark.asyncio
async def test_handle_registration_line_responds_to_ping():
    writer = _CollectorWriter()
    msg = IRCMessage.parse("PING :tok")
    done, nick = await _handle_registration_line(msg, writer, "n", "srv")  # type: ignore[arg-type]
    assert done is False
    assert nick == "n"
    assert writer.sent == [b"PONG :tok\r\n"]


@pytest.mark.asyncio
async def test_handle_registration_line_001_signals_done():
    writer = _CollectorWriter()
    msg = IRCMessage.parse(":srv 001 me :Welcome")
    done, nick = await _handle_registration_line(msg, writer, "n", "srv")  # type: ignore[arg-type]
    assert done is True
    assert nick == "n"
    assert writer.sent == []


@pytest.mark.asyncio
async def test_handle_registration_line_433_retries_new_nick():
    writer = _CollectorWriter()
    msg = IRCMessage.parse(":srv 433 * me :Nickname in use")
    done, nick = await _handle_registration_line(msg, writer, "old", "srv")  # type: ignore[arg-type]
    assert done is False
    assert nick != "old"
    assert nick.startswith("srv-_overview")
    assert writer.sent and writer.sent[0].startswith(b"NICK ")


@pytest.mark.asyncio
async def test_handle_registration_line_unknown_command_passes_through():
    writer = _CollectorWriter()
    msg = IRCMessage.parse(":srv 002 me :Your host is")
    done, nick = await _handle_registration_line(msg, writer, "n", "srv")  # type: ignore[arg-type]
    assert done is False
    assert nick == "n"
    assert writer.sent == []


# ---- _recv_until direct unit tests ------------------------------------------


class _FakeReader:
    """Yields canned lines, then blocks (simulating no more data)."""

    def __init__(self, lines: list[bytes], block_after: bool = True):
        self._lines = list(lines)
        self._block_after = block_after

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        if self._block_after:
            await asyncio.sleep(10)  # forces _recv_until's timeout
        return b""


@pytest.mark.asyncio
async def test_recv_until_stops_on_stop_command():
    reader = _FakeReader([b":srv 322 me #room 0 :topic\r\n", b":srv 323 me :End\r\n"])
    writer = _CollectorWriter()
    msgs = await _recv_until(reader, writer, {"323"}, timeout=1.0)  # type: ignore[arg-type]
    cmds = [m.command for m in msgs]
    assert "322" in cmds
    assert cmds[-1] == "323"


@pytest.mark.asyncio
async def test_recv_until_ponging_a_ping_does_not_emit_message():
    reader = _FakeReader([b"PING :tok\r\n", b":srv 323 me :End\r\n"])
    writer = _CollectorWriter()
    msgs = await _recv_until(reader, writer, {"323"}, timeout=1.0)  # type: ignore[arg-type]
    cmds = [m.command for m in msgs]
    assert "PING" not in cmds
    assert "323" in cmds
    assert writer.sent and writer.sent[0] == b"PONG :tok\r\n"


@pytest.mark.asyncio
async def test_recv_until_skips_blank_lines_and_returns_on_timeout():
    reader = _FakeReader([b"\r\n"], block_after=True)
    writer = _CollectorWriter()
    msgs = await _recv_until(reader, writer, {"323"}, timeout=0.1)  # type: ignore[arg-type]
    assert msgs == []


# ---- _query_roommeta / _query_tags via stubbed reader -----------------------


@pytest.mark.asyncio
async def test_query_roommeta_parses_all_keys(monkeypatch):
    """Cover the ROOMMETA key parser: room_id / owner / purpose / tags / persistent."""
    reader = _FakeReader(
        [
            b":srv ROOMMETA #room room_id :abc123\r\n",
            b":srv ROOMMETA #room owner :srv-ori\r\n",
            b":srv ROOMMETA #room purpose :for testing\r\n",
            b":srv ROOMMETA #room tags :alpha, beta ,gamma\r\n",
            b":srv ROOMMETA #room persistent :true\r\n",
            b":srv ROOMETAEND #room\r\n",
        ]
    )
    writer = _CollectorWriter()
    result = await _query_roommeta(reader, writer, "me", "#room")  # type: ignore[arg-type]
    assert result["room_id"] == "abc123"
    assert result["owner"] == "srv-ori"
    assert result["purpose"] == "for testing"
    assert result["tags"] == ["alpha", "beta", "gamma"]
    assert result["persistent"] is True


@pytest.mark.asyncio
async def test_query_roommeta_unknown_command_terminates_empty():
    reader = _FakeReader([b":srv ERR_UNKNOWNCOMMAND ROOMMETA :Unknown\r\n"])
    writer = _CollectorWriter()
    result = await _query_roommeta(reader, writer, "me", "#room")  # type: ignore[arg-type]
    assert result == {}


@pytest.mark.asyncio
async def test_query_roommeta_persistent_false_for_anything_else():
    reader = _FakeReader(
        [
            b":srv ROOMMETA #room persistent :no\r\n",
            b":srv ROOMETAEND #room\r\n",
        ]
    )
    writer = _CollectorWriter()
    result = await _query_roommeta(reader, writer, "me", "#room")  # type: ignore[arg-type]
    assert result["persistent"] is False


@pytest.mark.asyncio
async def test_query_tags_returns_list():
    reader = _FakeReader(
        [
            b":srv TAGS srv-bot :focus, async , devops\r\n",
            b":srv TAGSEND srv-bot\r\n",
        ]
    )
    writer = _CollectorWriter()
    tags = await _query_tags(reader, writer, "me", "srv-bot")  # type: ignore[arg-type]
    assert tags == ["focus", "async", "devops"]


@pytest.mark.asyncio
async def test_query_tags_returns_empty_when_no_such_nick():
    reader = _FakeReader([b":srv ERR_NOSUCHNICK srv-bot :No such nick\r\n"])
    writer = _CollectorWriter()
    tags = await _query_tags(reader, writer, "me", "srv-bot")  # type: ignore[arg-type]
    assert tags == []


# ---- _collect_bots edge cases -----------------------------------------------


def test_collect_bots_returns_empty_when_dir_missing(tmp_path):
    missing = tmp_path / "no-such-dir"
    with patch("culture.bots.config.BOTS_DIR", missing):
        assert _collect_bots() == []


def test_collect_bots_skips_dirs_without_yaml(tmp_path):
    (tmp_path / "no-yaml-here").mkdir()
    with patch("culture.bots.config.BOTS_DIR", tmp_path):
        assert _collect_bots() == []


def test_collect_bots_swallows_malformed_yaml(tmp_path):
    bot_dir = tmp_path / "broken-bot"
    bot_dir.mkdir()
    (bot_dir / "bot.yaml").write_text("this is: not: a valid: bot: config\n")
    with patch("culture.bots.config.BOTS_DIR", tmp_path):
        # The malformed entry is silently dropped, not raised.
        assert _collect_bots() == []


# ---- _enrich_via_ipc with a real Unix socket --------------------------------


@pytest.mark.asyncio
async def test_enrich_via_ipc_status_response_populates_agent(tmp_path, monkeypatch):
    """Happy path: socket returns a status response → agent fields populated."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sock_path = runtime / "culture-srv-claude.sock"

    async def handler(reader, writer):
        try:
            await reader.readline()
            from culture.clients.shared.ipc import encode_message

            writer.write(
                encode_message(
                    {
                        "type": "response",
                        "ok": True,
                        "data": {
                            "description": "thinking",
                            "turn_count": 7,
                            "circuit_open": False,
                            "paused": False,
                        },
                    }
                )
            )
            await writer.drain()
            writer.close()
        except Exception:
            pass

    srv = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        monkeypatch.setattr(collector_mod, "culture_runtime_dir", lambda: str(runtime))

        agents = {
            "srv-claude": Agent(
                nick="srv-claude",
                status="active",
                activity="",
                channels=[],
                server="srv",
            ),
        }
        await _enrich_via_ipc(agents, "srv")
        assert agents["srv-claude"].activity == "thinking"
        assert agents["srv-claude"].turns == 7
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_enrich_via_ipc_circuit_open_sets_status(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sock_path = runtime / "culture-srv-codex.sock"

    async def handler(reader, writer):
        try:
            await reader.readline()
            from culture.clients.shared.ipc import encode_message

            writer.write(
                encode_message(
                    {
                        "type": "response",
                        "ok": True,
                        "data": {"description": "waiting", "circuit_open": True},
                    }
                )
            )
            await writer.drain()
            writer.close()
        except Exception:
            pass

    srv = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        monkeypatch.setattr(collector_mod, "culture_runtime_dir", lambda: str(runtime))
        agents = {
            "srv-codex": Agent(
                nick="srv-codex",
                status="active",
                activity="",
                channels=[],
                server="srv",
            ),
        }
        await _enrich_via_ipc(agents, "srv")
        assert agents["srv-codex"].status == "circuit-open"
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_enrich_via_ipc_paused_sets_status(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sock_path = runtime / "culture-srv-acp.sock"

    async def handler(reader, writer):
        try:
            await reader.readline()
            from culture.clients.shared.ipc import encode_message

            writer.write(
                encode_message(
                    {
                        "type": "response",
                        "ok": True,
                        "data": {"description": "idle", "paused": True},
                    }
                )
            )
            await writer.drain()
            writer.close()
        except Exception:
            pass

    srv = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        monkeypatch.setattr(collector_mod, "culture_runtime_dir", lambda: str(runtime))
        agents = {
            "srv-acp": Agent(
                nick="srv-acp",
                status="active",
                activity="",
                channels=[],
                server="srv",
            ),
        }
        await _enrich_via_ipc(agents, "srv")
        assert agents["srv-acp"].status == "paused"
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_enrich_via_ipc_skips_unknown_agent_socket(tmp_path, monkeypatch):
    """Socket name doesn't match any agent → no error, no mutation."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    # Touch a socket file via an actual UNIX server so glob() sees it.
    sock_path = runtime / "culture-stranger.sock"

    async def handler(_r, w):
        w.close()

    srv = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        monkeypatch.setattr(collector_mod, "culture_runtime_dir", lambda: str(runtime))
        agents = {
            "srv-claude": Agent(
                nick="srv-claude",
                status="active",
                activity="",
                channels=[],
                server="srv",
            ),
        }
        before = agents["srv-claude"].activity
        await _enrich_via_ipc(agents, "srv")
        assert agents["srv-claude"].activity == before  # unchanged
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_enrich_via_ipc_skips_remote_agent(tmp_path, monkeypatch):
    """Agent on a foreign server is skipped even if a socket exists."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sock_path = runtime / "culture-thor-claude.sock"

    called: list[bool] = []

    async def handler(reader, writer):
        called.append(True)
        try:
            await reader.readline()
            writer.close()
        except Exception:
            pass

    srv = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        monkeypatch.setattr(collector_mod, "culture_runtime_dir", lambda: str(runtime))
        agents = {
            "thor-claude": Agent(
                nick="thor-claude",
                status="active",
                activity="",
                channels=[],
                server="thor",
            ),
        }
        await _enrich_via_ipc(agents, "srv")
        # Server short-circuits before reading; agent unchanged.
        assert agents["thor-claude"].activity == ""
        assert called == []
    finally:
        srv.close()
        await srv.wait_closed()


@pytest.mark.asyncio
async def test_enrich_via_ipc_swallows_connection_refused(tmp_path, monkeypatch):
    """A dangling sock file that can't be connected to is silently skipped."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sock_path = runtime / "culture-srv-stale.sock"
    # Create a regular file at the socket path so glob picks it up but
    # open_unix_connection fails.
    sock_path.write_text("not a socket")

    monkeypatch.setattr(collector_mod, "culture_runtime_dir", lambda: str(runtime))

    agents = {
        "srv-stale": Agent(
            nick="srv-stale",
            status="active",
            activity="",
            channels=[],
            server="srv",
        ),
    }
    # Should not raise — the broad except in _enrich_via_ipc swallows.
    await _enrich_via_ipc(agents, "srv")
    assert agents["srv-stale"].activity == ""


@pytest.mark.asyncio
async def test_enrich_via_ipc_skips_when_response_not_ok(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    sock_path = runtime / "culture-srv-noop.sock"

    async def handler(reader, writer):
        try:
            await reader.readline()
            from culture.clients.shared.ipc import encode_message

            writer.write(encode_message({"type": "response", "ok": False, "data": {}}))
            await writer.drain()
            writer.close()
        except Exception:
            pass

    srv = await asyncio.start_unix_server(handler, path=str(sock_path))
    try:
        monkeypatch.setattr(collector_mod, "culture_runtime_dir", lambda: str(runtime))
        agents = {
            "srv-noop": Agent(
                nick="srv-noop",
                status="active",
                activity="",
                channels=[],
                server="srv",
            ),
        }
        await _enrich_via_ipc(agents, "srv")
        # Response was not ok — activity stays empty.
        assert agents["srv-noop"].activity == ""
    finally:
        srv.close()
        await srv.wait_closed()


# ---- collect_mesh_state with manifest_agents end-to-end ----------------------


@pytest.mark.asyncio
async def test_collect_mesh_state_injects_stopped_manifest_agents(server):
    """Stopped agents from manifest_agents appear in `mesh.agents` even though
    they're not on IRC."""

    class _ManifestCfg:
        def __init__(self, nick):
            self.nick = nick
            self.channels = ["#general"]
            self.archived = False
            self.backend = "claude"
            self.directory = None

    mesh = await collect_mesh_state(
        host="127.0.0.1",
        port=server.config.port,
        server_name=server.config.name,
        message_limit=4,
        ipc_enabled=False,
        manifest_agents=[_ManifestCfg("testserv-stopped")],
    )
    found = next((a for a in mesh.agents if a.nick == "testserv-stopped"), None)
    assert found is not None
    assert found.status == "stopped"
