"""Shared fixtures for telemetry integration tests."""

from __future__ import annotations

import pytest_asyncio

from culture_core.bots.bot_manager import BotManager
from culture_core.bots.http_listener import HttpListener


@pytest_asyncio.fixture
async def webhook_server(server, tmp_path, monkeypatch):
    """IRCd + BotManager + running HttpListener on a random local port.

    Yields ``(server, mgr, port)``. The HttpListener is torn down and all
    bots stopped at the end of the test.
    """
    monkeypatch.setattr("culture_core.bots.config.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture_core.bots.bot.BOTS_DIR", tmp_path)
    monkeypatch.setattr("culture_core.bots.bot_manager.BOTS_DIR", tmp_path)

    mgr = BotManager(server)
    server.bot_manager = mgr

    listener = HttpListener(mgr, "127.0.0.1", 0)
    await listener.start()
    site = next(iter(listener._runner._sites))
    port = site._server.sockets[0].getsockname()[1]

    yield server, mgr, port

    await listener.stop()
    await mgr.stop_all()
