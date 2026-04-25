"""End-to-end trace propagation across two federated servers.

The load-bearing validation of Plan 2: a single trace_id spans every
hop of a federated message, with each server contributing its own
spans tied to that trace_id."""

from __future__ import annotations

import asyncio

import pytest


async def _wait_for_span(exporter, name: str, timeout: float = 1.5) -> None:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if any(s.name == name for s in exporter.get_finished_spans()):
            return
        await asyncio.sleep(0.02)


def _spans_with_name(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


@pytest.mark.asyncio
async def test_e2e_one_trace_id_across_two_servers(
    tracing_exporter, linked_servers, make_client_a, make_client_b
):
    """Client on alpha sends PRIVMSG to channel beta-bob is in.
    Confirm alpha-side and beta-side spans share the same trace_id."""
    _, _ = linked_servers
    tracing_exporter.clear()

    client_a = await make_client_a(nick="alpha-alice", user="alice")
    client_b = await make_client_b(nick="beta-bob", user="bob")

    # Both join a shared channel.
    await client_a.send("JOIN #e2e")
    await client_a.recv_all(timeout=0.5)
    await client_b.send("JOIN #e2e")
    await client_b.recv_all(timeout=0.5)
    # Allow membership to propagate.
    await asyncio.sleep(0.3)

    tracing_exporter.clear()  # discard JOIN spans, focus on PRIVMSG flow

    await client_a.send("PRIVMSG #e2e :hello-from-alpha")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)

    # Wait for spans to flush.
    await _wait_for_span(tracing_exporter, "irc.s2s.SMSG")

    spans = tracing_exporter.get_finished_spans()
    # Bucket by name.
    by_name: dict[str, list] = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)

    # We should have at least one of each on the relevant chain:
    #   alpha side: irc.command.PRIVMSG (from client) + irc.s2s.relay
    #   beta side:  irc.s2s.SMSG (received) + downstream irc.event.emit
    assert (
        "irc.command.PRIVMSG" in by_name
    ), f"missing alpha-side client dispatch span; got names: {sorted(by_name.keys())}"
    assert (
        "irc.s2s.relay" in by_name
    ), f"missing alpha-side relay span; got names: {sorted(by_name.keys())}"
    assert (
        "irc.s2s.SMSG" in by_name
    ), f"missing beta-side dispatch span; got names: {sorted(by_name.keys())}"

    # All spans on this chain must share one trace_id. Find the originating
    # client PRIVMSG span; its trace_id is the shared id.
    privmsg_spans = by_name["irc.command.PRIVMSG"]
    relay_spans = by_name["irc.s2s.relay"]
    smsg_spans = by_name["irc.s2s.SMSG"]

    # Filter the relay/SMSG spans to those that share trace_id with at
    # least one PRIVMSG span (defensive: there could be unrelated spans
    # in the buffer from a prior background event).
    privmsg_trace_ids = {s.context.trace_id for s in privmsg_spans}

    matching_relay = [s for s in relay_spans if s.context.trace_id in privmsg_trace_ids]
    matching_smsg = [s for s in smsg_spans if s.context.trace_id in privmsg_trace_ids]

    assert matching_relay, (
        f"no irc.s2s.relay span shares trace_id with irc.command.PRIVMSG. "
        f"PRIVMSG trace_ids: {[format(t, '032x') for t in privmsg_trace_ids]}; "
        f"relay trace_ids: {[format(s.context.trace_id, '032x') for s in relay_spans]}"
    )
    assert matching_smsg, (
        f"no irc.s2s.SMSG span shares trace_id with irc.command.PRIVMSG. "
        f"PRIVMSG trace_ids: {[format(t, '032x') for t in privmsg_trace_ids]}; "
        f"SMSG trace_ids: {[format(s.context.trace_id, '032x') for s in smsg_spans]}"
    )


@pytest.mark.asyncio
async def test_relay_no_active_span_no_inject(linked_servers, make_client_a, make_client_b):
    """When telemetry is disabled (no exporter installed → no recording
    spans), federation outbound bytes carry no traceparent tag.

    Note: this test deliberately does NOT use the tracing_exporter
    fixture, so spans are non-recording (current_traceparent returns
    None) and send_raw injects nothing.
    """
    from culture.telemetry.context import TRACEPARENT_TAG

    server_a, _ = linked_servers

    # Capture wire bytes from alpha to beta.
    link_to_b = server_a.links["beta"]
    captured: list[bytes] = []
    real_write = link_to_b.writer.write

    def recording_write(data):
        captured.append(data)
        return real_write(data)

    link_to_b.writer.write = recording_write
    try:
        client_a = await make_client_a(nick="alpha-alice", user="alice")
        client_b = await make_client_b(nick="beta-bob", user="bob")
        await client_a.send("JOIN #no-trace")
        await client_a.recv_all(timeout=0.5)
        await client_b.send("JOIN #no-trace")
        await client_b.recv_all(timeout=0.5)
        await asyncio.sleep(0.3)

        await client_a.send("PRIVMSG #no-trace :hi")
        await client_a.recv_all(timeout=0.5)
        await asyncio.sleep(0.3)
    finally:
        link_to_b.writer.write = real_write

    wire = b"".join(captured).decode("utf-8", errors="replace")
    assert (
        TRACEPARENT_TAG not in wire
    ), f"unexpected traceparent on wire when telemetry disabled: {wire!r}"
