"""End-to-end SkillClient — drives each high-level IRC verb (the ones
not already exercised by ``tests/test_integration_layer5.py``) through
the full daemon ↔ Unix socket ↔ ``agentirc.IRCd`` chain. Plus whisper
dispatch and close-with-pending-requests lifecycle.

Phase 0a Task 8.5(a): replaces the integration-shaped portion of
``tests/test_skill_client.py`` (the unit test moves to cultureagent in
Phase 1; this file stays as the contract test for culture's daemon ↔
skill IPC surface).

**Out of scope:** ``compact`` / ``clear`` (need a real claude SDK turn —
Task 8.5(b) territory) and the module-level CLI helpers
(``_sock_path_from_env``, ``_parse_ask_timeout``, ``_cmd_*``,
``_main`` — separate ``tests/test_cli_*`` shape).
"""

import asyncio
import os

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon
from culture.clients.claude.skill.irc_client import SkillClient


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


async def _wait_for_daemon_joined(server, channel, nick, timeout=5.0):
    """Poll ``server.channels[channel].members`` until the daemon's nick
    appears. Server processes JOIN synchronously on receipt, so this
    is a deterministic readiness signal — same helper as Tasks 3–6."""
    async with asyncio.timeout(timeout):
        while True:
            ch = server.channels.get(channel)
            if ch is not None and any(getattr(m, "nick", None) == nick for m in ch.members):
                return
            await asyncio.sleep(0.05)


def _make_daemon_setup(server, tmp_path):
    """Build the standard test config + agent + sock dir. Returns
    ``(config, agent, sock_dir)``."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    return config, agent, sock_dir


async def _connect_skill(sock_dir):
    sock_path = os.path.join(str(sock_dir), "culture-testserv-bot.sock")
    skill = SkillClient(sock_path)
    await skill.connect()
    return skill


@pytest.mark.asyncio
async def test_irc_who_returns_ok(server, tmp_path, monkeypatch):
    """``irc_who`` forwards a WHO query through the daemon. The IPC
    response is ``ok=True`` once the WHO has been sent — actual numerics
    arrive via the buffer, not the IPC reply (see daemon.py:916-922)."""
    _redirect_pidfile(monkeypatch, tmp_path)
    config, agent, sock_dir = _make_daemon_setup(server, tmp_path)
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        await _wait_for_daemon_joined(server, "#general", agent.nick)
        skill = await _connect_skill(sock_dir)
        try:
            result = await skill.irc_who("#general")
            assert result["ok"]
        finally:
            await skill.close()
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_irc_topic_set_and_get(server, tmp_path, monkeypatch):
    """``irc_topic`` in set mode (``topic=<str>``) and get mode (``topic``
    absent) both return ``ok=True``. Daemon dispatches to
    ``transport.send_topic(channel, topic|None)`` — see daemon.py:924-933."""
    _redirect_pidfile(monkeypatch, tmp_path)
    config, agent, sock_dir = _make_daemon_setup(server, tmp_path)
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        await _wait_for_daemon_joined(server, "#general", agent.nick)
        skill = await _connect_skill(sock_dir)
        try:
            set_result = await skill.irc_topic("#general", "Phase 0a complete")
            assert set_result["ok"]
            get_result = await skill.irc_topic("#general")
            assert get_result["ok"]
        finally:
            await skill.close()
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_supervisor_whisper_routes_to_pending_whispers(server, tmp_path, monkeypatch):
    """A whisper sent through the daemon's socket_server is queued onto
    ``SkillClient.pending_whispers`` (via the WHISPER branch of
    ``_dispatch_message``) and surfaced via ``drain_whispers()``.
    Drives lines 105-106 + 130-132 of ``irc_client.py``."""
    _redirect_pidfile(monkeypatch, tmp_path)
    config, agent, sock_dir = _make_daemon_setup(server, tmp_path)
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        await _wait_for_daemon_joined(server, "#general", agent.nick)
        skill = await _connect_skill(sock_dir)
        try:
            # Drive the whisper from the daemon side using the same path
            # production supervisors use — see daemon.py:666-669.
            assert daemon._socket_server is not None
            await daemon._socket_server.send_whisper("Stop retrying", "CORRECTION")

            async with asyncio.timeout(5.0):
                while not skill.pending_whispers:
                    await asyncio.sleep(0.05)
            whispers = skill.drain_whispers()
            assert len(whispers) == 1
            assert whispers[0]["whisper_type"] == "CORRECTION"
            assert "Stop retrying" in whispers[0]["message"]
            assert skill.pending_whispers == []  # drained
        finally:
            await skill.close()
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_close_fails_pending_requests(server, tmp_path, monkeypatch):
    """``SkillClient.close()`` resolves any in-flight request future with
    a ``ConnectionError`` — drives the cleanup branches at
    ``irc_client.py:65-69`` (``"SkillClient closed"``) and
    ``irc_client.py:91-95`` (``"Connection lost"``). In practice the
    read-loop's ``finally`` (line 91-95) wins the race because
    ``close()`` cancels the read task first; either message is a valid
    pending-fail signal so the assertion accepts both.

    Setup: replace one IPC handler in the daemon's ``_ipc_dispatch``
    table with a non-responding stub; the skill's ``irc_send`` request
    parks in ``_pending`` indefinitely. ``close()`` must then resolve
    that pending future with the documented exception.
    """
    _redirect_pidfile(monkeypatch, tmp_path)
    config, agent, sock_dir = _make_daemon_setup(server, tmp_path)
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()

    # Test-scoped unblock signal — set in finally so the daemon's
    # SocketServer per-client handler task can unwind before
    # daemon.stop() runs. Without this, SocketServer.stop() doesn't
    # cancel in-flight _handle_client tasks (its scope is closing
    # writers + the server, not handler awaits), so a never-returning
    # handler would leak a pending task past test teardown.
    unblock = asyncio.Event()

    async def _never_responds(_req_id, _msg):
        await unblock.wait()
        # Returning None is fine — the skill's request future has
        # already been resolved with ConnectionError by close().

    daemon._ipc_dispatch["irc_send"] = _never_responds

    try:
        await _wait_for_daemon_joined(server, "#general", agent.nick)
        skill = await _connect_skill(sock_dir)

        send_task = asyncio.create_task(skill.irc_send("#general", "stuck"))
        # Wait until the request has been written and registered in
        # _pending. A bounded poll here replaces fixed sleeps.
        async with asyncio.timeout(5.0):
            while not skill._pending:
                await asyncio.sleep(0.01)

        await skill.close()

        with pytest.raises(ConnectionError, match="SkillClient closed|Connection lost"):
            await send_task
    finally:
        unblock.set()  # let the daemon's IPC handler return cleanly
        await daemon.stop()
