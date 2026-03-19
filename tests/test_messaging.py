import pytest


@pytest.mark.asyncio
async def test_privmsg_to_channel(server, make_client):
    """PRIVMSG to a channel is relayed to other members."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    # Drain client1's notification of client2 joining
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :Hello agents!")
    response = await client2.recv()
    assert "PRIVMSG" in response
    assert "#general" in response
    assert "Hello agents!" in response
    assert "testserv-ori" in response


@pytest.mark.asyncio
async def test_privmsg_not_echoed_to_sender(server, make_client):
    """Sender does not receive their own PRIVMSG."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :talking to myself")
    lines = await client1.recv_all(timeout=0.5)
    privmsg_lines = [l for l in lines if "PRIVMSG" in l]
    assert len(privmsg_lines) == 0


@pytest.mark.asyncio
async def test_privmsg_dm(server, make_client):
    """PRIVMSG to a nick sends a DM."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")

    await client1.send("PRIVMSG testserv-claude :hey, need your help")
    response = await client2.recv()
    assert "PRIVMSG" in response
    assert "testserv-claude" in response
    assert "hey, need your help" in response


@pytest.mark.asyncio
async def test_privmsg_to_nonexistent_nick(server, make_client):
    """PRIVMSG to unknown nick returns ERR_NOSUCHNICK."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("PRIVMSG testserv-nobody :hello?")
    response = await client.recv()
    assert "401" in response  # ERR_NOSUCHNICK


@pytest.mark.asyncio
async def test_privmsg_to_nonexistent_channel(server, make_client):
    """PRIVMSG to unknown channel returns ERR_NOSUCHCHANNEL."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("PRIVMSG #doesnotexist :hello?")
    response = await client.recv()
    assert "403" in response  # ERR_NOSUCHCHANNEL


@pytest.mark.asyncio
async def test_notice_to_channel(server, make_client):
    """NOTICE to a channel is relayed but generates no error replies."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("NOTICE #general :FYI check the benchmark results")
    response = await client2.recv()
    assert "NOTICE" in response
    assert "FYI check the benchmark results" in response


@pytest.mark.asyncio
async def test_notice_to_nonexistent_channel_no_error(server, make_client):
    """NOTICE to unknown channel produces no error (per RFC)."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("NOTICE #doesnotexist :hello")
    lines = await client.recv_all(timeout=0.5)
    assert len(lines) == 0


@pytest.mark.asyncio
async def test_notice_dm(server, make_client):
    """NOTICE to a nick sends a DM."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")

    await client1.send("NOTICE testserv-claude :ping")
    response = await client2.recv()
    assert "NOTICE" in response
    assert "ping" in response
