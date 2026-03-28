import asyncio
import pytest
import re
from agentirc.clients.claude.irc_transport import IRCTransport
from agentirc.clients.claude.message_buffer import MessageBuffer


@pytest.mark.asyncio
async def test_connect_and_register(server):
    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1", port=server.config.port,
        nick="testserv-bot", user="bot", channels=["#general"], buffer=buf,
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
        host="127.0.0.1", port=server.config.port,
        nick="testserv-bot", user="bot", channels=["#general", "#dev"], buffer=buf,
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
        host="127.0.0.1", port=server.config.port,
        nick="testserv-bot", user="bot", channels=["#general"], buffer=buf,
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
        host="127.0.0.1", port=server.config.port,
        nick="testserv-bot", user="bot", channels=["#general"], buffer=buf,
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
        host="127.0.0.1", port=server.config.port,
        nick="testserv-bot", user="bot", channels=["#general"], buffer=buf,
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
        host="127.0.0.1", port=1,  # nothing listens here
        nick="testserv-bot", user="bot", channels=["#general"], buffer=buf,
    )
    with pytest.raises(ConnectionError, match=re.escape("127.0.0.1:1")):
        await transport.connect()


@pytest.mark.asyncio
async def test_reconnect_retries_after_connection_error(server):
    """The reconnect loop retries after ConnectionError instead of crashing."""
    buf = MessageBuffer()
    # Use a port where nothing listens so the first _do_connect fails
    transport = IRCTransport(
        host="127.0.0.1", port=1,
        nick="testserv-bot", user="bot", channels=["#general"], buffer=buf,
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
