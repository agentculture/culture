"""Spans for BotManager.on_event — bot.event.dispatch (Plan 7)."""

from __future__ import annotations

import pytest
from agentirc.protocol import Event, EventType


@pytest.mark.asyncio
async def test_bot_event_dispatch_span_per_match(tracing_exporter, server_with_bot):
    """A matched event-triggered bot produces one bot.event.dispatch span with attrs."""
    server, _ = server_with_bot(
        bot_name="testserv-watcher",
        trigger_type="event",
        event_filter="type == 'topic'",
        channels=["#general"],
        template="hi {event.nick}",
    )

    await server.emit_event(
        Event(
            type=EventType.TOPIC,
            channel="#general",
            nick="testserv-newcomer",
            data={"body": "hi"},
        )
    )

    spans = tracing_exporter.get_finished_spans()
    dispatch_spans = [
        s
        for s in spans
        if s.name == "bot.event.dispatch" and s.attributes.get("bot.name") == "testserv-watcher"
    ]
    assert len(dispatch_spans) == 1, [s.name for s in spans]
    span = dispatch_spans[0]
    assert span.attributes["bot.name"] == "testserv-watcher"
    assert span.attributes["event.type"] == "topic"

    # Parented under irc.event.emit (contextvars-propagated).
    emit_spans = [s for s in spans if s.name == "irc.event.emit"]
    assert span.parent is not None
    assert any(span.parent.span_id == e.context.span_id for e in emit_spans)


@pytest.mark.asyncio
async def test_bot_event_dispatch_span_one_per_matched_bot(tracing_exporter, server_with_bots):
    """Two bots match the same event → two bot.event.dispatch spans, one per bot."""
    server, _ = server_with_bots(
        [
            {
                "bot_name": "testserv-a",
                "trigger_type": "event",
                "event_filter": "type == 'topic'",
                "channels": ["#x"],
                "template": "a",
            },
            {
                "bot_name": "testserv-b",
                "trigger_type": "event",
                "event_filter": "type == 'topic'",
                "channels": ["#x"],
                "template": "b",
            },
        ]
    )

    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#x", nick="testserv-z", data={"body": "y"})
    )

    dispatch_spans = [
        s
        for s in tracing_exporter.get_finished_spans()
        if s.name == "bot.event.dispatch"
        and s.attributes.get("bot.name") in {"testserv-a", "testserv-b"}
    ]
    bot_names = sorted(s.attributes["bot.name"] for s in dispatch_spans)
    assert bot_names == ["testserv-a", "testserv-b"]


@pytest.mark.asyncio
async def test_no_dispatch_span_when_filter_rejects(tracing_exporter, server_with_bot):
    """Filter rejection produces no bot.event.dispatch span for that bot."""
    server, _ = server_with_bot(
        bot_name="testserv-silent",
        trigger_type="event",
        event_filter="channel == '#never'",
        channels=["#general"],
        template="x",
    )

    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#other", nick="testserv-q", data={"body": "x"})
    )

    dispatch_spans = [
        s
        for s in tracing_exporter.get_finished_spans()
        if s.name == "bot.event.dispatch" and s.attributes.get("bot.name") == "testserv-silent"
    ]
    assert dispatch_spans == []


@pytest.mark.asyncio
async def test_bot_event_dispatch_span_error_status_on_handle_raise(
    tracing_exporter, server_with_bot, monkeypatch
):
    """When Bot.handle raises, the dispatch span carries StatusCode.ERROR."""
    from opentelemetry.trace import StatusCode

    server, _ = server_with_bot(
        bot_name="testserv-broken",
        trigger_type="event",
        event_filter="type == 'topic'",
        channels=["#err"],
        template="x",
    )
    bot = server.bot_manager.bots["testserv-broken"]

    async def boom(payload):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(bot, "handle", boom)

    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#err", nick="testserv-x", data={"body": "x"})
    )

    dispatch_spans = [
        s
        for s in tracing_exporter.get_finished_spans()
        if s.name == "bot.event.dispatch" and s.attributes.get("bot.name") == "testserv-broken"
    ]
    assert len(dispatch_spans) == 1
    assert dispatch_spans[0].status.status_code == StatusCode.ERROR
    assert "kaboom" in (dispatch_spans[0].status.description or "")
