"""NT-3 — bridge has no SDK loop.

Phase 2 of the rearchitecture plan. The ``culture-bridge`` process is
a thin IRC + IPC + audit + daemon-log surface, with NO Claude Agent SDK
loop, no ``AgentRunner``, no supervisor, no autonomous LLM brain. CC
(the user-facing Claude Code session) is the boss; the bridge holds
the IRC nick and the audit/log surface on CC's behalf.

These tests assert that invariant at the structural level. They do not
require a running IRC server — they target the bridge's
``__init__`` + IPC dispatch directly.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from culture.clients.bridge.daemon import AgentDaemon
from culture.clients.bridge.ipc import decode_message, encode_message, make_request
from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
)


def _make_bridge_daemon(socket_dir: str) -> AgentDaemon:
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=6667),
    )
    agent = AgentConfig(nick="testserv-bridge", directory="/tmp", channels=[])
    return AgentDaemon(config, agent, socket_dir=socket_dir, skip_claude=True)


def test_bridge_has_no_agent_runner_attribute() -> None:
    """The bridge daemon must not carry an ``_agent_runner`` attribute.

    The old claude/daemon.py declared ``self._agent_runner = None`` in
    ``__init__`` and replaced it with an ``AgentRunner`` instance in
    ``_start_agent_runner``. The bridge daemon deletes that field
    entirely — nothing in the bridge codepath should reference it.
    """
    sock_dir = tempfile.mkdtemp()
    try:
        daemon = _make_bridge_daemon(sock_dir)
        assert not hasattr(
            daemon, "_agent_runner"
        ), "bridge daemon must not declare _agent_runner — it has no SDK loop"
    finally:
        # No async resources allocated; ctor only.
        pass


def test_bridge_has_no_supervisor_attribute() -> None:
    """The bridge daemon must not carry a ``_supervisor`` attribute.

    Workers still have Supervisor (they own a real SDK loop); the
    bridge drops it entirely (EL-1 split: supervisor is SDK-coupled).
    """
    sock_dir = tempfile.mkdtemp()
    daemon = _make_bridge_daemon(sock_dir)
    assert not hasattr(
        daemon, "_supervisor"
    ), "bridge daemon must not declare _supervisor — Workers keep Supervisor; bridge does not"


def test_bridge_has_no_circuit_open_attribute() -> None:
    """The bridge has no SDK loop to fail — no circuit-breaker state."""
    sock_dir = tempfile.mkdtemp()
    daemon = _make_bridge_daemon(sock_dir)
    assert not hasattr(daemon, "_circuit_open"), (
        "bridge daemon must not declare _circuit_open — circuit breaker is "
        "SDK-loop-specific and lives only in worker daemons"
    )


def test_bridge_has_no_poll_or_sleep_task_attributes() -> None:
    """The bridge has no SDK poll loop and no sleep scheduler."""
    sock_dir = tempfile.mkdtemp()
    daemon = _make_bridge_daemon(sock_dir)
    assert not hasattr(
        daemon, "_poll_task"
    ), "bridge daemon must not declare _poll_task — push-everywhere rule"
    assert not hasattr(
        daemon, "_sleep_task"
    ), "bridge daemon must not declare _sleep_task — bridge does not sleep/wake"


def test_bridge_has_no_context_watch_attribute() -> None:
    """``_context_watch`` is SDK-coupled (per-turn input_tokens watermark)
    and stays in the worker daemon."""
    sock_dir = tempfile.mkdtemp()
    daemon = _make_bridge_daemon(sock_dir)
    assert not hasattr(
        daemon, "_context_watch"
    ), "bridge daemon must not declare _context_watch — SDK-coupled"


def test_bridge_skip_claude_is_forced_true() -> None:
    """Even when constructed with ``skip_claude=False``, the bridge
    daemon forces it to True. The bridge never owns an SDK loop."""
    sock_dir = tempfile.mkdtemp()
    config = DaemonConfig(server=ServerConnConfig(host="127.0.0.1", port=6667))
    agent = AgentConfig(nick="testserv-bridge", directory="/tmp", channels=[])
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=False)
    assert (
        daemon.skip_claude is True
    ), "bridge daemon must coerce skip_claude=True regardless of constructor arg"


def test_bridge_ipc_dispatch_has_no_clear_pause_resume() -> None:
    """The dropped verbs (clear/pause/resume) are SDK-coupled and must
    not be in the bridge's dispatch table."""
    sock_dir = tempfile.mkdtemp()
    daemon = _make_bridge_daemon(sock_dir)
    for verb in ("clear", "pause", "resume"):
        assert (
            verb not in daemon._ipc_dispatch
        ), f"bridge daemon must not advertise SDK-coupled verb {verb!r}"


def test_bridge_ipc_dispatch_has_net_new_verbs() -> None:
    """The NET-NEW verbs land in this phase; verify presence."""
    sock_dir = tempfile.mkdtemp()
    daemon = _make_bridge_daemon(sock_dir)
    for verb in (
        "cc_session_start",
        "cc_session_end",
        "set_runtime_model",
        "sdk_event",
        "daemon_log_record",
        "inbound_dm_ack",
        "inbound_mention_ack",
        "inbound_roominvite_ack",
        "perm_decision_ack",
    ):
        assert verb in daemon._ipc_dispatch, f"bridge daemon missing NET-NEW IPC verb {verb!r}"


def test_bridge_ipc_dispatch_preserves_13_irc_verbs() -> None:
    """Preserved IRC/thread verbs survive byte-for-byte."""
    sock_dir = tempfile.mkdtemp()
    daemon = _make_bridge_daemon(sock_dir)
    for verb in (
        "irc_send",
        "irc_read",
        "irc_join",
        "irc_part",
        "irc_channels",
        "irc_who",
        "irc_topic",
        "irc_ask",
        "irc_thread_create",
        "irc_thread_reply",
        "irc_threads",
        "irc_thread_close",
        "irc_thread_read",
    ):
        assert verb in daemon._ipc_dispatch, f"bridge daemon missing preserved IRC verb {verb!r}"


@pytest.mark.asyncio
async def test_status_ipc_returns_cc_connected_false_by_default(server) -> None:
    """The ``status`` IPC verb returns ``cc_connected: False`` until a
    ``cc_session_start`` has fired; ``circuit_open`` MUST NOT appear."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bridge", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    try:
        await asyncio.sleep(0.3)
        sock_path = os.path.join(sock_dir, "culture-testserv-bridge.sock")
        reader, writer = await asyncio.open_unix_connection(sock_path)
        req = make_request("status")
        writer.write(encode_message(req))
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        resp = decode_message(data)
        assert resp["ok"] is True, resp
        body = resp["data"]
        assert body["cc_connected"] is False, body
        assert (
            "circuit_open" not in body
        ), "status IPC must drop circuit_open — that field is SDK-loop-specific"
        assert body["activity"] == "awaiting_cc", body
        writer.close()
        await writer.wait_closed()
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_cc_session_start_flips_cc_connected_flag(server) -> None:
    """A ``cc_session_start`` IPC call must flip ``cc_connected`` to True."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
    )
    agent = AgentConfig(nick="testserv-bridge", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    try:
        await asyncio.sleep(0.3)
        sock_path = os.path.join(sock_dir, "culture-testserv-bridge.sock")
        reader, writer = await asyncio.open_unix_connection(sock_path)
        # Fire cc_session_start.
        writer.write(encode_message(make_request("cc_session_start", nick="testserv-bridge")))
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        ack = decode_message(data)
        assert ack["ok"] is True, ack
        # Now status should report cc_connected=True.
        writer.write(encode_message(make_request("status")))
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), timeout=2.0)
        resp = decode_message(data)
        assert resp["ok"] is True
        assert resp["data"]["cc_connected"] is True
        assert resp["data"]["activity"] == "connected"
        writer.close()
        await writer.wait_closed()
    finally:
        await daemon.stop()


def test_bridge_module_imports_without_sdk_dependency() -> None:
    """Importing the bridge daemon module must not pull in the SDK
    runner. The claude-agent-sdk dependency is only required by
    ``culture.clients.claude.agent_runner`` and by workers; the bridge
    must boot on a host where the SDK isn't installed.

    This test stops short of mocking sys.modules — it just imports the
    bridge daemon module and confirms ``AgentRunner`` is not in scope.
    """
    import culture.clients.bridge.daemon as bridge_daemon_mod

    assert not hasattr(
        bridge_daemon_mod, "AgentRunner"
    ), "bridge.daemon must not import AgentRunner"
    assert not hasattr(bridge_daemon_mod, "Supervisor"), "bridge.daemon must not import Supervisor"
