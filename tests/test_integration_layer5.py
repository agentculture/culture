"""End-to-end Layer 5 integration tests."""

import asyncio
import os
import tempfile

import pytest
from cultureagent.clients.claude.daemon import AgentDaemon
from cultureagent.clients.shared.skill_irc_client import SkillClient

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)


@pytest.mark.asyncio
async def test_full_send_receive_flow(server, make_client):
    """Agent sends via skill → human receives on IRC → human replies → agent reads."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
    skill = SkillClient(sock_path)
    await skill.connect()
    try:
        result = await skill.irc_send("#general", "hello from agent")
        assert result["ok"]
        msg = await human.recv(timeout=2.0)
        assert "hello from agent" in msg
        await human.send("PRIVMSG #general :hello back agent")
        await asyncio.sleep(0.3)
        result = await skill.irc_read("#general", limit=50)
        assert result["ok"]
        messages = result["data"]["messages"]
        assert any("hello back agent" in m["text"] for m in messages)
    finally:
        await skill.close()
        await daemon.stop()


@pytest.mark.asyncio
async def test_join_part_via_skill(server):
    """Skill client can join and part channels dynamically."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
    skill = SkillClient(sock_path)
    await skill.connect()
    try:
        result = await skill.irc_join("#testing")
        assert result["ok"]
        await asyncio.sleep(0.2)
        assert "#testing" in server.channels
        result = await skill.irc_channels()
        assert result["ok"]
        assert "#testing" in result["data"]["channels"]
        result = await skill.irc_part("#testing")
        assert result["ok"]
        await asyncio.sleep(0.2)
        assert "#testing" not in server.channels
    finally:
        await skill.close()
        await daemon.stop()


@pytest.mark.asyncio
async def test_webhook_fires_on_question(server, make_client):
    """Webhook fires when agent uses irc_ask."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None, irc_channel="#alerts", events=["agent_question"]),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general", "#alerts"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    watcher = await make_client(nick="testserv-watch", user="watch")
    await watcher.send("JOIN #alerts")
    await watcher.recv_all(timeout=0.3)
    sock_path = os.path.join(sock_dir, "culture-testserv-bot.sock")
    skill = SkillClient(sock_path)
    await skill.connect()
    try:
        await skill.irc_ask("#general", "what cmake flags?", timeout=1)
        await asyncio.sleep(0.5)
        alerts = await watcher.recv_all(timeout=1.0)
        assert any("QUESTION" in line for line in alerts)
    finally:
        await skill.close()
        await daemon.stop()
