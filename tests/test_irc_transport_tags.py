"""All-backends IRCv3 tag parsing smoke test — Task 17."""

import asyncio

import pytest

from culture.protocol.message import Message


def test_message_parse_extracts_tags():
    """Message.parse() correctly extracts IRCv3 tags — shared by all transports."""
    line = "@event=user.join;event-data=eyJuaWNrIjoib3JpIn0= :system-spark!system@spark PRIVMSG #general :ori joined"
    msg = Message.parse(line)
    assert msg.tags.get("event") == "user.join"
    assert "event-data" in msg.tags
    assert msg.command == "PRIVMSG"
    assert msg.params == ["#general", "ori joined"]


def test_message_parse_no_tags():
    """Lines without tags still parse correctly."""
    line = ":nick!user@host PRIVMSG #channel :hello"
    msg = Message.parse(line)
    assert msg.tags == {}
    assert msg.command == "PRIVMSG"


@pytest.mark.asyncio
async def test_transport_negotiates_message_tags(server, make_client):
    """Transport negotiates message-tags capability during connection.

    After a real IRCTransport connects, the server-side client object
    should have 'message-tags' in its caps set, proving the CAP handshake
    completed successfully.
    """
    from agentirc.protocol import Event, EventType

    from culture.clients.shared.irc_transport import IRCTransport
    from culture.clients.shared.message_buffer import MessageBuffer

    buf = MessageBuffer()
    transport = IRCTransport(
        host="127.0.0.1",
        port=server.config.port,
        nick="testserv-tagtest",
        user="tagtest",
        channels=["#test-tags"],
        buffer=buf,
    )
    await transport.connect()
    try:
        # Give the transport time to finish the welcome sequence and join
        await asyncio.sleep(0.4)
        assert transport.connected, "Transport did not connect"
        assert "testserv-tagtest" in server.clients

        # Emit an event that the server will surface as a tagged PRIVMSG
        ev = Event(
            type=EventType.JOIN,
            channel="#test-tags",
            nick="testserv-visitor",
            data={"nick": "testserv-visitor"},
        )
        await server.emit_event(ev)

        # Give the transport time to receive the event line
        await asyncio.sleep(0.3)

        # The server should have sent tagged lines to this client because it
        # negotiated CAP REQ :message-tags during _do_connect()
        server_client = server.clients.get("testserv-tagtest")
        assert server_client is not None
        assert "message-tags" in server_client.caps, (
            "Transport did not negotiate message-tags CAP — "
            "CAP REQ :message-tags was not sent before NICK/USER"
        )
    finally:
        await transport.disconnect()


@pytest.mark.asyncio
async def test_transport_receives_tagged_events(server, make_client):
    """A raw IRCTestClient that REQs message-tags receives tagged event PRIVMSGs."""
    from agentirc.protocol import Event, EventType

    # Connect a tag-capable client via the raw test helper
    agent = await make_client("testserv-tagrcv", "tagrcv")
    await agent.send("CAP REQ :message-tags")
    await agent.send("CAP END")
    await agent.recv_all(timeout=0.5)
    # All events now route to #system, so join it to observe them.
    await agent.send("JOIN #system")
    await agent.recv_all(timeout=0.5)

    # Emit an event that surfaces as tagged PRIVMSG
    ev = Event(
        type=EventType.JOIN,
        channel="#test-tags",
        nick="testserv-visitor",
        data={"nick": "testserv-visitor"},
    )
    await server.emit_event(ev)

    lines = await agent.recv_all(timeout=0.5)
    tagged = [line for line in lines if line.startswith("@")]
    assert tagged, f"Expected tagged line but got: {lines}"
    assert "event=user.join" in tagged[0]
