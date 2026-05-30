import asyncio
import re

import pytest

from culture.clients.claude.irc_transport import IRCTransport
from culture.clients.claude.message_buffer import MessageBuffer


@pytest.mark.asyncio
async def test_connect_and_register(server):
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    try:
        await asyncio.sleep(0.3)
        assert transport.connected
        assert "testserv-bot" in server.clients
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_joins_channels(server):
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general", "#dev"],
        buffer=buf,
    )
    await transport.connect()
    try:
        await asyncio.sleep(0.3)
        assert "#general" in server.channels
        assert "#dev" in server.channels
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_buffers_incoming_messages(server, make_client):
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #general :hello bot")
    await asyncio.sleep(0.3)
    msgs = buf.read("#general", limit=50)
    assert any(m.text == "hello bot" and m.nick == "testserv-ori" for m in msgs)
    await transport.disconnect()


@pytest.mark.asyncio
async def test_send_privmsg(server, make_client):
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    await transport.send_privmsg("#general", "hello human")
    response = await human.recv(timeout=2.0)
    assert "hello human" in response
    await transport.disconnect()


@pytest.mark.asyncio
async def test_send_join_part(server):
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    await transport.join_channel("#new")
    await asyncio.sleep(0.2)
    assert "#new" in server.channels
    await transport.part_channel("#new")
    await asyncio.sleep(0.2)
    assert "#new" not in server.channels
    await transport.disconnect()


@pytest.mark.asyncio
async def test_connect_raises_on_refused():
    """Connecting to an unreachable server raises ConnectionError with a clear message."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=1,  # nothing listens here
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    with pytest.raises(ConnectionError, match=re.escape("127.0.0.1:1")):
        await transport.connect()


@pytest.mark.asyncio
async def test_reconnect_retries_after_connection_error(server):
    """The reconnect loop retries after ConnectionError instead of crashing."""
    buf = MessageBuffer()
    # Use a port where nothing listens so the first _do_connect fails
    transport = IRCTransport(
        host="127.0.0.1",
        port=1,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    transport._should_run = True
    transport._reconnecting = False

    # Patch _do_connect to fail once with ConnectionError, then succeed
    call_count = 0
    original_do_connect = transport._do_connect

    async def patched_do_connect():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("simulated failure")
        # On retry, connect to the real server
        transport.host = "127.0.0.1"
        transport.port = server.config.port
        await original_do_connect()

    transport._do_connect = patched_do_connect

    # _reconnect should retry and eventually succeed
    await asyncio.wait_for(transport._reconnect(), timeout=5.0)
    assert call_count >= 2
    assert transport.connected or not transport._reconnecting
    await transport.disconnect()


@pytest.mark.asyncio
async def test_multiline_privmsg_splits_into_separate_messages(server, make_client):
    """Multi-line text should be split into separate PRIVMSG lines."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    await transport.send_privmsg("#general", "line one\nline two\nline three")
    lines = await human.recv_all(timeout=2.0)
    privmsgs = [l for l in lines if "PRIVMSG" in l]
    assert len(privmsgs) == 3
    assert "line one" in privmsgs[0]
    assert "line two" in privmsgs[1]
    assert "line three" in privmsgs[2]
    await transport.disconnect()


@pytest.mark.asyncio
async def test_multiline_privmsg_skips_empty_lines(server, make_client):
    """Empty lines in multi-line text should be skipped."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    await transport.send_privmsg("#general", "first\n\n\nsecond")
    lines = await human.recv_all(timeout=2.0)
    privmsgs = [l for l in lines if "PRIVMSG" in l]
    assert len(privmsgs) == 2
    assert "first" in privmsgs[0]
    assert "second" in privmsgs[1]
    await transport.disconnect()


@pytest.mark.asyncio
async def test_join_rejects_channel_without_hash(server):
    """Joining a channel without # prefix should fail."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    try:
        await asyncio.sleep(0.3)
        await transport.join_channel("nohash")
        assert "nohash" not in transport.channels
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_part_rejects_channel_without_hash(server):
    """Parting a channel without # prefix should be a no-op."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    try:
        await asyncio.sleep(0.3)
        await transport.part_channel("nohash")
        # Should not crash or modify state
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_own_messages_in_buffer(server, make_client):
    """Agent's own sent messages should appear in channel history."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)

    await transport.send_privmsg("#general", "my own message")
    await asyncio.sleep(0.1)

    msgs = buf.read("#general", limit=50)
    assert any(m.text == "my own message" and m.nick == "testserv-bot" for m in msgs)
    await transport.disconnect()


@pytest.mark.asyncio
async def test_own_dm_messages_in_buffer(server, make_client):
    """Agent's own sent DMs should be buffered under DM:{target}."""
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.recv_all(timeout=0.3)

    await transport.send_privmsg("testserv-ori", "hello via DM")
    await asyncio.sleep(0.1)

    msgs = buf.read("DM:testserv-ori", limit=50)
    assert any(m.text == "hello via DM" and m.nick == "testserv-bot" for m in msgs)
    await transport.disconnect()


@pytest.mark.asyncio
async def test_join_channel_backfills_history(server, make_client):
    """When a transport joins a channel, pre-existing messages are backfilled
    into the buffer via HISTORY RECENT."""
    # Phase 1: another user posts messages BEFORE the transport joins
    human = await make_client(nick="testserv-ori", user="ori")
    await human.recv_all(timeout=0.3)
    await human.send(f"JOIN #backfill-test")
    await human.recv_all(timeout=0.3)
    await human.send(f"PRIVMSG #backfill-test :message before join 1")
    await human.send(f"PRIVMSG #backfill-test :message before join 2")
    await asyncio.sleep(0.3)

    # Phase 2: transport joins — should issue HISTORY RECENT and backfill
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    await transport.join_channel("#backfill-test")
    await asyncio.sleep(0.5)  # allow HISTORY responses to arrive

    msgs = buf.read("#backfill-test", limit=50)
    texts = [m.text for m in msgs]
    assert "message before join 1" in texts
    assert "message before join 2" in texts
    await transport.disconnect()


@pytest.mark.asyncio
async def test_history_backfill_filters_system_nicks(server, make_client):
    """When history contains system-* entries (from server events like joins),
    they should be filtered out of the backfilled buffer."""
    # Phase 1: create channel activity that generates system-* history entries.
    # A user joining + posting creates both a system-<server> lifecycle event
    # and a regular user message in the channel history.
    human = await make_client(nick="testserv-ori", user="ori")
    await human.recv_all(timeout=0.3)
    await human.send("JOIN #sysfilt-test")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #sysfilt-test :human message")
    await asyncio.sleep(0.3)

    # Phase 2: transport joins — history backfill should exclude system-* entries
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)
    await transport.join_channel("#sysfilt-test")
    await asyncio.sleep(0.5)

    msgs = buf.read("#sysfilt-test", limit=50)
    # The human message should appear
    assert any(m.text == "human message" and m.nick == "testserv-ori" for m in msgs)
    # No system-* nick messages should appear
    assert not any(m.nick.startswith("system-") for m in msgs)
    await transport.disconnect()


@pytest.mark.asyncio
async def test_history_backfill_filters_own_messages(server, make_client):
    """When history contains the agent's own old messages, they should be
    filtered out of the backfilled buffer to prevent re-processing."""
    # Phase 1: bot posts to channel, then leaves
    buf1 = MessageBuffer()
    bot1 = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#selfhist-test"],
        buffer=buf1,
    )
    await bot1.connect()
    await asyncio.sleep(0.3)
    await bot1.send_privmsg("#selfhist-test", "my old message")
    await asyncio.sleep(0.2)

    # A human also posts
    human = await make_client(nick="testserv-ori", user="ori")
    await human.recv_all(timeout=0.3)
    await human.send("JOIN #selfhist-test")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #selfhist-test :human says hi")
    await asyncio.sleep(0.2)

    # Bot leaves
    await bot1.part_channel("#selfhist-test")
    await asyncio.sleep(0.2)
    await bot1.disconnect()

    # Phase 2: bot re-joins — history backfill should exclude its own messages
    buf2 = MessageBuffer()
    bot2 = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf2,
    )
    await bot2.connect()
    await asyncio.sleep(0.3)
    await bot2.join_channel("#selfhist-test")
    await asyncio.sleep(0.5)

    msgs = buf2.read("#selfhist-test", limit=50)
    # Human message should appear
    assert any(m.text == "human says hi" and m.nick == "testserv-ori" for m in msgs)
    # Own old message should NOT appear
    assert not any(m.nick == "testserv-bot" for m in msgs)
    await bot2.disconnect()


@pytest.mark.asyncio
async def test_duplicate_join_does_not_duplicate_history(server, make_client):
    """Joining a channel the transport is already in should be a no-op,
    preventing duplicate HISTORY backfill entries."""
    # A human posts a message before the bot joins
    human = await make_client(nick="testserv-ori", user="ori")
    await human.recv_all(timeout=0.3)
    await human.send("JOIN #dupjoin-test")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #dupjoin-test :before join")
    await asyncio.sleep(0.3)

    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-bot",
        user="bot",
        channels=["#general"],
        buffer=buf,
    )
    await transport.connect()
    await asyncio.sleep(0.3)

    # First join — should backfill history
    await transport.join_channel("#dupjoin-test")
    await asyncio.sleep(0.5)
    raw_buf = buf._buffers.get("#dupjoin-test", [])
    count_first = len([m for m in raw_buf if m.text == "before join"])
    assert count_first == 1

    # Second join — should be a no-op (early return)
    await transport.join_channel("#dupjoin-test")
    await asyncio.sleep(0.5)
    raw_buf2 = buf._buffers.get("#dupjoin-test", [])
    count_second = len([m for m in raw_buf2 if m.text == "before join"])
    assert count_second == count_first  # no duplicates

    await transport.disconnect()
