"""Tests for the irc.s2s.session span on ServerLink.handle.

Covers direction attribute (set at span start) and peer attribute (set lazily
in _try_complete_handshake once peer_name is known).
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_session_span_records_direction_and_peer_for_both_sides(
    tracing_exporter,
    linked_servers,
):
    """Both ends of a federation link record an irc.s2s.session span with
    the right s2s.direction and s2s.peer attributes after teardown."""
    server_a, server_b = linked_servers
    tracing_exporter.clear()  # discard handshake spans recorded by the fixture

    # Tear down the link from server_a's side; both sides' session spans end.
    link_to_b = server_a.links.get("beta")
    assert link_to_b is not None, "linked_servers fixture failed to establish A->B link"
    link_to_b.writer.close()
    try:
        await link_to_b.writer.wait_closed()
    except ConnectionError:
        pass
    for _ in range(50):
        if "beta" not in server_a.links and "alpha" not in server_b.links:
            break
        await asyncio.sleep(0.05)
    # Allow span flush.
    await asyncio.sleep(0.1)

    spans = tracing_exporter.get_finished_spans()
    session_spans = [s for s in spans if s.name == "irc.s2s.session"]
    assert len(session_spans) >= 2, (
        f"expected at least 2 session spans (one per side), got "
        f"{len(session_spans)}: {[s.name for s in spans]}"
    )

    # Bucket by direction; expect one inbound and one outbound.
    by_direction: dict[str, list] = {"inbound": [], "outbound": []}
    for span in session_spans:
        attrs = dict(span.attributes or {})
        direction = attrs.get("s2s.direction")
        if direction in by_direction:
            by_direction[direction].append(attrs)

    assert by_direction["outbound"], "no outbound session span recorded"
    assert by_direction["inbound"], "no inbound session span recorded"

    # The outbound side (server_a -> beta) carries s2s.peer = "beta".
    outbound_peers = {a.get("s2s.peer") for a in by_direction["outbound"]}
    assert (
        "beta" in outbound_peers
    ), f"expected s2s.peer=beta on outbound side, got {outbound_peers}"
    # The inbound side (server_b accepted from alpha) carries s2s.peer = "alpha".
    inbound_peers = {a.get("s2s.peer") for a in by_direction["inbound"]}
    assert "alpha" in inbound_peers, f"expected s2s.peer=alpha on inbound side, got {inbound_peers}"
