"""Tests for VirtualClient — bot IRC presence."""

import pytest

from agentirc.bots.virtual_client import VirtualClient


@pytest.mark.asyncio
async def test_join_channel_appears_in_names(server, make_client):
    """A VirtualClient that joins a channel should appear in NAMES."""
    bot = VirtualClient("testserv-ori-mybot", "mybot", server)
    await bot.join_channel("#test")

    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #test")
    await client.recv_all(timeout=0.5)

    # Ask for NAMES
    await client.send("NAMES #test")
    names_lines = await client.recv_all(timeout=0.5)
    names_text = " ".join(names_lines)
    assert "testserv-ori-mybot" in names_text

    await bot.part_channel("#test")


@pytest.mark.asyncio
async def test_send_to_channel_delivers_privmsg(server, make_client):
    """Messages from a VirtualClient should reach real clients."""
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #builds")
    await client.recv_all(timeout=0.5)

    bot = VirtualClient("testserv-ori-ghci", "ghci", server)
    await bot.join_channel("#builds")
    # Drain the JOIN notification
    await client.recv_all(timeout=0.3)

    await bot.send_to_channel("#builds", "CI completed for main")
    lines = await client.recv_all(timeout=0.5)
    assert any("CI completed for main" in line for line in lines)
    assert any("testserv-ori-ghci" in line for line in lines)

    await bot.part_channel("#builds")


@pytest.mark.asyncio
async def test_mention_triggers_notice(server, make_client):
    """@mention in a bot message should send a NOTICE to the target."""
    client = await make_client("testserv-claude", "claude")
    await client.send("JOIN #builds")
    await client.recv_all(timeout=0.5)

    bot = VirtualClient("testserv-ori-ghci", "ghci", server)
    await bot.join_channel("#builds")
    await client.recv_all(timeout=0.3)

    await bot.send_to_channel("#builds", "@testserv-claude CI done")
    lines = await client.recv_all(timeout=0.5)

    # Should get both the PRIVMSG and a NOTICE about the mention
    has_privmsg = any("PRIVMSG" in line and "CI done" in line for line in lines)
    has_notice = any("NOTICE" in line and "mentioned you" in line for line in lines)
    assert has_privmsg
    assert has_notice

    await bot.part_channel("#builds")


@pytest.mark.asyncio
async def test_part_channel_removes_from_names(server, make_client):
    """After parting, bot should not appear in NAMES."""
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #test")
    await client.recv_all(timeout=0.5)

    bot = VirtualClient("testserv-ori-bot", "bot", server)
    await bot.join_channel("#test")
    await client.recv_all(timeout=0.3)

    await bot.part_channel("#test")
    await client.recv_all(timeout=0.3)

    await client.send("NAMES #test")
    names_lines = await client.recv_all(timeout=0.5)
    names_text = " ".join(names_lines)
    assert "testserv-ori-bot" not in names_text


@pytest.mark.asyncio
async def test_send_dm(server, make_client):
    """VirtualClient should be able to DM a real client."""
    client = await make_client("testserv-agent", "agent")

    bot = VirtualClient("testserv-ori-bot", "bot", server)
    await bot.send_dm("testserv-agent", "Hello from bot")
    lines = await client.recv_all(timeout=0.5)
    assert any("Hello from bot" in line for line in lines)


@pytest.mark.asyncio
async def test_bot_not_auto_promoted_to_operator(server, make_client):
    """A VirtualClient should never become a channel operator."""
    bot = VirtualClient("testserv-ori-bot", "bot", server)
    await bot.join_channel("#optest")

    channel = server.channels["#optest"]
    assert bot not in channel.operators

    # Now a real client joins — they should become op, not the bot
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #optest")
    await client.recv_all(timeout=0.5)

    real_client = server.clients["testserv-agent"]
    assert real_client in channel.operators
    assert bot not in channel.operators

    await bot.part_channel("#optest")


@pytest.mark.asyncio
async def test_bot_send_is_noop(server):
    """VirtualClient.send() should silently succeed (no-op)."""
    from agentirc.protocol.message import Message

    bot = VirtualClient("testserv-ori-bot", "bot", server)
    msg = Message(prefix="someone", command="PRIVMSG", params=["bot", "hello"])
    await bot.send(msg)  # Should not raise


@pytest.mark.asyncio
async def test_prefix_format(server):
    bot = VirtualClient("testserv-ori-bot", "mybot", server)
    assert bot.prefix == "testserv-ori-bot!mybot@bot"


@pytest.mark.asyncio
async def test_tags_include_bot(server):
    bot = VirtualClient("testserv-ori-bot", "bot", server)
    assert "bot" in bot.tags
