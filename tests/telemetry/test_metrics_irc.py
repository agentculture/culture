"""Tests for message-flow metrics: bytes_sent, bytes_received, message.size, privmsg.delivered."""

from __future__ import annotations

import asyncio

import pytest

from tests.telemetry._metrics_helpers import (
    get_counter_value,
    get_histogram_count,
)


@pytest.mark.asyncio
async def test_bytes_sent_counter_records_outgoing_to_client(metrics_reader, server, make_client):
    """Server-to-client write increments bytes_sent with direction=s2c."""
    client = await make_client(nick="testserv-alice", user="alice")
    await client.send("PING token")
    await client.recv_all(timeout=0.5)
    n = get_counter_value(metrics_reader, "culture.irc.bytes_sent", attrs={"direction": "s2c"})
    # NICK/USER welcome numerics + PONG response — guaranteed > 0.
    assert n > 0, f"expected s2c bytes_sent > 0, got {n}"


@pytest.mark.asyncio
async def test_bytes_received_counter_records_incoming_from_client(
    metrics_reader, server, make_client
):
    """Client-to-server read increments bytes_received with direction=c2s."""
    client = await make_client(nick="testserv-bob", user="bob")
    await client.send("PING token")
    await client.recv_all(timeout=0.5)
    n = get_counter_value(metrics_reader, "culture.irc.bytes_received", attrs={"direction": "c2s"})
    assert n > 0, f"expected c2s bytes_received > 0, got {n}"


@pytest.mark.asyncio
async def test_message_size_histogram_records_per_verb_per_direction(
    metrics_reader, server, make_client
):
    client = await make_client(nick="testserv-carol", user="carol")
    await client.send("PING ping1")
    await client.recv_all(timeout=0.5)
    n = get_histogram_count(
        metrics_reader,
        "culture.irc.message.size",
        attrs={"verb": "PING", "direction": "c2s"},
    )
    assert n >= 1, f"expected ≥1 PING c2s sample, got {n}"


@pytest.mark.asyncio
async def test_privmsg_delivered_channel(metrics_reader, server, make_client):
    """PRIVMSG to a channel increments delivered with kind=channel + channel label."""
    sender = await make_client(nick="testserv-dan", user="dan")
    listener = await make_client(nick="testserv-eve", user="eve")
    await sender.send("JOIN #pm-test")
    await sender.recv_all(timeout=0.5)
    await listener.send("JOIN #pm-test")
    await listener.recv_all(timeout=0.5)
    await sender.send("PRIVMSG #pm-test :hello")
    await sender.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)
    n = get_counter_value(
        metrics_reader,
        "culture.privmsg.delivered",
        attrs={"kind": "channel", "channel": "#pm-test"},
    )
    assert n >= 1, f"expected ≥1 channel delivery, got {n}"


@pytest.mark.asyncio
async def test_privmsg_delivered_dm(metrics_reader, server, make_client):
    """PRIVMSG to a nick increments delivered with kind=dm."""
    sender = await make_client(nick="testserv-frank", user="frank")
    # Receiver must exist so the dm path increments delivered; we don't read it.
    await make_client(nick="testserv-grace", user="grace")
    await sender.send("PRIVMSG testserv-grace :hello")
    await sender.recv_all(timeout=0.5)
    await asyncio.sleep(0.1)
    n = get_counter_value(metrics_reader, "culture.privmsg.delivered", attrs={"kind": "dm"})
    assert n >= 1, f"expected ≥1 dm delivery, got {n}"


@pytest.mark.asyncio
async def test_s2s_bytes_sent_recorded(metrics_reader, linked_servers):
    """S2S writes record bytes_sent with direction=s2s."""
    # The handshake itself (PASS + SERVER + bursts) generates s2s traffic.
    # By the time the linked_servers fixture yields, bytes_sent has fired.
    n = get_counter_value(metrics_reader, "culture.irc.bytes_sent", attrs={"direction": "s2s"})
    assert n > 0, f"expected s2s bytes_sent > 0 after handshake, got {n}"


@pytest.mark.asyncio
async def test_s2s_bytes_received_recorded(metrics_reader, linked_servers):
    """S2S reads record bytes_received with direction=s2s."""
    n = get_counter_value(metrics_reader, "culture.irc.bytes_received", attrs={"direction": "s2s"})
    assert n > 0, f"expected s2s bytes_received > 0 after handshake, got {n}"
