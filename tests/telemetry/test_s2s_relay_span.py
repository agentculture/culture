"""Tests for the irc.s2s.relay span on ServerLink.relay_event.

Verifies the per-hop re-sign rule from culture/protocol/extensions/tracing.md:
the wire-injected traceparent's parent-id matches the relay span's id (NOT
the inbound trace's parent-id), even when an inbound trace context is active.
"""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry import trace as otel_trace

from culture.agentirc.skill import Event, EventType
from culture.telemetry.context import TRACEPARENT_TAG


async def _wait_for_span(exporter, name: str, timeout: float = 1.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if any(s.name == name for s in exporter.get_finished_spans()):
            return
        await asyncio.sleep(0.02)


def _spans_with_name(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


@pytest.mark.asyncio
async def test_relay_span_recorded_with_event_type_and_peer(tracing_exporter, linked_servers):
    server_a, server_b = linked_servers
    tracing_exporter.clear()

    # Trigger a relay by calling relay_event directly (skip _dispatch path).
    link_to_b = server_a.links["beta"]
    event = Event(
        type=EventType.MESSAGE,
        channel=None,
        nick="alpha-bob",
        data={"target": "alpha-charlie", "text": "hi"},
    )
    await link_to_b.relay_event(event)

    await _wait_for_span(tracing_exporter, "irc.s2s.relay")
    spans = _spans_with_name(tracing_exporter, "irc.s2s.relay")
    assert spans, "no irc.s2s.relay span recorded"
    span = spans[-1]
    attrs = dict(span.attributes or {})
    assert attrs.get("event.type") == "message"
    assert attrs.get("s2s.peer") == "beta"


@pytest.mark.asyncio
async def test_relay_resigns_per_hop(tracing_exporter, linked_servers):
    """Outbound traceparent on relayed wire bytes points to the relay span,
    NOT to whatever inbound trace was active. This is the per-hop re-sign rule.
    """
    server_a, server_b = linked_servers
    tracing_exporter.clear()

    # Record raw bytes server_b sees on the link from alpha. We swap the
    # writer's `write` method on the alpha->beta link with a recording one,
    # then call relay_event from inside an outer span. The outer span
    # simulates an "inbound" trace context that should NOT leak into the wire.
    link_to_b = server_a.links["beta"]
    captured: list[bytes] = []
    real_write = link_to_b.writer.write

    def recording_write(data):
        captured.append(data)
        return real_write(data)

    link_to_b.writer.write = recording_write

    tracer = otel_trace.get_tracer("test")
    outer_span_id_hex = None
    try:
        with tracer.start_as_current_span("simulated_inbound") as outer:
            outer_span_id_hex = format(outer.get_span_context().span_id, "016x")
            event = Event(
                type=EventType.MESSAGE,
                channel=None,
                nick="alpha-bob",
                data={"target": "alpha-charlie", "text": "ping"},
            )
            await link_to_b.relay_event(event)
    finally:
        link_to_b.writer.write = real_write

    # Find the wire bytes that carry the SMSG line.
    relayed = [b.decode("utf-8", errors="replace") for b in captured]
    smsg_lines = [
        line
        for line in "".join(relayed).split("\r\n")
        if " SMSG " in line and TRACEPARENT_TAG in line
    ]
    assert smsg_lines, f"expected an SMSG line carrying traceparent, got: {relayed!r}"
    line = smsg_lines[0]
    # Extract the traceparent value from the @-tag block.
    tag_block = line.split(" ", 1)[0][1:]
    tags = dict(t.split("=", 1) for t in tag_block.split(";") if "=" in t)
    tp_value = tags[TRACEPARENT_TAG]
    # W3C: 00-<trace-id>-<parent-id>-<flags>
    parts = tp_value.split("-")
    assert len(parts) == 4, f"malformed traceparent: {tp_value!r}"
    parent_id_hex = parts[2]
    # The wire parent-id MUST NOT equal the outer span's id — that would mean
    # we copied the inbound traceparent verbatim instead of re-signing.
    assert parent_id_hex != outer_span_id_hex, (
        f"wire parent-id {parent_id_hex!r} matches outer span "
        f"{outer_span_id_hex!r} — re-sign rule violated"
    )
