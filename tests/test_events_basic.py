"""End-to-end: `emit_event` surfaces a tagged PRIVMSG from `system-<server>`."""

import asyncio
import base64
import json
from unittest.mock import patch

import pytest

from culture.agentirc.config import ServerConfig
from culture.agentirc.ircd import IRCd
from culture.agentirc.skill import Event, EventType
from tests.conftest import IRCTestClient


@pytest.mark.asyncio
async def test_event_nick_from_event_field_populates_payload(server, make_client):
    """Emitters that set Event.nick (not data['nick']) still produce correct
    payload + body — payload.setdefault('nick', event.nick) covers them."""
    c = await make_client("testserv-alice", "alice")
    await c.send("CAP REQ :message-tags")
    await c.recv_until("CAP")
    await c.send("JOIN #general")
    await c.recv_until("366")
    await asyncio.sleep(0.05)
    await c.recv_all(timeout=0.2)

    ev = Event(
        type=EventType.JOIN,
        channel="#general",
        nick="testserv-bob",
        data={},  # NO data['nick'] — only Event.nick
    )
    await server.emit_event(ev)

    line = await c.recv_until("event=user.join")
    assert "testserv-bob joined" in line  # template rendered actor
    # Decode payload and confirm 'nick' was populated from Event.nick
    at_idx = line.find("@")
    space_idx = line.find(" ", at_idx)
    tag_blob = line[at_idx + 1 : space_idx]
    data_piece = [p for p in tag_blob.split(";") if p.startswith("event-data=")][0]
    decoded = json.loads(base64.b64decode(data_piece.split("=", 1)[1]))
    assert decoded["nick"] == "testserv-bob"


@pytest.mark.asyncio
async def test_unserializable_payload_does_not_crash(server, make_client):
    """An event with a non-JSON-serializable value surfaces with empty payload
    rather than crashing emit_event."""
    c = await make_client("testserv-alice", "alice")
    await c.send("CAP REQ :message-tags")
    await c.recv_until("CAP")
    await c.send("JOIN #system")
    await c.recv_until("366")
    await asyncio.sleep(0.05)
    await c.recv_all(timeout=0.2)

    class Unserializable:
        pass

    ev = Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-testserv",
        data={"nick": "testserv-bob", "obj": Unserializable()},
    )
    # Must not raise.
    await server.emit_event(ev)

    line = await c.recv_until("event=agent.connect")
    # Body still rendered correctly (Unserializable wasn't needed for render)
    assert "testserv-bob connected" in line
    # Payload is empty (encoded {}) — confirm no Unserializable repr leaked
    assert "Unserializable" not in line


@pytest.mark.asyncio
async def test_event_surfaces_as_tagged_privmsg(server, make_client):
    """A tag-capable client in #system receives a tagged PRIVMSG on emit_event."""
    c = await make_client("testserv-alice", "alice")
    await c.send("CAP REQ :message-tags")
    await c.recv_until("ACK")
    await c.send("JOIN #system")
    # Drain all JOIN responses including the join-event PRIVMSG that fires immediately.
    await c.recv_until("366")  # end of NAMES
    await asyncio.sleep(0.05)
    await c.recv_all(timeout=0.2)  # flush any queued join-event PRIVMSG

    # Simulate a server-originated event.
    ev = Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-testserv",
        data={"nick": "testserv-bob"},
    )
    await server.emit_event(ev)

    line = await c.recv_until("agent.connect")
    assert line.startswith("@") or "@event=" in line
    assert "event=agent.connect" in line
    assert "event-data=" in line
    assert ":system-testserv!" in line
    assert " PRIVMSG #system :" in line
    assert "testserv-bob connected" in line


@pytest.mark.asyncio
async def test_channel_scoped_event_goes_to_channel(server, make_client):
    """A channel-scoped event is posted to its channel, not #system."""
    c = await make_client("testserv-alice", "alice")
    await c.send("CAP REQ :message-tags")
    await c.recv_until("ACK")
    await c.send("JOIN #general")
    # Drain all JOIN responses including the immediate join-event PRIVMSG.
    await c.recv_until("366")  # end of NAMES
    await asyncio.sleep(0.05)
    await c.recv_all(timeout=0.2)  # flush any queued join-event PRIVMSGs

    ev = Event(
        type=EventType.JOIN,
        channel="#general",
        nick="testserv-bob",
        data={"nick": "testserv-bob"},
    )
    await server.emit_event(ev)

    line = await c.recv_until("event=user.join")
    assert " PRIVMSG #general :" in line
    # EventType.JOIN.value is "user.join"
    assert "event=user.join" in line


@pytest.mark.asyncio
async def test_event_data_is_base64_json(server, make_client):
    c = await make_client("testserv-alice", "alice")
    await c.send("CAP REQ :message-tags")
    await c.recv_until("ACK")
    await c.send("JOIN #system")
    # Drain all JOIN responses including the immediate join-event PRIVMSG.
    await c.recv_until("366")  # end of NAMES
    await asyncio.sleep(0.05)
    await c.recv_all(timeout=0.2)  # flush any queued join-event PRIVMSGs

    ev = Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="system-testserv",
        data={"nick": "testserv-bob"},
    )
    await server.emit_event(ev)

    line = await c.recv_until("agent.connect")
    # Extract the tag block from the received line(s)
    for raw_line in line.split("\r\n"):
        if "agent.connect" in raw_line:
            line = raw_line
            break
    if line.startswith("@"):
        tags = line.split(" ", 1)[0][1:]
    else:
        # find the @ block in the line
        at_idx = line.find("@")
        space_idx = line.find(" ", at_idx)
        tags = line[at_idx + 1 : space_idx]
    data_piece = [p for p in tags.split(";") if p.startswith("event-data=")][0]
    b64 = data_piece.split("=", 1)[1]
    decoded = json.loads(base64.b64decode(b64))
    assert decoded["nick"] == "testserv-bob"


@pytest.mark.asyncio
async def test_federated_event_uses_origin_prefix(server, make_client):
    """An event with _origin set surfaces with system-<origin> prefix.

    This locks in the contract Task 12 (SEVENT federation relay) will
    consume on the receive side: federated events surface locally with
    the originating peer's system user as the message source.
    """
    c = await make_client("testserv-alice", "alice")
    await c.send("CAP REQ :message-tags")
    await c.recv_until("ACK")
    await c.send("JOIN #system")
    await c.recv_until("366")  # end of NAMES
    await asyncio.sleep(0.05)
    await c.recv_all(timeout=0.2)  # flush any queued join-event PRIVMSG

    ev = Event(
        type=EventType.AGENT_CONNECT,
        channel=None,
        nick="alpha-bob",
        data={"_origin": "alpha", "nick": "alpha-bob"},
    )
    await server.emit_event(ev)

    line = await c.recv_until("event=agent.connect")
    assert ":system-alpha!system@alpha" in line
    # Internal _-prefixed keys are NOT in the encoded payload
    assert "_origin" not in line


@pytest.mark.asyncio
async def test_server_wake_emitted_on_start(server):
    """server.wake is emitted during IRCd.start() and recorded in _event_log.

    We assert via _event_log introspection rather than HISTORY RECENT because
    HistorySkill only stores MESSAGE events (Task 13 extends it to lifecycle
    events). The _event_log is the canonical emission record: every event that
    passes through emit_event() is appended here unconditionally.
    """
    wake_events = [ev for _seq, ev in server._event_log if ev.type == EventType.SERVER_WAKE]
    assert wake_events, "Expected at least one SERVER_WAKE event in _event_log after start()"
    ev = wake_events[0]
    assert ev.channel is None
    assert ev.data.get("server") == server.config.name


@pytest.mark.asyncio
async def test_server_sleep_emitted_on_stop(tmp_path):
    """server.sleep surfaces as a tagged PRIVMSG to #system before teardown.

    We construct a dedicated IRCd instance for this test rather than using the
    shared `server` fixture because the test calls stop() itself — which would
    leave the fixture's own teardown calling stop() a second time on an
    already-stopped server. Using a fresh instance keeps fixture teardown
    trivially safe and makes the test self-contained.

    The test verifies both that the event is emitted AND that the surfacing
    reaches connected clients before any socket teardown (i.e. stop() emits
    server.sleep at the very top).
    """
    empty_bots = tmp_path / "_bots"
    empty_bots.mkdir()
    config = ServerConfig(name="testserv", host="127.0.0.1", port=0, webhook_port=0)

    with (
        patch("culture.bots.bot_manager.BOTS_DIR", empty_bots),
        patch("culture.bots.config.BOTS_DIR", empty_bots),
        patch("culture.bots.bot.BOTS_DIR", empty_bots),
    ):
        ircd = IRCd(config)
        await ircd.start()
        ircd.config.port = ircd._server.sockets[0].getsockname()[1]

        # Connect a tag-capable client and join #system before stopping.
        reader, writer = await asyncio.open_connection("127.0.0.1", ircd.config.port)
        client = IRCTestClient(reader, writer)
        await client.send("CAP REQ :message-tags")
        await client.recv_until("CAP")
        await client.send("NICK testserv-alice")
        await client.send("USER alice 0 * :alice")
        await client.recv_all(timeout=0.5)
        await client.send("JOIN #system")
        await client.recv_until("366")
        await asyncio.sleep(0.05)
        await client.recv_all(timeout=0.2)  # flush any queued join-event PRIVMSG

        # Start receiving concurrently before calling stop() so we capture the
        # server.sleep PRIVMSG that must arrive at the top of stop() before any
        # socket teardown. server.wait_closed() will not return until after we
        # close the client connection.
        recv_task = asyncio.create_task(client.recv_until("server.sleep"))

        # Allow the event loop to schedule the recv task before we block in stop().
        await asyncio.sleep(0)

        # Trigger stop() — server.sleep must surface BEFORE sockets close.
        # We close the client immediately after stop() emits the event so that
        # server.wait_closed() can finish.
        stop_task = asyncio.create_task(ircd.stop())

        # Collect the sleep message — recv_until times out after 2s or on
        # ConnectionError so this will not hang even if the event is never sent.
        line = await recv_task

        # Now close the client so server.wait_closed() can proceed.
        try:
            await client.close()
        except Exception:
            pass

        # Wait for stop() to fully complete.
        await stop_task

        assert (
            "event=server.sleep" in line
        ), f"Expected server.sleep PRIVMSG before socket closed; got: {line!r}"
        assert "testserv is shutting down" in line
