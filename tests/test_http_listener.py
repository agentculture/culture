"""Tests for the webhook HTTP listener."""

import asyncio
import json

import pytest
import pytest_asyncio
from aiohttp import ClientSession

from agentirc.bots.bot_manager import BotManager
from agentirc.bots.config import BotConfig
from agentirc.bots.http_listener import HttpListener


@pytest_asyncio.fixture
async def webhook_server(server, tmp_path, monkeypatch):
    """Start an IRC server with BotManager and HttpListener."""
    monkeypatch.setattr("agentirc.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("agentirc.bots.bot_manager.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    server.bot_manager = mgr

    listener = HttpListener(mgr, "127.0.0.1", 0)
    await listener.start()

    # Get the actual port
    site = list(listener._runner._sites)[0]
    port = site._server.sockets[0].getsockname()[1]

    yield server, mgr, port

    await listener.stop()
    await mgr.stop_all()


@pytest.mark.asyncio
async def test_health_endpoint(webhook_server):
    _, _, port = webhook_server
    async with ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/health") as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"


@pytest.mark.asyncio
async def test_webhook_post_success(webhook_server, make_client):
    ircd, mgr, port = webhook_server
    client = await make_client("testserv-agent", "agent")
    await client.send("JOIN #builds")
    await client.recv_all(timeout=0.5)

    await mgr.create_bot(
        BotConfig(
            name="testserv-ori-ci",
            owner="testserv-ori",
            channels=["#builds"],
            template="Build {body.status} for {body.branch}",
        )
    )
    await client.recv_all(timeout=0.3)

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-ori-ci",
            json={"status": "passed", "branch": "main"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "Build passed for main" in data["message"]

    lines = await client.recv_all(timeout=0.5)
    assert any("Build passed for main" in line for line in lines)


@pytest.mark.asyncio
async def test_webhook_unknown_bot(webhook_server):
    _, _, port = webhook_server
    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/nonexistent",
            json={"test": True},
        ) as resp:
            assert resp.status == 404
            data = await resp.json()
            assert "not found" in data["error"]


@pytest.mark.asyncio
async def test_webhook_stopped_bot(webhook_server):
    _, mgr, port = webhook_server
    await mgr.create_bot(
        BotConfig(
            name="testserv-ori-stopped",
            channels=["#test"],
        )
    )
    await mgr.stop_bot("testserv-ori-stopped")

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-ori-stopped",
            json={"test": True},
        ) as resp:
            assert resp.status == 503
            data = await resp.json()
            assert "not active" in data["error"]


@pytest.mark.asyncio
async def test_webhook_invalid_json(webhook_server):
    _, mgr, port = webhook_server
    await mgr.create_bot(
        BotConfig(
            name="testserv-ori-badjson",
            channels=["#test"],
        )
    )

    async with ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/testserv-ori-badjson",
            data=b"not json",
            headers={"Content-Type": "application/json"},
        ) as resp:
            assert resp.status == 400
            data = await resp.json()
            assert "invalid JSON" in data["error"]
