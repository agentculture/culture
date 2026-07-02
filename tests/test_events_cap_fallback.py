"""CAP negotiation for message-tags + plain-body fallback for non-tag clients."""

import pytest


@pytest.mark.asyncio
async def test_cap_ls_lists_message_tags(server, make_client):
    c = await make_client()
    await c.send("CAP LS")
    line = await c.recv()
    assert "message-tags" in line


@pytest.mark.asyncio
async def test_cap_req_ack(server, make_client):
    c = await make_client()
    await c.send("CAP LS")
    await c.recv()
    await c.send("CAP REQ :message-tags")
    line = await c.recv()
    assert "ACK" in line
    assert "message-tags" in line


@pytest.mark.asyncio
async def test_non_tag_client_receives_plain_privmsg(server, make_client):
    """A client that never REQs message-tags should not receive @tag blocks."""
    from culture_core.protocol.message import Message

    c = await make_client(nick="testserv-alice", user="alice")
    # Do not send CAP REQ. Server will strip tags.

    # Join a channel first
    await c.send("JOIN #testchan")
    # Drain all JOIN responses: JOIN confirmation, NAMES list, END OF NAMES,
    # and the user.join system PRIVMSG emitted after NAMES (new in Task 7).
    await c.recv_until("366")  # end of NAMES
    # Drain the user.join system PRIVMSG that fires after join numerics.
    await c.recv_all(timeout=0.2)

    # Directly exercise send_tagged on the server-side client object
    server_client = server.clients.get("testserv-alice")
    assert server_client is not None, "Client not registered on server"

    # Construct a tagged channel message and send it via send_tagged
    tagged_msg = Message(
        tags={"event": "test.value"},
        prefix="testserv-bob!bob@host",
        command="PRIVMSG",
        params=["#testchan", "hello"],
    )
    await server_client.send_tagged(tagged_msg)

    # The client should receive the message WITHOUT the @tag block
    line = await c.recv()
    assert not line.startswith("@"), f"unexpected tagged line: {line}"
    assert "PRIVMSG" in line
    assert "#testchan" in line
    assert "hello" in line
