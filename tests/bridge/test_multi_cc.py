"""NT-16 — two concurrent CC sessions can run side-by-side with distinct
project-named bridge nicks.

The 2026-06-03 mesh re-architecture moves identity from a single
``local-boss`` per host to per-project bridge nicks resolved via
``resolve_project_nick`` (see ``cc_plugin/_nick_resolver.py``). Two
CC sessions in different cwds must:

    1. Resolve to DIFFERENT nicks via the resolver,
    2. Each bind to its own bridge IPC socket (distinct paths),
    3. Both successfully connect to the same IRCd and appear under
       their distinct nicks in ``server.clients``.

This test boots two real bridge daemons in-process against the
``server`` conftest fixture and confirms all three properties end-to-
end. If the future CC environment ever prevents two bridges in one
pytest run, the resolver-only fall-back at the bottom (Test 4)
guarantees the nick-resolution contract still holds.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from culture.clients.bridge.daemon import AgentDaemon
from culture.clients.claude.cc_plugin._nick_resolver import resolve_project_nick
from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
)


# Use realistic project-style nicks the CC plugin would resolve to.
_BRIDGE_A = "testserv-fork-rearch"
_BRIDGE_B = "testserv-payment-debug"


def _make_bridge_config(server) -> tuple[DaemonConfig, str]:
    """Build a fresh DaemonConfig pointed at the test IRCd plus a private
    socket_dir so two bridges can't collide on the same Unix socket path."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    sock_dir = tempfile.mkdtemp()
    return config, sock_dir


@pytest.mark.asyncio
async def test_two_bridges_with_distinct_nicks_connect(server) -> None:
    """Boot bridge A and bridge B in one event loop; both register
    successfully under their distinct nicks."""
    config_a, sock_a = _make_bridge_config(server)
    config_b, sock_b = _make_bridge_config(server)

    agent_a = AgentConfig(nick=_BRIDGE_A, directory="/tmp/a", channels=[])
    agent_b = AgentConfig(nick=_BRIDGE_B, directory="/tmp/b", channels=[])

    bridge_a = AgentDaemon(config_a, agent_a, socket_dir=sock_a)
    bridge_b = AgentDaemon(config_b, agent_b, socket_dir=sock_b)

    await bridge_a.start()
    try:
        await bridge_b.start()
        try:
            # Allow both NICK/USER handshakes to land.
            await asyncio.sleep(0.6)
            assert _BRIDGE_A in server.clients, (
                f"bridge A nick {_BRIDGE_A!r} missing from server.clients: "
                f"{sorted(server.clients)}"
            )
            assert _BRIDGE_B in server.clients, (
                f"bridge B nick {_BRIDGE_B!r} missing from server.clients: "
                f"{sorted(server.clients)}"
            )
            # Distinct nicks → distinct entries.
            assert server.clients[_BRIDGE_A] is not server.clients[_BRIDGE_B]
        finally:
            await bridge_b.stop()
    finally:
        await bridge_a.stop()


@pytest.mark.asyncio
async def test_two_bridges_have_distinct_socket_paths(server) -> None:
    """The IPC socket path is keyed by nick — two bridges must NOT share
    a socket path or one would silently overwrite the other's IPC."""
    config_a, sock_a = _make_bridge_config(server)
    config_b, sock_b = _make_bridge_config(server)

    agent_a = AgentConfig(nick=_BRIDGE_A, directory="/tmp/a", channels=[])
    agent_b = AgentConfig(nick=_BRIDGE_B, directory="/tmp/b", channels=[])

    bridge_a = AgentDaemon(config_a, agent_a, socket_dir=sock_a)
    bridge_b = AgentDaemon(config_b, agent_b, socket_dir=sock_b)

    assert bridge_a._socket_path != bridge_b._socket_path
    # And the nick must appear in the path so an operator can locate it.
    assert _BRIDGE_A in bridge_a._socket_path
    assert _BRIDGE_B in bridge_b._socket_path


@pytest.mark.asyncio
async def test_two_bridges_create_distinct_socket_files(server) -> None:
    """After ``start()`` both bridges must own a real socket file at the
    declared path — concurrent operation, no collision."""
    config_a, sock_a = _make_bridge_config(server)
    config_b, sock_b = _make_bridge_config(server)

    agent_a = AgentConfig(nick=_BRIDGE_A, directory="/tmp/a", channels=[])
    agent_b = AgentConfig(nick=_BRIDGE_B, directory="/tmp/b", channels=[])

    bridge_a = AgentDaemon(config_a, agent_a, socket_dir=sock_a)
    bridge_b = AgentDaemon(config_b, agent_b, socket_dir=sock_b)

    await bridge_a.start()
    try:
        await bridge_b.start()
        try:
            await asyncio.sleep(0.3)
            assert os.path.exists(bridge_a._socket_path)
            assert os.path.exists(bridge_b._socket_path)
        finally:
            await bridge_b.stop()
    finally:
        await bridge_a.stop()


def test_nick_resolver_returns_distinct_nicks_for_distinct_cwds(
    tmp_path, monkeypatch
) -> None:
    """Resolver-level fall-back contract: two cwds with distinct project
    names MUST yield distinct nicks. Without this, the multi-CC story
    collapses to "first session wins, second one overwrites".

    Kept independent of the bridge so that even if the in-process
    two-daemon scenarios above are ever sandboxed away, the
    foundational nick-resolution contract is still test-covered.
    """
    monkeypatch.delenv("CULTURE_BOSS_NICK", raising=False)
    cwd_a = tmp_path / "fork-rearch"
    cwd_b = tmp_path / "payment-debug"
    cwd_a.mkdir()
    cwd_b.mkdir()

    nick_a = resolve_project_nick(str(cwd_a))
    nick_b = resolve_project_nick(str(cwd_b))

    assert nick_a == "fork-rearch"
    assert nick_b == "payment-debug"
    assert nick_a != nick_b
