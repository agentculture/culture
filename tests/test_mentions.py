import pytest


@pytest.mark.asyncio
async def test_mention_sends_notice(server, make_client):
    """@mention in channel sends NOTICE to mentioned user."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :@testserv-claude hello")
    lines = await client2.recv_all(timeout=1.0)
    joined = " ".join(lines)
    # Should get PRIVMSG relay AND server NOTICE
    assert "PRIVMSG" in joined
    assert "NOTICE" in joined
    assert "mentioned you" in joined


@pytest.mark.asyncio
async def test_self_mention_ignored(server, make_client):
    """Self-mention does not trigger a notification."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :@testserv-ori talking to myself")
    lines1 = await client1.recv_all(timeout=1.0)
    # ori should NOT get a mention NOTICE (self-mention)
    joined = " ".join(lines1)
    assert "mentioned you" not in joined


@pytest.mark.asyncio
async def test_unknown_nick_ignored(server, make_client):
    """Mentioning a nick that doesn't exist is silently ignored."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :@testserv-nobody hello")
    lines = await client1.recv_all(timeout=0.5)
    # No error, no crash
    joined = " ".join(lines)
    assert "mentioned you" not in joined


@pytest.mark.asyncio
async def test_mentioned_user_not_in_channel_ignored(server, make_client):
    """Mentioning a user not in the channel doesn't send NOTICE."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    # claude does NOT join #general

    await client1.send("PRIVMSG #general :@testserv-claude hello")
    lines = await client2.recv_all(timeout=0.5)
    joined = " ".join(lines)
    assert "mentioned you" not in joined


@pytest.mark.asyncio
async def test_multiple_mentions(server, make_client):
    """Multiple @mentions in one message notify each user once."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    client3 = await make_client(nick="testserv-bob", user="bob")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client3.send("JOIN #general")
    await client3.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :hey @testserv-claude and @testserv-bob check this")
    lines2 = await client2.recv_all(timeout=1.0)
    lines3 = await client3.recv_all(timeout=1.0)
    assert any("mentioned you" in l for l in lines2)
    assert any("mentioned you" in l for l in lines3)


@pytest.mark.asyncio
async def test_trailing_punctuation_stripped(server, make_client):
    """Trailing punctuation on @nick is stripped for lookup."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :hello @testserv-claude, how are you?")
    lines = await client2.recv_all(timeout=1.0)
    assert any("mentioned you" in l for l in lines)


@pytest.mark.asyncio
async def test_mention_in_dm(server, make_client):
    """Mention in a DM sends NOTICE to mentioned user."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    await make_client(nick="testserv-claude", user="claude")
    client3 = await make_client(nick="testserv-bob", user="bob")

    await client1.send("PRIVMSG testserv-claude :tell @testserv-bob hi")
    # bob should get the mention notice
    lines3 = await client3.recv_all(timeout=1.0)
    assert any("mentioned you" in l for l in lines3)


@pytest.mark.asyncio
async def test_privmsg_not_altered(server, make_client):
    """PRIVMSG is relayed unchanged — mention only adds a NOTICE."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :@testserv-claude hello")
    lines = await client2.recv_all(timeout=1.0)
    privmsg_lines = [l for l in lines if "PRIVMSG" in l]
    notice_lines = [l for l in lines if "NOTICE" in l]
    assert len(privmsg_lines) == 1
    assert "@testserv-claude hello" in privmsg_lines[0]
    assert len(notice_lines) == 1


@pytest.mark.asyncio
async def test_duplicate_mention_only_notifies_once(server, make_client):
    """Same nick mentioned twice only sends one NOTICE."""
    client1 = await make_client(nick="testserv-ori", user="ori")
    client2 = await make_client(nick="testserv-claude", user="claude")
    await client1.send("JOIN #general")
    await client1.recv_all(timeout=0.5)
    await client2.send("JOIN #general")
    await client2.recv_all(timeout=0.5)
    await client1.recv_all(timeout=0.5)

    await client1.send("PRIVMSG #general :@testserv-claude @testserv-claude hello")
    lines = await client2.recv_all(timeout=1.0)
    notice_lines = [l for l in lines if "mentioned you" in l]
    assert len(notice_lines) == 1
