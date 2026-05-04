"""Tests for the welcome system bot."""

import asyncio

import pytest
from agentirc.protocol import Event, EventType

from tests.conftest import IRCTestClient

# ---------------------------------------------------------------------------
# test_welcome_bot_greets_on_join
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_welcome_bot_greets_on_join(server, make_client):
    """Welcome bot greets a user when they join a channel."""
    # Ensure the server has a welcome system bot loaded
    welcome_nick = f"system-{server.config.name}-welcome"
    bot = server.bot_manager.bots.get(welcome_nick)
    assert bot is not None, (
        f"Expected system bot {welcome_nick!r} to be registered. "
        f"Registered bots: {list(server.bot_manager.bots)}"
    )

    # Connect a watcher who will receive the greeting
    watcher = await make_client("testserv-watcher", "watcher")
    await watcher.send("JOIN #lobby")
    await watcher.recv_all(timeout=0.5)  # drain welcome/JOIN messages

    # Emit a user.join event for the channel
    await server.emit_event(
        Event(
            type=EventType.JOIN,
            channel="#lobby",
            nick="testserv-newbie",
            data={"nick": "testserv-newbie"},
        )
    )

    lines = await watcher.recv_all(timeout=1.0)
    greeting = "Welcome testserv-newbie to #lobby"
    assert any(
        greeting in line for line in lines
    ), f"Expected greeting {greeting!r} not found in lines: {lines}"


# ---------------------------------------------------------------------------
# test_welcome_bot_disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_welcome_bot_disabled(server_welcome_disabled):
    """When welcome bot is disabled via config, it is not registered."""
    server = server_welcome_disabled
    welcome_nick = f"system-{server.config.name}-welcome"
    bot = server.bot_manager.bots.get(welcome_nick)
    assert (
        bot is None
    ), f"Expected welcome bot to be absent when disabled, but found: {welcome_nick!r}"

    # Connect a watcher directly to this server, emit a join — no greeting should arrive
    reader, writer = await asyncio.open_connection("127.0.0.1", server.config.port)
    watcher = IRCTestClient(reader, writer)
    try:
        await watcher.send("NICK testserv-watcher2")
        await watcher.send("USER watcher2 0 * :watcher2")
        await watcher.recv_all(timeout=0.5)  # drain welcome messages
        await watcher.send("JOIN #lobby")
        await watcher.recv_all(timeout=0.5)

        await server.emit_event(
            Event(
                type=EventType.JOIN,
                channel="#lobby",
                nick="testserv-ghost",
                data={"nick": "testserv-ghost"},
            )
        )

        lines = await watcher.recv_all(timeout=0.5)
        assert not any(
            "Welcome testserv-ghost" in line for line in lines
        ), f"Greeting should not appear when bot is disabled. Lines: {lines}"
    finally:
        await watcher.close()
