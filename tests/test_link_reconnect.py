# tests/test_link_reconnect.py
"""S2S link auto-reconnect tests."""

import asyncio

import pytest

from culture.server.config import LinkConfig, ServerConfig
from culture.server.ircd import IRCd
from tests.conftest import TEST_LINK_PASSWORD


@pytest.mark.asyncio
async def test_link_drop_triggers_retry():
    """When a linked peer drops (non-SQUIT), the server schedules retry."""
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)

    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    # Update link configs with actual ports
    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    # Link the servers
    await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
    for _ in range(50):
        if "beta" in server_a.links and "alpha" in server_b.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a.links

    # Kill server B abruptly (non-SQUIT drop)
    await server_b.stop()

    # Wait for alpha to detect the link drop and schedule retry
    for _ in range(50):
        if "beta" in server_a._link_retry_state:
            break
        await asyncio.sleep(0.05)

    assert "beta" in server_a._link_retry_state
    assert server_a._link_retry_state["beta"]["task"] is not None

    # Cleanup
    await server_a.stop()


@pytest.mark.asyncio
async def test_squit_does_not_trigger_retry():
    """When a peer sends SQUIT, no retry should be scheduled."""
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)

    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
    for _ in range(50):
        if "beta" in server_a.links and "alpha" in server_b.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a.links

    # Have server B send SQUIT to server A (graceful delink)
    link_to_alpha = server_b.links["alpha"]
    await link_to_alpha.send_raw("SQUIT beta :Shutting down gracefully")

    # Wait for alpha to process the SQUIT and remove the link
    for _ in range(50):
        if "beta" not in server_a.links:
            break
        await asyncio.sleep(0.05)

    assert "beta" not in server_a.links
    # SQUIT should NOT trigger retry
    assert "beta" not in server_a._link_retry_state

    # Cleanup
    await server_a.stop()
    await server_b.stop()


@pytest.mark.asyncio
async def test_incoming_connection_cancels_retry():
    """When a peer reconnects inbound while retry is pending, retry is cancelled."""
    link_password = TEST_LINK_PASSWORD

    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=0, password=link_password)],
    )
    config_b = ServerConfig(
        name="beta",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="alpha", host="127.0.0.1", port=0, password=link_password)],
    )

    server_a = IRCd(config_a)
    server_b = IRCd(config_b)

    await server_a.start()
    await server_b.start()

    server_a.config.port = server_a._server.sockets[0].getsockname()[1]
    server_b.config.port = server_b._server.sockets[0].getsockname()[1]

    config_a.links[0].port = server_b.config.port
    config_b.links[0].port = server_a.config.port

    # Link the servers (A -> B)
    await server_a.connect_to_peer("127.0.0.1", server_b.config.port, link_password)
    for _ in range(50):
        if "beta" in server_a.links and "alpha" in server_b.links:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a.links

    # Kill server B to trigger retry on A
    await server_b.stop()

    # Wait for retry to be scheduled on A
    for _ in range(50):
        if "beta" in server_a._link_retry_state:
            break
        await asyncio.sleep(0.05)
    assert "beta" in server_a._link_retry_state

    # Restart server B and have it connect inbound to A
    server_b2 = IRCd(config_b)
    await server_b2.start()
    server_b2.config.port = server_b2._server.sockets[0].getsockname()[1]
    # Update A's link config so retry would point at the new B port
    config_a.links[0].port = server_b2.config.port

    # B2 connects to A (inbound connection to A)
    await server_b2.connect_to_peer("127.0.0.1", server_a.config.port, link_password)
    for _ in range(50):
        if "beta" in server_a.links:
            break
        await asyncio.sleep(0.05)

    assert "beta" in server_a.links
    # Retry state should be cleared by the incoming connection
    assert "beta" not in server_a._link_retry_state

    # Cleanup
    await server_a.stop()
    await server_b2.stop()


@pytest.mark.asyncio
async def test_reconnect_after_initial_failure():
    """If initial connect_to_peer fails, server schedules retry."""
    link_password = TEST_LINK_PASSWORD
    config_a = ServerConfig(
        name="alpha",
        host="127.0.0.1",
        port=0,
        links=[LinkConfig(name="beta", host="127.0.0.1", port=16999, password=link_password)],
    )

    server_a = IRCd(config_a)
    await server_a.start()
    server_a.config.port = server_a._server.sockets[0].getsockname()[1]

    # Simulate initial connection failure (port 16999 has nothing listening)
    for lc in config_a.links:
        try:
            await server_a.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
        except Exception:
            server_a.maybe_retry_link(lc.name)

    # Retry should be scheduled
    assert "beta" in server_a._link_retry_state

    # Clean up
    server_a._link_retry_state["beta"]["task"].cancel()
    await server_a.stop()
