import pytest


@pytest.mark.asyncio
async def test_first_joiner_gets_op(server, make_client):
    """First user to join an empty channel gets @prefix in NAMES."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    lines = await client.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    assert "@testserv-ori" in names_line


@pytest.mark.asyncio
async def test_second_joiner_no_prefix(server, make_client):
    """Second user to join does not get op."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    lines = await client2.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    # ori has @, claude has no prefix
    assert "@testserv-ori" in names_line
    assert "@testserv-claude" not in names_line
    assert "testserv-claude" in names_line


@pytest.mark.asyncio
async def test_op_can_grant_op(server, make_client):
    """Channel operator can grant +o to another user."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)  # drain join notification

    await client1.send("MODE #general +o testserv-claude")
    lines1 = await client1.recv_all(timeout=1.0)
    lines2 = await client2.recv_all(timeout=1.0)

    # Both users should see the MODE change
    mode_line1 = " ".join(lines1)
    mode_line2 = " ".join(lines2)
    assert "MODE" in mode_line1
    assert "+o" in mode_line1
    assert "MODE" in mode_line2
    assert "+o" in mode_line2

    # Verify NAMES now shows @ for both
    await client1.send("NAMES #general")
    lines = await client1.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    assert "@testserv-ori" in names_line
    assert "@testserv-claude" in names_line


@pytest.mark.asyncio
async def test_op_can_revoke_op(server, make_client):
    """Channel operator can revoke -o from another user."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    # Grant then revoke
    await client1.send("MODE #general +o testserv-claude")
    await client1.recv_all(timeout=0.5)
    await client2.recv_all(timeout=0.5)

    await client1.send("MODE #general -o testserv-claude")
    await client1.recv_all(timeout=0.5)

    await client1.send("NAMES #general")
    lines = await client1.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    assert "@testserv-ori" in names_line
    assert "@testserv-claude" not in names_line


@pytest.mark.asyncio
async def test_op_can_grant_voice(server, make_client):
    """Channel operator can grant +v."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("MODE #general +v testserv-claude")
    await client1.recv_all(timeout=0.5)

    await client1.send("NAMES #general")
    lines = await client1.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    assert "+testserv-claude" in names_line


@pytest.mark.asyncio
async def test_non_op_cannot_set_mode(server, make_client):
    """Non-operator gets 482 error when trying to set modes."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    await client2.send("MODE #general +o testserv-claude")
    lines = await client2.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "482" in joined


@pytest.mark.asyncio
async def test_mode_broadcast_to_all(server, make_client):
    """MODE changes are broadcast to all channel members."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    client3 = await make_client(nick="testserv-bob", user="bob")
    await client3.send("JOIN #general")
    await client3.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)
    await client2.recv_all(timeout=0.5)

    await client1.send("MODE #general +v testserv-claude")
    lines3 = await client3.recv_all(timeout=1.0)
    joined = " ".join(lines3)
    assert "MODE" in joined
    assert "+v" in joined


@pytest.mark.asyncio
async def test_mode_query_returns_324(server, make_client):
    """MODE #channel query returns RPL_CHANNELMODEIS (324)."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("JOIN #general")
    await client.recv_all(timeout=0.5)

    await client.send("MODE #general")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "324" in joined


@pytest.mark.asyncio
async def test_mode_nonexistent_channel(server, make_client):
    """MODE on nonexistent channel returns 403."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE #doesnotexist")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "403" in joined


@pytest.mark.asyncio
async def test_mode_target_not_in_channel(server, make_client):
    """MODE on a nick not in the channel returns 441."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    await make_client(nick="testserv-claude", user="claude")

    await client1.send("MODE #general +o testserv-claude")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "441" in joined


@pytest.mark.asyncio
async def test_user_mode_query(server, make_client):
    """MODE <nick> returns RPL_UMODEIS (221)."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("MODE testserv-ori")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "221" in joined


@pytest.mark.asyncio
async def test_part_removes_from_modes(server, make_client):
    """PARTing a channel removes mode flags."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("MODE #general +o testserv-claude")
    await client1.recv_all(timeout=0.5)
    await client2.recv_all(timeout=0.5)

    # Part and rejoin
    await client2.send("PART #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    await client1.send("NAMES #general")
    lines = await client1.recv_all(timeout=1.0)
    names_line = [l for l in lines if "353" in l][0]
    # claude should not have @ after rejoin
    assert "@testserv-claude" not in names_line
