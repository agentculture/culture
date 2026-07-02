"""Tests for culture.events.emitted + culture.events.render.duration."""

from __future__ import annotations

import asyncio

import pytest

from tests.telemetry._metrics_helpers import get_counter_value, get_histogram_count


@pytest.mark.asyncio
async def test_events_emitted_counter_increments_per_event(metrics_reader, server, make_client):
    """Each event through emit_event increments events_emitted with correct labels."""
    client = await make_client(nick="testserv-eve", user="eve")
    # JOIN emits EventType.JOIN; PART emits EventType.PART.
    await client.send("JOIN #events-test")
    await client.recv_all(timeout=0.5)
    await client.send("PART #events-test")
    await client.recv_all(timeout=0.5)
    # Allow event-loop drain.
    await asyncio.sleep(0.1)

    join_count = get_counter_value(
        metrics_reader,
        "culture.events.emitted",
        attrs={"event.type": "user.join", "origin": "local"},
    )
    part_count = get_counter_value(
        metrics_reader,
        "culture.events.emitted",
        attrs={"event.type": "user.part", "origin": "local"},
    )
    assert join_count >= 1, f"expected ≥1 join event, got {join_count}"
    assert part_count >= 1, f"expected ≥1 part event, got {part_count}"


@pytest.mark.asyncio
async def test_events_render_duration_histogram_records_per_event(
    metrics_reader, server, make_client
):
    """Each event records into events_render_duration with the right type label."""
    client = await make_client(nick="testserv-frank", user="frank")
    await client.send("JOIN #render-test")
    await client.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    n = get_histogram_count(
        metrics_reader,
        "culture.events.render.duration",
        attrs={"event.type": "user.join"},
    )
    assert n >= 1, f"expected ≥1 join render datapoint, got {n}"


@pytest.mark.asyncio
async def test_events_emitted_origin_federated_for_inbound_sevent(metrics_reader, linked_servers):
    """A federated event arriving via SEVENT increments events_emitted with origin=federated on the receiver."""
    server_a, _ = linked_servers
    link_alpha_to_beta = server_a.links["beta"]
    # Compose a minimal SEVENT line for a 'message' event.
    line = b":alpha SEVENT alpha 1 message * :eyJ0ZXh0IjoiaGkifQ==\r\n"
    link_alpha_to_beta.writer.write(line)
    await link_alpha_to_beta.writer.drain()

    # Poll for the federated message event on beta.
    for _ in range(50):
        n = get_counter_value(
            metrics_reader,
            "culture.events.emitted",
            attrs={"event.type": "message", "origin": "federated"},
        )
        if n >= 1:
            break
        await asyncio.sleep(0.02)
    count = get_counter_value(
        metrics_reader,
        "culture.events.emitted",
        attrs={"event.type": "message", "origin": "federated"},
    )
    assert count >= 1, f"expected ≥1 federated message event, got {count}"
