"""Integration tests for lifecycle event emission via user modes +A and +C.

Design decision (Option B): Separate user modes +A (agent) and +C (console)
trigger lifecycle events, rather than ICON values. ICON retains its display-only
role unchanged.

Event emission contract:
- +A transition OFF→ON: emit agent.connect
- -A transition ON→OFF, or client with +A disconnects: emit agent.disconnect
- +C transition OFF→ON: emit console.open
- -C transition ON→OFF, or client with +C disconnects: emit console.close
- +H and +B are plain mode tags; they do NOT emit events.
"""

import asyncio
import base64
import json

import pytest

from tests.conftest import IRCTestClient


def _decode_event_payload(lines: str) -> dict:
    """Extract and decode the IRCv3 `event-data=<b64-json>` tag.

    ``lines`` is multi-line text as returned by ``recv_until``; scan each
    line for the ``@event=...`` tag blob and decode the matching
    ``event-data=`` value. Only tagged PRIVMSG-style lines start with ``@``.
    """
    for raw in lines.split("\r\n"):
        if not raw.startswith("@"):
            continue
        space_idx = raw.find(" ")
        if space_idx == -1:
            continue
        tag_blob = raw[1:space_idx]
        for piece in tag_blob.split(";"):
            if piece.startswith("event-data="):
                return json.loads(base64.b64decode(piece.split("=", 1)[1]))
    raise AssertionError(f"No event-data tag found in lines: {lines!r}")


async def _setup_observer(make_client) -> IRCTestClient:
    """Connect alice to #system with message-tags CAP, draining join noise."""
    alice = await make_client("testserv-alice", "alice")
    await alice.send("CAP REQ :message-tags")
    await alice.recv_until("ACK")
    await alice.send("JOIN #system")
    await alice.recv_until("366")  # end of NAMES
    await asyncio.sleep(0.05)
    await alice.recv_all(timeout=0.2)  # flush join-event PRIVMSG
    return alice


@pytest.mark.asyncio
async def test_agent_connect_on_mode_a(server, make_client):
    """MODE +A causes agent.connect to be delivered to #system subscribers."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +A")

    line = await alice.recv_until("event=agent.connect")
    assert "event=agent.connect" in line, f"Expected agent.connect, got: {line!r}"
    assert "testserv-bob connected" in line


@pytest.mark.asyncio
async def test_agent_disconnect_on_mode_minus_a(server, make_client):
    """MODE -A after +A causes agent.disconnect to be delivered."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +A")
    await alice.recv_until("event=agent.connect")
    await alice.recv_all(timeout=0.1)  # drain any trailing lines

    await bob.send("MODE testserv-bob -A")
    line = await alice.recv_until("event=agent.disconnect")
    assert "event=agent.disconnect" in line, f"Expected agent.disconnect, got: {line!r}"
    assert "testserv-bob disconnected" in line


@pytest.mark.asyncio
async def test_agent_disconnect_on_close(server, make_client):
    """A client with +A that closes the TCP connection triggers agent.disconnect."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +A")
    await alice.recv_until("event=agent.connect")
    await alice.recv_all(timeout=0.1)

    await bob.close()

    line = await alice.recv_until("event=agent.disconnect")
    assert "event=agent.disconnect" in line, f"Expected agent.disconnect on close, got: {line!r}"
    assert "testserv-bob disconnected" in line


@pytest.mark.asyncio
async def test_console_open_on_mode_c(server, make_client):
    """MODE +C causes console.open to be delivered to #system subscribers."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +C")

    line = await alice.recv_until("event=console.open")
    assert "event=console.open" in line, f"Expected console.open, got: {line!r}"
    assert "testserv-bob opened a console" in line


@pytest.mark.asyncio
async def test_console_close_on_mode_minus_c(server, make_client):
    """MODE -C after +C causes console.close to be delivered."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +C")
    await alice.recv_until("event=console.open")
    await alice.recv_all(timeout=0.1)

    await bob.send("MODE testserv-bob -C")
    line = await alice.recv_until("event=console.close")
    assert "event=console.close" in line, f"Expected console.close, got: {line!r}"
    assert "testserv-bob closed their console" in line


@pytest.mark.asyncio
async def test_console_close_on_disconnect(server, make_client):
    """A client with +C that closes the TCP connection triggers console.close."""
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +C")
    await alice.recv_until("event=console.open")
    await alice.recv_all(timeout=0.1)

    await bob.close()

    line = await alice.recv_until("event=console.close")
    assert "event=console.close" in line, f"Expected console.close on close, got: {line!r}"
    assert "testserv-bob closed their console" in line


@pytest.mark.asyncio
async def test_h_and_b_modes_do_not_emit_events(server, make_client):
    """Modes +H and +B are plain identity tags that do NOT emit any lifecycle events.

    The test sends both mode changes and then waits briefly. No event= PRIVMSG
    should arrive. recv_until times out and returns an empty-or-non-matching string,
    which we verify contains no event= tag.
    """
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +H")
    await bob.send("MODE testserv-bob +B")

    # Give the server a moment to process both MODEs
    await asyncio.sleep(0.15)

    # recv_until returns accumulated lines or "" if not found within timeout
    # We want a short wait here — wrap in a short timeout
    collected = []
    try:
        async with asyncio.timeout(0.5):
            while True:
                line = await alice.recv()
                collected.append(line)
    except (asyncio.TimeoutError, TimeoutError, ConnectionError):
        pass

    result = " ".join(collected)
    assert (
        "event=" not in result
    ), f"Unexpected event delivery for +H/+B modes. Received: {result!r}"


@pytest.mark.asyncio
async def test_agent_mode_idempotent(server, make_client):
    """Sending MODE +A twice only fires agent.connect once.

    Invariant: the event is triggered on the OFF→ON edge only. A second +A
    when +A is already set is a no-op — the mode bit is already set, so there
    is no state transition and no event is emitted.

    This is the correct IRC semantics for idempotent mode changes and prevents
    duplicate connect notifications on reconnect races.
    """
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    # First +A: should emit
    await bob.send("MODE testserv-bob +A")
    line = await alice.recv_until("event=agent.connect")
    assert "event=agent.connect" in line

    # Clear any trailing lines
    await alice.recv_all(timeout=0.1)

    # Second +A: already set, no edge, no event
    await bob.send("MODE testserv-bob +A")
    await asyncio.sleep(0.15)

    collected = []
    try:
        async with asyncio.timeout(0.5):
            while True:
                line2 = await alice.recv()
                collected.append(line2)
    except (asyncio.TimeoutError, TimeoutError, ConnectionError):
        pass

    result = " ".join(collected)
    assert (
        "event=agent.connect" not in result
    ), f"agent.connect fired on second +A (should be idempotent). Got: {result!r}"


@pytest.mark.asyncio
async def test_console_mode_hc_combined_emits_console_open_only(server, make_client):
    """Console clients send `MODE <nick> +HC` in one message (human + console).

    The combined mode string must:
    - Set both +H and +C on the client.
    - Emit `console.open` exactly once (the +C edge).
    - NOT emit any event for +H (no such event type).

    Mirrors the exact wire format produced by `culture.console.client`.
    """
    alice = await _setup_observer(make_client)
    bob = await make_client("testserv-bob", "bob")

    await bob.send("MODE testserv-bob +HC")

    line = await alice.recv_until("event=console.open")
    assert "event=console.open" in line, f"Expected console.open from +HC, got: {line!r}"
    assert "testserv-bob opened a console" in line
    # No agent.connect leaked into the same batch.
    assert "event=agent.connect" not in line
    # Give the server a moment; no further events should fire for +H.
    await asyncio.sleep(0.1)
    tail = await alice.recv_all(timeout=0.2)
    tail_joined = " ".join(tail)
    assert "event=" not in tail_joined, f"Unexpected trailing event after +HC: {tail_joined!r}"


@pytest.mark.asyncio
async def test_mode_before_registration_does_not_emit_events(server, make_client):
    """Pre-registration MODE +A must not inject agent.connect.

    A socket that has sent NICK but not USER is not yet registered. The server
    must not emit lifecycle events on its behalf — otherwise any client that
    opens a TCP connection and sends NICK+MODE can forge agent.connect /
    console.open into #system.
    """
    alice = await _setup_observer(make_client)

    # Half-open client: NICK only, no USER -> not registered.
    import asyncio as _asyncio

    reader, writer = await _asyncio.open_connection("127.0.0.1", server.config.port)
    bob_raw = IRCTestClient(reader, writer)
    await bob_raw.send("NICK testserv-bob")
    await bob_raw.send("MODE testserv-bob +A")
    # Give the server time to process (and, if buggy, emit).
    await _asyncio.sleep(0.2)
    await bob_raw.recv_all(timeout=0.2)

    # Alice should see nothing.
    tail = await alice.recv_all(timeout=0.3)
    tail_joined = " ".join(tail)
    assert (
        "event=agent.connect" not in tail_joined
    ), f"agent.connect fired pre-registration (security bug). Got: {tail_joined!r}"

    await bob_raw.close()


@pytest.mark.asyncio
async def test_room_create_emitted_on_roomcreate(server, make_client):
    """ROOMCREATE emits a distinct `room.create` event, separate from room.meta.

    room.create signals the room's birth (a lifecycle moment); the follow-up
    room.meta carries the initial metadata snapshot. Downstream consumers
    (bots, history, federation peers) see both and can pick whichever semantic
    fits their logic.
    """
    creator = await make_client("testserv-creator", "creator")
    await creator.send("CAP REQ :message-tags")
    await creator.recv_until("ACK")

    # Create a managed room. The channel-scoped room.create surfaces on the
    # new channel itself — creator is auto-joined, so they receive it.
    await creator.send("ROOMCREATE #research :purpose=AI research;tags=ai;persistent=true")

    line = await creator.recv_until("event=room.create")
    assert "event=room.create" in line, f"Expected room.create tag, got: {line!r}"
    assert "testserv-creator created room #research" in line

    # Lock the structured payload fields downstream consumers rely on.
    payload = _decode_event_payload(line)
    assert payload["nick"] == "testserv-creator"
    assert payload["purpose"] == "AI research"
    # room_id is generated server-side — shape check only (starts with "R").
    assert payload["room_id"].startswith(
        "R"
    ), f"Expected room_id to start with R, got: {payload['room_id']!r}"
    # The server enriches the payload with the channel when the event is
    # channel-scoped (see IRCd._build_event_payload).
    assert payload["channel"] == "#research"


@pytest.mark.asyncio
async def test_room_create_precedes_room_meta(server, make_client):
    """room.create must precede room.meta on the wire so downstream consumers
    can distinguish creation from subsequent metadata updates."""
    creator = await make_client("testserv-creator", "creator")
    await creator.send("CAP REQ :message-tags")
    await creator.recv_until("ACK")

    await creator.send("ROOMCREATE #ordering :purpose=Test;persistent=true")
    collected = await creator.recv_until("event=room.meta")
    create_idx = collected.find("event=room.create")
    meta_idx = collected.find("event=room.meta")
    assert create_idx != -1, f"room.create not found in: {collected!r}"
    assert meta_idx != -1, f"room.meta not found in: {collected!r}"
    assert create_idx < meta_idx, (
        f"room.create must precede room.meta (create@{create_idx}, meta@{meta_idx}) "
        f"in: {collected!r}"
    )
