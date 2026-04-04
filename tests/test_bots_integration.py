"""End-to-end integration test for the full bot webhook flow."""

import asyncio

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from agentirc.bots.bot_manager import BotManager
from agentirc.bots.config import BotConfig
from agentirc.bots.http_listener import HttpListener


@pytest_asyncio.fixture
async def full_bot_setup(server, make_client, tmp_path, monkeypatch):
    """Start an IRC server with BotManager, HttpListener, and a test client."""
    monkeypatch.setattr("agentirc.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot_manager.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    server.bot_manager = mgr

    listener = HttpListener(mgr, "127.0.0.1", 0)
    await listener.start()
    site = list(listener._runner._sites)[0]
    port = site._server.sockets[0].getsockname()[1]

    yield server, mgr, port, make_client

    await listener.stop()
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_full_webhook_to_irc_flow(full_bot_setup):
    """POST webhook → bot renders template → PRIVMSG in channel → agent gets @mention."""
    server, mgr, port, make_client = full_bot_setup

    # Connect an "agent" to the channel
    agent = await make_client("testserv-claude", "claude")
    await agent.send("JOIN #builds")
    await agent.recv_all(timeout=0.5)

    # Connect an "owner" for DM testing
    owner = await make_client("testserv-ori", "ori")

    # Create a bot that posts to #builds, @mentions claude, and DMs the owner
    await mgr.create_bot(
        BotConfig(
            name="testserv-ori-ghci",
            owner="testserv-ori",
            description="GitHub CI notifier",
            channels=["#builds"],
            dm_owner=True,
            mention="testserv-claude",
            template="CI {body.action} for {body.repo}: {body.status}",
        )
    )
    # Drain the bot's JOIN notification
    await agent.recv_all(timeout=0.3)

    # POST a webhook
    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-ori-ghci",
            json={"action": "completed", "repo": "agentirc", "status": "success"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "CI completed for agentirc: success" in data["message"]
            assert "@testserv-claude" in data["message"]

    # Agent should receive the PRIVMSG + NOTICE (mention)
    agent_lines = await agent.recv_all(timeout=0.5)
    assert any("CI completed for agentirc: success" in l for l in agent_lines)
    assert any("PRIVMSG" in l and "@testserv-claude" in l for l in agent_lines)
    assert any("NOTICE" in l and "mentioned you" in l for l in agent_lines)

    # Owner should receive a DM
    owner_lines = await owner.recv_all(timeout=0.5)
    assert any("CI completed for agentirc: success" in l for l in owner_lines)


@pytest.mark.asyncio
async def test_bot_lifecycle_via_webhook(full_bot_setup):
    """Create bot → POST works → stop bot → POST returns 503 → restart → POST works again."""
    server, mgr, port, make_client = full_bot_setup

    agent = await make_client("testserv-agent", "agent")
    await agent.send("JOIN #test")
    await agent.recv_all(timeout=0.5)

    await mgr.create_bot(
        BotConfig(
            name="testserv-ori-lifecycle",
            channels=["#test"],
            template="Event: {body.type}",
        )
    )
    await agent.recv_all(timeout=0.3)

    url = f"http://127.0.0.1:{port}/testserv-ori-lifecycle"

    # Should work
    async with ClientSession() as session:
        async with session.post(url, json={"type": "deploy"}) as resp:
            assert resp.status == 200

    lines = await agent.recv_all(timeout=0.5)
    assert any("Event: deploy" in l for l in lines)

    # Stop the bot
    await mgr.stop_bot("testserv-ori-lifecycle")

    # Should return 503
    async with ClientSession() as session:
        async with session.post(url, json={"type": "deploy"}) as resp:
            assert resp.status == 503

    # Restart the bot
    await mgr.start_bot("testserv-ori-lifecycle")

    # Should work again
    async with ClientSession() as session:
        async with session.post(url, json={"type": "rollback"}) as resp:
            assert resp.status == 200

    lines = await agent.recv_all(timeout=0.5)
    assert any("Event: rollback" in l for l in lines)


@pytest.mark.asyncio
async def test_bot_fallback_to_json(full_bot_setup):
    """When template has unresolvable tokens, fall back to JSON stringify."""
    server, mgr, port, make_client = full_bot_setup

    agent = await make_client("testserv-agent", "agent")
    await agent.send("JOIN #raw")
    await agent.recv_all(timeout=0.5)

    await mgr.create_bot(
        BotConfig(
            name="testserv-ori-rawbot",
            channels=["#raw"],
            template="Missing: {body.nonexistent_field}",
            fallback="json",
        )
    )
    await agent.recv_all(timeout=0.3)

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-ori-rawbot",
            json={"actual": "data"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            # Should contain the JSON-stringified payload
            assert '"actual"' in data["message"]

    lines = await agent.recv_all(timeout=0.5)
    assert any('"actual"' in l for l in lines)
