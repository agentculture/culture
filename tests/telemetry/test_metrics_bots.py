"""Metrics for bots — culture.bot.invocations (Plan 7)."""

from __future__ import annotations

import pytest
from agentirc.protocol import Event, EventType

from tests.telemetry._metrics_helpers import get_counter_value


@pytest.mark.asyncio
async def test_bot_invocations_counter_success(metrics_reader, server_with_bot):
    """Successful event dispatch increments bot_invocations with outcome=success."""
    server, _ = server_with_bot(
        bot_name="testserv-ok",
        trigger_type="event",
        event_filter="type == 'topic'",
        channels=["#a"],
        template="hi {event.nick}",
    )
    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#a", nick="testserv-j", data={"body": "x"})
    )

    n = get_counter_value(
        metrics_reader,
        "culture.bot.invocations",
        attrs={"bot": "testserv-ok", "event.type": "topic", "outcome": "success"},
    )
    assert n == 1


@pytest.mark.asyncio
async def test_bot_invocations_counter_error(metrics_reader, server_with_bot, monkeypatch):
    """A bot whose handle raises increments outcome=error."""
    server, _ = server_with_bot(
        bot_name="testserv-fail",
        trigger_type="event",
        event_filter="type == 'topic'",
        channels=["#b"],
        template="x",
    )
    bot = server.bot_manager.bots["testserv-fail"]

    async def boom(payload):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(bot, "handle", boom)

    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#b", nick="testserv-j", data={"body": "x"})
    )

    err = get_counter_value(
        metrics_reader,
        "culture.bot.invocations",
        attrs={"bot": "testserv-fail", "event.type": "topic", "outcome": "error"},
    )
    ok = get_counter_value(
        metrics_reader,
        "culture.bot.invocations",
        attrs={"bot": "testserv-fail", "event.type": "topic", "outcome": "success"},
    )
    assert err == 1
    assert ok == 0


@pytest.mark.asyncio
async def test_bot_invocations_counter_two_bots_one_event(metrics_reader, server_with_bots):
    """Two matched bots → counter increments once for each, distinguished by bot label."""
    server, _ = server_with_bots(
        [
            {
                "bot_name": "testserv-a",
                "trigger_type": "event",
                "event_filter": "type == 'topic'",
                "channels": ["#c"],
                "template": "a",
            },
            {
                "bot_name": "testserv-b",
                "trigger_type": "event",
                "event_filter": "type == 'topic'",
                "channels": ["#c"],
                "template": "b",
            },
        ]
    )

    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#c", nick="testserv-z", data={"body": "y"})
    )

    a = get_counter_value(
        metrics_reader,
        "culture.bot.invocations",
        attrs={"bot": "testserv-a", "outcome": "success"},
    )
    b = get_counter_value(
        metrics_reader,
        "culture.bot.invocations",
        attrs={"bot": "testserv-b", "outcome": "success"},
    )
    assert a == 1
    assert b == 1


@pytest.mark.asyncio
async def test_bot_invocations_counter_no_match(metrics_reader, server_with_bot):
    """Non-matching event must not move the counter for that bot."""
    server, _ = server_with_bot(
        bot_name="testserv-nomatch",
        trigger_type="event",
        event_filter="channel == '#never'",
        channels=["#z"],
        template="x",
    )
    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#other", nick="testserv-q", data={"body": "x"})
    )

    n = get_counter_value(
        metrics_reader,
        "culture.bot.invocations",
        attrs={"bot": "testserv-nomatch"},
    )
    assert n == 0
