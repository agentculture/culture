"""Tests for bot chain: bot A fires an event that triggers bot B."""

import pytest
from agentirc.protocol import Event, EventType

from culture.bots.config import EmitEventSpec


@pytest.mark.asyncio
async def test_bot_chain_fires_event(server_with_bots, make_client):
    """Bot A fires on user.join, emits a custom event; bot B triggers on that event."""
    # Bot A: triggered by user.join, fires custom.alert event
    fires_a = EmitEventSpec(
        type="custom.alert",
        data={"source": "bot-a"},
    )

    # Bot B: triggered by custom.alert, posts to #alerts channel
    server, _ = server_with_bots(
        [
            {
                "bot_name": "testserv-bot-a",
                "trigger_type": "event",
                "event_filter": "type == 'user.join'",
                "channels": [],
                "template": None,
                "fires_event": fires_a,
            },
            {
                "bot_name": "testserv-bot-b",
                "trigger_type": "event",
                "event_filter": "type == 'custom.alert'",
                "channels": ["#alerts"],
                "template": "Chain alert: {event.data.source}",
                "fires_event": None,
            },
        ]
    )

    # Agent subscribes to #alerts to observe bot B
    agent = await make_client("testserv-chain-watcher", "watcher")
    await agent.send("JOIN #alerts")
    await agent.recv_all(timeout=0.5)

    # Emit user.join — should chain: bot A fires custom.alert → bot B posts
    await server.emit_event(
        Event(
            type=EventType.JOIN,
            channel="#lobby",
            nick="testserv-newbie",
            data={"nick": "testserv-newbie"},
        )
    )

    # Allow async chain to propagate
    lines = await agent.recv_all(timeout=0.8)
    assert any(
        "Chain alert" in line for line in lines
    ), f"Bot chain message not received. Lines: {lines}"
