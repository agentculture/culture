"""Tests for client/session metrics: clients.connected, session.duration, command.duration."""

from __future__ import annotations

import asyncio

import pytest

from tests.telemetry._metrics_helpers import (
    get_histogram_count,
    get_up_down_value,
)


@pytest.mark.asyncio
async def test_clients_connected_increments_on_connect_and_decrements_on_disconnect(
    metrics_reader, server, make_client
):
    """clients_connected goes up on connect, returns to baseline on disconnect."""
    baseline = get_up_down_value(
        metrics_reader, "culture.clients.connected", attrs={"kind": "human"}
    )
    client = await make_client(nick="testserv-alice", user="alice")
    # During session, gauge is +1 from baseline.
    # InMemoryMetricReader collects the latest value at read time, so
    # we can check the live value.
    during = get_up_down_value(metrics_reader, "culture.clients.connected", attrs={"kind": "human"})
    assert during == baseline + 1, (
        f"expected baseline+1 connected during session, got " f"baseline={baseline} during={during}"
    )

    await client.close()
    # Allow handle()'s finally to run.
    for _ in range(50):
        post = get_up_down_value(
            metrics_reader, "culture.clients.connected", attrs={"kind": "human"}
        )
        if post == baseline:
            break
        await asyncio.sleep(0.02)
    post = get_up_down_value(metrics_reader, "culture.clients.connected", attrs={"kind": "human"})
    assert post == baseline, f"expected baseline after disconnect, got {post}"


@pytest.mark.asyncio
async def test_client_session_duration_recorded_on_disconnect(metrics_reader, server, make_client):
    """session_duration histogram gets a data point when the connection closes."""
    client = await make_client(nick="testserv-bob", user="bob")
    await client.close()
    # Allow handle()'s finally to run.
    for _ in range(50):
        n = get_histogram_count(
            metrics_reader,
            "culture.client.session.duration",
            attrs={"kind": "human"},
        )
        if n >= 1:
            break
        await asyncio.sleep(0.02)
    count = get_histogram_count(
        metrics_reader,
        "culture.client.session.duration",
        attrs={"kind": "human"},
    )
    assert count >= 1, f"expected ≥1 session duration sample, got {count}"


@pytest.mark.asyncio
async def test_client_command_duration_recorded_per_verb(metrics_reader, server, make_client):
    """command_duration histogram records per dispatched verb (uppercase)."""
    client = await make_client(nick="testserv-carol", user="carol")
    await client.send("PING token")
    await client.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)

    n = get_histogram_count(
        metrics_reader, "culture.client.command.duration", attrs={"verb": "PING"}
    )
    assert n >= 1, f"expected ≥1 PING command duration, got {n}"
