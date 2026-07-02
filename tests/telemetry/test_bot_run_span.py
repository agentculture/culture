"""Spans for Bot.handle — bot.run (Plan 7)."""

from __future__ import annotations

import pytest
from agentirc.protocol import Event, EventType


@pytest.mark.asyncio
async def test_bot_run_span_parented_under_dispatch(tracing_exporter, server_with_bot):
    """bot.run span exists and is parented under bot.event.dispatch in the event path."""
    server, _ = server_with_bot(
        bot_name="testserv-runner",
        trigger_type="event",
        event_filter="type == 'topic'",
        channels=["#room"],
        template="hi {event.nick}",
    )

    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#room", nick="testserv-x", data={"body": "y"})
    )

    spans = tracing_exporter.get_finished_spans()
    run_spans = [
        s
        for s in spans
        if s.name == "bot.run" and s.attributes.get("bot.name") == "testserv-runner"
    ]
    assert len(run_spans) == 1
    run = run_spans[0]
    assert run.attributes["bot.name"] == "testserv-runner"

    dispatch = next(
        s
        for s in spans
        if s.name == "bot.event.dispatch" and s.attributes.get("bot.name") == "testserv-runner"
    )
    assert run.parent is not None
    assert run.parent.span_id == dispatch.context.span_id


@pytest.mark.asyncio
async def test_bot_run_span_marks_empty_message(tracing_exporter, server_with_bot):
    """Empty rendered message sets bot.run.empty_message=True."""
    # Whitespace-only template strips to "" inside Bot._render_message,
    # forcing the empty-message branch deterministically.
    server, _ = server_with_bot(
        bot_name="testserv-empty",
        trigger_type="event",
        event_filter="type == 'topic'",
        channels=["#z"],
        template="   ",
    )

    await server.emit_event(
        Event(type=EventType.TOPIC, channel="#z", nick="testserv-y", data={"body": "x"})
    )

    run_spans = [
        s
        for s in tracing_exporter.get_finished_spans()
        if s.name == "bot.run" and s.attributes.get("bot.name") == "testserv-empty"
    ]
    assert len(run_spans) == 1
    assert run_spans[0].attributes.get("bot.run.empty_message") is True


@pytest.mark.asyncio
async def test_bot_run_span_via_dispatch_method(tracing_exporter, server_with_bot):
    """Calling BotManager.dispatch(...) directly (webhook path proxy) still produces bot.run."""
    server, _ = server_with_bot(
        bot_name="testserv-direct",
        trigger_type="webhook",
        channels=["#hooks"],
        template="hi {body}",
    )

    bot = server.bot_manager.bots["testserv-direct"]
    await bot.start()

    await server.bot_manager.dispatch("testserv-direct", {"body": "ping"})

    run_spans = [
        s
        for s in tracing_exporter.get_finished_spans()
        if s.name == "bot.run" and s.attributes.get("bot.name") == "testserv-direct"
    ]
    assert len(run_spans) == 1
    assert run_spans[0].attributes["bot.name"] == "testserv-direct"
