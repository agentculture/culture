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
# test_welcome_bot_skips_peek_nicks (#334)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "peek_nick",
    [
        "testserv-_peek7aef",  # legacy opaque peek nick
        "testserv-claude__peek1234",  # parent-attributed peek nick (#329)
    ],
)
async def test_welcome_bot_skips_peek_nicks(server, make_client, peek_nick):
    """The welcome bot must not greet transient peek-client joins (#334).

    Every `culture channel message` call connects an ephemeral peek
    client whose nick contains the `_peek` marker. Greeting them
    produced 4 lines of bot chatter per real CLI message — a 5:1 noise
    ratio that buried real conversation in `culture channel read`.
    """
    welcome_nick = f"system-{server.config.name}-welcome"
    assert server.bot_manager.bots.get(welcome_nick) is not None

    watcher = await make_client("testserv-watcher3", "watcher3")
    await watcher.send("JOIN #lobby")
    await watcher.recv_all(timeout=0.5)

    await server.emit_event(
        Event(
            type=EventType.JOIN,
            channel="#lobby",
            nick=peek_nick,
            data={"nick": peek_nick},
        )
    )

    lines = await watcher.recv_all(timeout=0.5)
    greeting = f"Welcome {peek_nick} to #lobby"
    assert not any(greeting in line for line in lines), (
        f"Welcome bot greeted peek nick {peek_nick!r} — should have been "
        f"filtered out. Lines: {lines}"
    )


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
