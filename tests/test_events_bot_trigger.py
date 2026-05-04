"""Tests for event-triggered bots (trigger_type: event + filter DSL)."""

import pytest
from agentirc.protocol import Event, EventType

# ---------------------------------------------------------------------------
# test_event_triggered_bot_runs
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_triggered_bot_runs(server_with_bot, make_client):
    """A bot with trigger_type=event fires on a matching event and posts to channels."""
    server, _ = server_with_bot(
        bot_name="testserv-evt-bot",
        trigger_type="event",
        event_filter="type == 'user.join'",
        channels=["#general"],
        template="User joined: {event.nick}",
    )

    # Connect a real client so it can join and receive the bot message
    agent = await make_client("testserv-watcher", "watcher")
    await agent.send("JOIN #general")
    await agent.recv_all(timeout=0.5)  # drain JOIN ack + bot JOIN

    # Emit a matching event
    await server.emit_event(
        Event(
            type=EventType.JOIN,
            channel="#general",
            nick="testserv-joiner",
            data={"nick": "testserv-joiner"},
        )
    )

    lines = await agent.recv_all(timeout=0.5)
    assert any(
        "User joined: testserv-joiner" in line for line in lines
    ), f"Expected bot message not in lines: {lines}"


# ---------------------------------------------------------------------------
# test_filter_mismatch_does_not_fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_mismatch_does_not_fire(server_with_bot, make_client):
    """When the filter doesn't match, the bot must not post anything."""
    server, _ = server_with_bot(
        bot_name="testserv-silent-bot",
        trigger_type="event",
        event_filter="channel == '#restricted'",
        channels=["#general"],
        template="Triggered: {event.nick}",
    )

    agent = await make_client("testserv-spy", "spy")
    await agent.send("JOIN #general")
    await agent.recv_all(timeout=0.5)

    # Emit an event whose channel does NOT match the filter
    await server.emit_event(
        Event(
            type=EventType.JOIN,
            channel="#other",
            nick="testserv-nobody",
            data={"nick": "testserv-nobody"},
        )
    )

    lines = await agent.recv_all(timeout=0.4)
    assert not any("Triggered:" in line for line in lines), f"Bot fired unexpectedly: {lines}"


# ---------------------------------------------------------------------------
# test_bad_filter_rejected_at_load
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bad_filter_rejected_at_load(server):
    """A malformed filter expression raises ValueError when the bot is registered."""
    from culture.bots.bot_manager import BotManager
    from culture.bots.config import BotConfig

    mgr = BotManager(server=server)
    cfg = BotConfig(
        name="testserv-badfilter-bot",
        owner="testserv",
        trigger_type="event",
        event_filter="type == (",  # deliberately malformed
        channels=[],
    )

    with pytest.raises(ValueError, match="invalid filter"):
        mgr.register_bot(cfg)
