"""Tests for culture.trace.inbound counter — closes Plan 2's deferral.

Verifies the counter increments with the right result + peer labels for both
Client._dispatch (peer="") and ServerLink._dispatch (peer=<peer_name>) across
all four extract.status values: missing, valid, malformed, too_long.
"""

from __future__ import annotations

import asyncio

import pytest

from culture_core.telemetry.context import TRACEPARENT_TAG
from tests.telemetry._metrics_helpers import get_counter_value

VALID_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


# --- Client-side dispatch (peer="") ----------------------------------------


@pytest.mark.asyncio
async def test_trace_inbound_missing_on_client_dispatch(metrics_reader, server, make_client):
    """Plain message with no traceparent tag → result=missing."""
    client = await make_client(nick="testserv-alice", user="alice")
    await client.send("PING token1")
    await client.recv_all(timeout=0.5)
    # PING and prior NICK/USER all produce 'missing' inbound.
    count = get_counter_value(
        metrics_reader, "culture.trace.inbound", attrs={"result": "missing", "peer": ""}
    )
    assert count >= 1, f"expected ≥1 missing, got {count}"


@pytest.mark.asyncio
async def test_trace_inbound_valid_on_client_dispatch(metrics_reader, server, make_client):
    """Message carrying a valid traceparent tag → result=valid."""
    client = await make_client(nick="testserv-bob", user="bob")
    # Send a tagged PING via raw write so we control the wire bytes.
    await client.send(f"@{TRACEPARENT_TAG}={VALID_TP} PING token2")
    await client.recv_all(timeout=0.5)
    count = get_counter_value(
        metrics_reader, "culture.trace.inbound", attrs={"result": "valid", "peer": ""}
    )
    assert count >= 1, f"expected ≥1 valid, got {count}"


@pytest.mark.asyncio
async def test_trace_inbound_malformed_on_client_dispatch(metrics_reader, server, make_client):
    client = await make_client(nick="testserv-carol", user="carol")
    await client.send(f"@{TRACEPARENT_TAG}=not-a-traceparent PING token3")
    await client.recv_all(timeout=0.5)
    count = get_counter_value(
        metrics_reader,
        "culture.trace.inbound",
        attrs={"result": "malformed", "peer": ""},
    )
    assert count >= 1, f"expected ≥1 malformed, got {count}"


@pytest.mark.asyncio
async def test_trace_inbound_too_long_on_client_dispatch(metrics_reader, server, make_client):
    oversize = VALID_TP + "extrachars"
    client = await make_client(nick="testserv-dan", user="dan")
    await client.send(f"@{TRACEPARENT_TAG}={oversize} PING token4")
    await client.recv_all(timeout=0.5)
    count = get_counter_value(
        metrics_reader,
        "culture.trace.inbound",
        attrs={"result": "too_long", "peer": ""},
    )
    assert count >= 1, f"expected ≥1 too_long, got {count}"


# --- Server-side dispatch (peer=<peer_name>) -------------------------------


@pytest.mark.asyncio
async def test_trace_inbound_missing_on_s2s_dispatch(metrics_reader, linked_servers):
    """Plain S2S SMSG with no traceparent → result=missing, peer=alpha (on beta)."""
    server_a, _ = linked_servers
    link_alpha_to_beta = server_a.links["beta"]
    link_alpha_to_beta.writer.write(b":alpha SMSG #trace-test alpha-bob :hello\r\n")
    await link_alpha_to_beta.writer.drain()
    # Wait briefly for dispatch.

    for _ in range(50):
        n = get_counter_value(
            metrics_reader,
            "culture.trace.inbound",
            attrs={"result": "missing", "peer": "alpha"},
        )
        if n >= 1:
            break
        await asyncio.sleep(0.02)
    count = get_counter_value(
        metrics_reader,
        "culture.trace.inbound",
        attrs={"result": "missing", "peer": "alpha"},
    )
    assert count >= 1, f"expected ≥1 missing on s2s, got {count}"


@pytest.mark.asyncio
async def test_trace_inbound_valid_on_s2s_dispatch(metrics_reader, linked_servers):
    server_a, _ = linked_servers
    link_alpha_to_beta = server_a.links["beta"]
    line = f"@{TRACEPARENT_TAG}={VALID_TP} :alpha SMSG #trace-test alpha-bob :ping\r\n"
    link_alpha_to_beta.writer.write(line.encode("utf-8"))
    await link_alpha_to_beta.writer.drain()

    for _ in range(50):
        n = get_counter_value(
            metrics_reader,
            "culture.trace.inbound",
            attrs={"result": "valid", "peer": "alpha"},
        )
        if n >= 1:
            break
        await asyncio.sleep(0.02)
    count = get_counter_value(
        metrics_reader,
        "culture.trace.inbound",
        attrs={"result": "valid", "peer": "alpha"},
    )
    assert count >= 1, f"expected ≥1 valid on s2s, got {count}"
