"""Tests for federation metrics: s2s.messages, relay_latency, links_active, link_events."""

from __future__ import annotations

import asyncio

import pytest

from culture.agentirc.config import LinkConfig, ServerConfig
from culture.agentirc.ircd import IRCd
from tests.conftest import TEST_LINK_PASSWORD
from tests.telemetry._metrics_helpers import (
    get_counter_value,
    get_histogram_count,
    get_up_down_value,
)


@pytest.mark.asyncio
async def test_s2s_messages_counter_inbound(metrics_reader, linked_servers):
    """Inbound S2S verbs (handshake + burst) increment s2s_messages."""
    # By the time linked_servers yields, PASS, SERVER, and burst messages
    # have all flowed inbound on each side.
    n_pass = get_counter_value(
        metrics_reader,
        "culture.s2s.messages",
        attrs={"verb": "PASS", "direction": "inbound"},
    )
    n_server = get_counter_value(
        metrics_reader,
        "culture.s2s.messages",
        attrs={"verb": "SERVER", "direction": "inbound"},
    )
    assert n_pass >= 1, f"expected ≥1 PASS, got {n_pass}"
    assert n_server >= 1, f"expected ≥1 SERVER, got {n_server}"


@pytest.mark.asyncio
async def test_s2s_links_active_increments_after_handshake(metrics_reader, linked_servers):
    """links_active = +1 on each side once handshake completes."""
    n_outbound = get_up_down_value(
        metrics_reader,
        "culture.s2s.links_active",
        attrs={"peer": "beta", "direction": "outbound"},
    )
    n_inbound = get_up_down_value(
        metrics_reader,
        "culture.s2s.links_active",
        attrs={"peer": "alpha", "direction": "inbound"},
    )
    assert n_outbound == 1, f"expected 1 outbound link, got {n_outbound}"
    assert n_inbound == 1, f"expected 1 inbound link, got {n_inbound}"


@pytest.mark.asyncio
async def test_s2s_link_events_connect(metrics_reader, linked_servers):
    """connect event fires once per side on handshake completion."""
    n_outbound = get_counter_value(
        metrics_reader,
        "culture.s2s.link_events",
        attrs={"peer": "beta", "event": "connect"},
    )
    n_inbound = get_counter_value(
        metrics_reader,
        "culture.s2s.link_events",
        attrs={"peer": "alpha", "event": "connect"},
    )
    assert n_outbound >= 1, f"expected ≥1 outbound connect, got {n_outbound}"
    assert n_inbound >= 1, f"expected ≥1 inbound connect, got {n_inbound}"


@pytest.mark.asyncio
async def test_s2s_relay_latency_histogram(metrics_reader, linked_servers, make_client_a):
    """relay_event records to s2s_relay_latency when an event relays."""
    _ = linked_servers  # required for fixture setup
    client_a = await make_client_a(nick="alpha-alice", user="alice")
    await client_a.send("JOIN #relay-test")
    await client_a.recv_all(timeout=0.5)
    await asyncio.sleep(0.3)
    # JOIN events relay to peer; relay_event fires.
    n = get_histogram_count(
        metrics_reader,
        "culture.s2s.relay_latency",
        attrs={"peer": "beta"},
    )
    assert n >= 1, f"expected ≥1 relay latency sample, got {n}"


@pytest.mark.asyncio
async def test_s2s_link_events_disconnect(metrics_reader, linked_servers):
    """disconnect event fires when the link tears down."""
    server_a, _ = linked_servers
    link = server_a.links["beta"]
    await link.send_raw("SQUIT alpha :test shutdown")
    await asyncio.sleep(0.5)
    # Allow finally block to run on both sides.
    n = get_counter_value(
        metrics_reader,
        "culture.s2s.link_events",
        attrs={"peer": "beta", "event": "disconnect"},
    )
    assert n >= 1, f"expected ≥1 disconnect, got {n}"


@pytest.mark.asyncio
async def test_s2s_link_events_auth_fail():
    """Bad password fires auth_fail."""
    # We need a manually-built tracing setup since this test doesn't use
    # linked_servers. Use a fresh metrics_reader fixture pattern inline.
    from opentelemetry import metrics as otel_metrics
    from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader
    from opentelemetry.sdk.resources import Resource

    from culture.telemetry.metrics import reset_for_tests as _reset_metrics

    _reset_metrics()
    reader = InMemoryMetricReader()
    provider = SdkMeterProvider(
        resource=Resource.create({"service.name": "test"}),
        metric_readers=[reader],
    )
    otel_metrics.set_meter_provider(provider)
    try:
        config_a = ServerConfig(name="alpha", host="127.0.0.1", port=0)
        config_b = ServerConfig(
            name="beta",
            host="127.0.0.1",
            port=0,
            links=[
                LinkConfig(
                    name="alpha",
                    host="127.0.0.1",
                    port=0,
                    password="correct",  # nosec B106 - test fixture, intentional bad-password assertion
                )
            ],
        )

        server_a = IRCd(config_a)
        server_b = IRCd(config_b)
        await server_a.start()
        await server_b.start()

        server_a.config.port = server_a._server.sockets[0].getsockname()[1]
        server_b.config.port = server_b._server.sockets[0].getsockname()[1]

        try:
            await server_a.connect_to_peer("127.0.0.1", server_b.config.port, "wrong")
            await asyncio.sleep(0.5)
            n = get_counter_value(
                reader,
                "culture.s2s.link_events",
                attrs={"peer": "alpha", "event": "auth_fail"},
            )
            assert n >= 1, f"expected ≥1 auth_fail on bad password, got {n}"
        finally:
            await server_a.stop()
            await server_b.stop()
    finally:
        _reset_metrics()
