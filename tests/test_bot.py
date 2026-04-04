"""Tests for the Bot entity."""

import asyncio

import pytest
import pytest_asyncio

from agentirc.bots.bot import Bot
from agentirc.bots.config import BOTS_DIR, BotConfig


@pytest.fixture
def bot_config():
    return BotConfig(
        name="testserv-ori-ghci",
        owner="testserv-ori",
        description="Test CI bot",
        created="2026-04-03",
        trigger_type="webhook",
        channels=["#builds"],
        dm_owner=False,
        mention=None,
        template="CI {body.action} for {body.repo}",
        fallback="json",
    )


@pytest.mark.asyncio
async def test_bot_start_creates_virtual_client(server, bot_config):
    bot = Bot(bot_config, server)
    await bot.start()
    assert bot.active
    assert bot.virtual_client is not None
    assert bot.virtual_client.nick == "testserv-ori-ghci"
    assert "#builds" in server.channels
    assert bot.virtual_client in server.channels["#builds"].members
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_stop_cleans_up(server, bot_config):
    bot = Bot(bot_config, server)
    await bot.start()
    await bot.stop()
    assert not bot.active
    assert bot.virtual_client is None


@pytest.mark.asyncio
async def test_bot_handle_with_template(server, make_client, bot_config):
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #builds")
    await client.recv_all(timeout=0.5)

    bot = Bot(bot_config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"action": "completed", "repo": "myrepo"})
    assert "CI completed for myrepo" in result

    lines = await client.recv_all(timeout=0.5)
    assert any("CI completed for myrepo" in line for line in lines)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_handle_fallback_json(server, make_client):
    config = BotConfig(
        name="testserv-ori-fallbot",
        channels=["#test"],
        template="Missing: {body.nonexistent}",
        fallback="json",
    )
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #test")
    await client.recv_all(timeout=0.5)

    bot = Bot(config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"key": "value"})
    assert '"key"' in result  # JSON stringified

    await bot.stop()


@pytest.mark.asyncio
async def test_bot_handle_with_mention(server, make_client):
    config = BotConfig(
        name="testserv-ori-mentionbot",
        channels=["#builds"],
        mention="testserv-claude",
        template="Build done: {body.status}",
    )
    client = await make_client("testserv-claude", "claude")
    await client.send("JOIN #builds")
    await client.recv_all(timeout=0.5)

    bot = Bot(config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"status": "success"})
    assert "@testserv-claude" in result

    lines = await client.recv_all(timeout=0.5)
    privmsg_lines = [l for l in lines if "PRIVMSG" in l]
    assert any("@testserv-claude" in l for l in privmsg_lines)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_handle_with_dm_owner(server, make_client):
    config = BotConfig(
        name="testserv-ori-dmbot",
        owner="testserv-ori",
        channels=["#builds"],
        dm_owner=True,
        template="Event: {body.type}",
    )
    owner = await make_client("testserv-ori", "ori")

    bot = Bot(config, server)
    await bot.start()

    await bot.handle({"type": "deploy"})
    lines = await owner.recv_all(timeout=0.5)
    assert any("Event: deploy" in line for line in lines)
    await bot.stop()


@pytest.mark.asyncio
async def test_bot_nick_collision(server, make_client, bot_config):
    # Register a real client with the same nick
    await make_client("testserv-ori-ghci", "ghci")

    bot = Bot(bot_config, server)
    with pytest.raises(ValueError, match="already in use"):
        await bot.start()


@pytest.mark.asyncio
async def test_bot_handle_not_active(server, bot_config):
    bot = Bot(bot_config, server)
    with pytest.raises(RuntimeError, match="not active"):
        await bot.handle({"test": True})


@pytest.mark.asyncio
async def test_bot_webhook_url(server, bot_config):
    bot = Bot(bot_config, server)
    url = bot.webhook_url
    assert "testserv-ori-ghci" in url
    assert str(server.config.webhook_port) in url


@pytest.mark.asyncio
async def test_bot_no_template_uses_json(server, make_client):
    config = BotConfig(
        name="testserv-ori-rawbot",
        channels=["#test"],
        template=None,
        fallback="json",
    )
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #test")
    await client.recv_all(timeout=0.5)

    bot = Bot(config, server)
    await bot.start()
    await client.recv_all(timeout=0.3)

    result = await bot.handle({"raw": "data"})
    assert '"raw"' in result
    await bot.stop()
