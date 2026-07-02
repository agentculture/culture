import pytest


@pytest.mark.asyncio
async def test_who_channel_lists_members(server, make_client):
    """WHO #channel lists all members."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    await client1.send("WHO #general")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "352" in joined  # RPL_WHOREPLY
    assert "testserv-ori" in joined
    assert "testserv-claude" in joined
    assert "315" in joined  # RPL_ENDOFWHO


@pytest.mark.asyncio
async def test_who_shows_op_prefix(server, make_client):
    """WHO shows H@ for operators and H+ for voiced."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    # Grant voice to claude
    await client1.send("MODE #general +v testserv-claude")
    await client1.recv_all(timeout=0.5)
    await client2.recv_all(timeout=0.5)

    await client1.send("WHO #general")
    lines = await client1.recv_all(timeout=1.0)
    who_lines = [l for l in lines if "352" in l]
    ori_line = [l for l in who_lines if "testserv-ori H" in l][0]
    claude_line = [l for l in who_lines if "testserv-claude H" in l][0]
    assert "H@" in ori_line
    assert "H+" in claude_line


@pytest.mark.asyncio
async def test_who_by_nick(server, make_client):
    """WHO <nick> returns info for that nick."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    await client1.send("WHO testserv-claude")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "352" in joined
    assert "testserv-claude" in joined
    assert "#general" in joined
    assert "315" in joined


@pytest.mark.asyncio
async def test_who_nonexistent_channel(server, make_client):
    """WHO on nonexistent channel returns only ENDOFWHO."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("WHO #doesnotexist")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "315" in joined
    assert "352" not in joined


@pytest.mark.asyncio
async def test_who_no_params(server, make_client):
    """WHO with no params returns ENDOFWHO."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("WHO")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "315" in joined


@pytest.mark.asyncio
async def test_whois_returns_user_info(server, make_client):
    """WHOIS returns user info, server, and channels."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)

    await client1.send("WHOIS testserv-claude")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "311" in joined  # RPL_WHOISUSER
    assert "312" in joined  # RPL_WHOISSERVER
    assert "319" in joined  # RPL_WHOISCHANNELS
    assert "318" in joined  # RPL_ENDOFWHOIS
    assert "testserv-claude" in joined
    assert "#general" in joined


@pytest.mark.asyncio
async def test_whois_channels_show_mode_prefixes(server, make_client):
    """WHOIS channels show @/+ prefixes for ops/voiced."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    # ori is op
    await client1.send("WHOIS testserv-ori")
    lines = await client1.recv_all(timeout=1.0)
    chan_line = [l for l in lines if "319" in l][0]
    assert "@#general" in chan_line


@pytest.mark.asyncio
async def test_whois_nonexistent_nick(server, make_client):
    """WHOIS on unknown nick returns 401 + ENDOFWHOIS."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("WHOIS testserv-nobody")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "401" in joined  # ERR_NOSUCHNICK
    assert "318" in joined  # RPL_ENDOFWHOIS


@pytest.mark.asyncio
async def test_whois_no_params(server, make_client):
    """WHOIS with no params returns ERR_NONICKNAMEGIVEN."""
    client = await make_client(nick="testserv-ori", user="ori")
    await client.send("WHOIS")
    lines = await client.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "431" in joined  # ERR_NONICKNAMEGIVEN


@pytest.mark.asyncio
async def test_who_nick_not_in_any_channel(server, make_client):
    """WHO <nick> for a user not in any channel shows * for channel."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await make_client(nick="testserv-claude", user="claude")

    await client1.send("WHO testserv-claude")
    lines = await client1.recv_all(timeout=1.0)
    who_lines = [l for l in lines if "352" in l]
    assert len(who_lines) == 1
    # Channel should be * since claude is not in any channel
    assert "* claude" in who_lines[0]


@pytest.mark.asyncio
async def test_whois_no_channels(server, make_client):
    """WHOIS for user not in any channel omits RPL_WHOISCHANNELS."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await make_client(nick="testserv-claude", user="claude")

    await client1.send("WHOIS testserv-claude")
    lines = await client1.recv_all(timeout=1.0)
    joined = " ".join(lines)
    assert "311" in joined
    assert "319" not in joined  # No RPL_WHOISCHANNELS
    assert "318" in joined
