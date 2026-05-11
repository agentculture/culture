import asyncio
import tempfile

import pytest
from cultureagent.clients.claude.daemon import AgentDaemon

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
)


@pytest.mark.asyncio
async def test_mention_full_nick(server, make_client):
    """@testserv-bot (full nick) should trigger mention callback."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)

    mentions = []
    daemon._on_mention = lambda t, s, txt: mentions.append((t, s, txt))
    daemon._transport_on_mention = daemon._on_mention

    await daemon.start()
    # Re-wire the transport's on_mention to our capturing function
    daemon._transport.on_mention = lambda t, s, txt: mentions.append((t, s, txt))
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    await human.send("PRIVMSG #general :@testserv-bot hello")
    await asyncio.sleep(0.3)

    assert len(mentions) >= 1
    assert mentions[0][0] == "#general"
    assert "hello" in mentions[0][2]

    await daemon.stop()


@pytest.mark.asyncio
async def test_mention_short_alias(server, make_client):
    """@bot (short suffix) should trigger mention callback for testserv-bot."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)

    mentions = []
    await daemon.start()
    daemon._transport.on_mention = lambda t, s, txt: mentions.append((t, s, txt))
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    await human.send("PRIVMSG #general :@bot are you there?")
    await asyncio.sleep(0.3)

    assert len(mentions) >= 1
    assert mentions[0][0] == "#general"
    assert "are you there?" in mentions[0][2]

    await daemon.stop()


@pytest.mark.asyncio
async def test_mention_unrelated_no_trigger(server, make_client):
    """@unrelated should NOT trigger mention callback."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)

    mentions = []
    await daemon.start()
    daemon._transport.on_mention = lambda t, s, txt: mentions.append((t, s, txt))
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    await human.send("PRIVMSG #general :@unrelated hello")
    await asyncio.sleep(0.3)

    assert len(mentions) == 0

    await daemon.stop()


@pytest.mark.asyncio
async def test_mention_substring_no_false_positive(server, make_client):
    """@botany should NOT trigger mention for testserv-bot (word boundary)."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)

    mentions = []
    await daemon.start()
    daemon._transport.on_mention = lambda t, s, txt: mentions.append((t, s, txt))
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)

    await human.send("PRIVMSG #general :@botany is a great hobby")
    await asyncio.sleep(0.3)

    assert len(mentions) == 0

    await daemon.stop()


@pytest.mark.asyncio
async def test_dm_activates_agent(server, make_client):
    """A direct message (PRIVMSG to agent nick) should trigger mention callback (#153)."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)

    mentions = []
    await daemon.start()
    daemon._transport.on_mention = lambda t, s, txt: mentions.append((t, s, txt))
    await asyncio.sleep(0.5)

    human = await make_client(nick="testserv-ori", user="ori")
    await asyncio.sleep(0.3)

    # Send a DM (PRIVMSG directly to agent nick, no @mention in text)
    await human.send("PRIVMSG testserv-bot :hello, are you there?")
    await asyncio.sleep(0.3)

    assert len(mentions) >= 1
    assert mentions[0][0] == "testserv-bot"
    assert "hello" in mentions[0][2]

    await daemon.stop()
