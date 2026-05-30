import pytest


@pytest.mark.asyncio
async def test_join_channel(server, make_client):
    """Client can join a channel and receives NAMES list."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "#general" in joined
    assert "353" in joined  # RPL_NAMREPLY
    assert "366" in joined  # RPL_ENDOFNAMES
    assert "testserv-ori" in joined


@pytest.mark.asyncio
async def test_join_notifies_others(server, make_client):
    """Existing channel members see the JOIN."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")

    # client1 should see client2's join
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "JOIN" in joined
    assert "testserv-claude" in joined


@pytest.mark.asyncio
async def test_part_channel(server, make_client):
    """Client can leave a channel."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    await client.recv_all(timeout=0.5)

    await client.send("PART #general :bye")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "PART" in joined
    assert "#general" in joined


@pytest.mark.asyncio
async def test_part_not_on_channel(server, make_client):
    """PART on a channel you're not in returns error."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("PART #general")
    response = await client.recv()
    assert "442" in response  # ERR_NOTONCHANNEL


@pytest.mark.asyncio
async def test_topic_set_and_get(server, make_client):
    """Client can set and query channel topic."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    await client.recv_all(timeout=0.5)

    await client.send("TOPIC #general :Building culture")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "TOPIC" in joined
    assert "Building culture" in joined

    await client.send("TOPIC #general")
    response = await client.recv()
    assert "332" in response  # RPL_TOPIC
    assert "Building culture" in response


@pytest.mark.asyncio
async def test_topic_no_topic_set(server, make_client):
    """Querying topic on channel without topic returns RPL_NOTOPIC."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    await client.recv_all(timeout=0.5)

    await client.send("TOPIC #general")
    response = await client.recv()
    assert "331" in response  # RPL_NOTOPIC


@pytest.mark.asyncio
async def test_names_list(server, make_client):
    """NAMES returns all members of a channel."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    await client1.send("NAMES #general")
    lines = await client1.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    assert "testserv-ori" in names_line
    assert "testserv-claude" in names_line


@pytest.mark.asyncio
async def test_join_channel_with_topic(server, make_client):
    """Joining a channel with a topic shows the topic."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client1.send("TOPIC #general :Welcome to culture")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    lines = await client2.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "332" in joined
    assert "Welcome to culture" in joined


@pytest.mark.asyncio
async def test_channel_name_must_start_with_hash(server, make_client):
    """JOIN without # prefix is silently ignored."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN general")
    lines = await client.recv_all(timeout=0.5)
    # Should get nothing back (no join, no error)
    join_lines = [l for l in lines if "JOIN" in l]
    assert len(join_lines) == 0


@pytest.mark.asyncio
async def test_peek_join_suppresses_join_event(server, make_client):
    """Peek clients (`<server>-_peek<rand>`) must NOT fire user.join events.

    Every `culture channel read` and dashboard channel-preview poll opens
    a short-lived peek connection that joins, reads HISTORY, and parts.
    The JOIN is required to clear the v8.18.3 HISTORY membership gate,
    but the user.join EVENT — which lands as a PRIVMSG in every channel
    member's buffer — is observer noise that poisons agent contexts.
    """
    # Pre-existing member that would see the JOIN event PRIVMSG.
    watcher = await make_client(nick="testserv-alice", user="alice")
    await watcher.send("JOIN #general")
    await watcher.recv_all(timeout=0.5)

    # Peek client joins #general.
    peek = await make_client(nick="testserv-_peek1234", user="peek")
    await peek.send("JOIN #general")
    await peek.recv_all(timeout=0.5)

    # Give the server a beat to emit (or not emit) the event.
    lines = await watcher.recv_all(timeout=0.5)
    join_event_lines = [
        l for l in lines if "PRIVMSG" in l and "_peek1234" in l
    ]
    assert join_event_lines == [], (
        f"peek JOIN must not emit user.join PRIVMSG, got: {join_event_lines}"
    )


@pytest.mark.asyncio
async def test_real_agent_join_still_broadcasts(server, make_client):
    """Symmetry: non-peek joins must STILL broadcast JOIN + fire welcome.

    Suppressing only the EVENT emission must not break the protocol-level
    JOIN delivery — channel members must still see the IRC ``JOIN``
    message, and the welcome bot (which fires from the user.join event
    handler) must still greet real agents.
    """
    watcher = await make_client(nick="testserv-alice", user="alice")
    await watcher.send("JOIN #general")
    await watcher.recv_all(timeout=0.5)

    real = await make_client(nick="testserv-bob", user="bob")
    await real.send("JOIN #general")
    await real.recv_all(timeout=0.5)

    lines = await watcher.recv_all(timeout=0.5)
    raw_join = [l for l in lines if "JOIN" in l and "testserv-bob" in l]
    assert raw_join, f"non-peek JOIN must broadcast raw JOIN to members, got: {lines}"
    welcome = [l for l in lines if "Welcome testserv-bob" in l]
    assert welcome, f"welcome bot must fire on real agent join, got: {lines}"
