"""Tests for ServerLink._dispatch span and inbound traceparent mitigation.

Covers the four states defined in culture/protocol/extensions/tracing.md:
  - valid:     start child span linked to extracted context
  - missing:   start root span (origin still "remote" — federation always-remote)
  - malformed: drop tag, root span, dropped_reason="malformed"
  - too_long:  drop tag, root span, dropped_reason="too_long"
"""

from __future__ import annotations

import asyncio

import pytest
from opentelemetry import trace as otel_trace

from culture.protocol.message import Message
from culture.telemetry.context import TRACEPARENT_TAG

VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


async def _wait_for_span(exporter, name: str, timeout: float = 1.0) -> None:
    """Wait until at least one span with `name` appears in `exporter`,
    or `timeout` seconds elapse. SimpleSpanProcessor is synchronous, but
    the federation write travels through an event-loop boundary."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if any(s.name == name for s in exporter.get_finished_spans()):
            return
        await asyncio.sleep(0.02)
    # Fall through; caller's assertion will fail with helpful context.


def _spans_with_name(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


@pytest.mark.asyncio
async def test_dispatch_valid_traceparent_creates_child(tracing_exporter, linked_servers):
    """Inbound message with valid traceparent → s2s.<VERB> span is parented
    under the extracted remote context (shared trace_id)."""
    server_a, server_b = linked_servers
    tracing_exporter.clear()

    # Send a fabricated SMSG line with a valid traceparent tag from alpha→beta.
    link_alpha_to_beta = server_a.links["beta"]
    line = f"@{TRACEPARENT_TAG}={VALID_TP} " f":alpha SMSG #s2s-trace-test alpha-bob :hello"
    # Use the writer directly to inject from alpha's side without going
    # through send_raw (which would re-sign with alpha's session traceparent).
    link_alpha_to_beta.writer.write((line + "\r\n").encode("utf-8"))
    await link_alpha_to_beta.writer.drain()

    # Give server_b time to dispatch.
    await _wait_for_span(tracing_exporter, "irc.s2s.SMSG")

    # Find an s2s.SMSG span on server_b.
    smsg_spans = _spans_with_name(tracing_exporter, "irc.s2s.SMSG")
    assert smsg_spans, "no irc.s2s.SMSG span recorded on receiver"
    span = smsg_spans[-1]
    attrs = dict(span.attributes or {})
    assert attrs.get("irc.command") == "SMSG"
    assert attrs.get("culture.trace.origin") == "remote"
    assert attrs.get("culture.federation.peer") == "alpha"
    assert "culture.trace.dropped_reason" not in attrs
    # Child span shares trace-id from VALID_TP.
    assert format(span.context.trace_id, "032x") == "4bf92f3577b34da6a3ce929d0e0e4736"


@pytest.mark.asyncio
async def test_dispatch_missing_traceparent_root_span(tracing_exporter, linked_servers):
    server_a, server_b = linked_servers
    tracing_exporter.clear()

    link_alpha_to_beta = server_a.links["beta"]
    # Plain SMSG, no traceparent tag.
    line = ":alpha SMSG #s2s-trace-test alpha-bob :hi"
    link_alpha_to_beta.writer.write((line + "\r\n").encode("utf-8"))
    await link_alpha_to_beta.writer.drain()
    await _wait_for_span(tracing_exporter, "irc.s2s.SMSG")

    smsg_spans = _spans_with_name(tracing_exporter, "irc.s2s.SMSG")
    assert smsg_spans
    span = smsg_spans[-1]
    attrs = dict(span.attributes or {})
    assert attrs.get("culture.trace.origin") == "remote"
    assert attrs.get("culture.federation.peer") == "alpha"
    # No dropped_reason — tag was simply absent.
    assert "culture.trace.dropped_reason" not in attrs
    # Not parented to a remote trace (no traceparent on the wire).
    assert span.parent is None or not span.parent.is_remote


@pytest.mark.asyncio
async def test_dispatch_malformed_traceparent_dropped(tracing_exporter, linked_servers):
    server_a, server_b = linked_servers
    tracing_exporter.clear()

    link_alpha_to_beta = server_a.links["beta"]
    line = f"@{TRACEPARENT_TAG}=not-a-traceparent " ":alpha SMSG #s2s-trace-test alpha-bob :hi"
    link_alpha_to_beta.writer.write((line + "\r\n").encode("utf-8"))
    await link_alpha_to_beta.writer.drain()
    await _wait_for_span(tracing_exporter, "irc.s2s.SMSG")

    smsg_spans = _spans_with_name(tracing_exporter, "irc.s2s.SMSG")
    assert smsg_spans
    span = smsg_spans[-1]
    attrs = dict(span.attributes or {})
    assert attrs.get("culture.trace.dropped_reason") == "malformed"
    assert attrs.get("culture.federation.peer") == "alpha"
    # Tag was dropped — span is not parented to a remote trace.
    assert span.parent is None or not span.parent.is_remote


@pytest.mark.asyncio
async def test_dispatch_oversize_traceparent_dropped(tracing_exporter, linked_servers):
    server_a, server_b = linked_servers
    tracing_exporter.clear()

    link_alpha_to_beta = server_a.links["beta"]
    oversize = VALID_TP + "extrachars"
    line = f"@{TRACEPARENT_TAG}={oversize} " ":alpha SMSG #s2s-trace-test alpha-bob :hi"
    link_alpha_to_beta.writer.write((line + "\r\n").encode("utf-8"))
    await link_alpha_to_beta.writer.drain()
    await _wait_for_span(tracing_exporter, "irc.s2s.SMSG")

    smsg_spans = _spans_with_name(tracing_exporter, "irc.s2s.SMSG")
    assert smsg_spans
    span = smsg_spans[-1]
    attrs = dict(span.attributes or {})
    assert attrs.get("culture.trace.dropped_reason") == "too_long"
    assert attrs.get("culture.federation.peer") == "alpha"
    # Tag was dropped — span is not parented to a remote trace.
    assert span.parent is None or not span.parent.is_remote
