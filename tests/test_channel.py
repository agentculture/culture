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
