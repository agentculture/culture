"""End-to-end message buffer behavior — flood a real channel past the
buffer's max_per_channel cap and verify the ring-buffer eviction policy.

Replaces tests/test_message_buffer.py at the integration layer; the unit
test moves to cultureagent in Phase 1.
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

BUFFER_SIZE = 10
FLOOD_COUNT = BUFFER_SIZE * 2


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test. ``write_pid`` reads
    ``PID_DIR`` from its own module at call time, so an attribute patch
    is sufficient."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


async def _wait_for_daemon_joined(server, channel, nick, timeout=5.0):
    """Poll until the daemon's IRC nick appears in ``server.channels[channel].members``.

    ``AgentDaemon.start()`` returns once the transport task has been spawned, but
    the IRC welcome (001) → JOIN handshake completes asynchronously after that.
    Flooding before the daemon is in the channel means the server delivers no
    PRIVMSGs to it. The most deterministic readiness signal is the server's own
    membership view (the server processes JOIN synchronously on receipt).
    """
    async with asyncio.timeout(timeout):
        while True:
            ch = server.channels.get(channel)
            # ``ch.members`` is a set of Client objects, not nicks.
            if ch is not None and any(getattr(m, "nick", None) == nick for m in ch.members):
                return
            await asyncio.sleep(0.05)


async def _wait_for_buffer_delta(daemon, channel, baseline, expected_delta, timeout=5.0):
    """Poll until ``daemon._buffer`` has ingested ``expected_delta`` *additional*
    messages on ``channel`` since ``baseline`` was captured.

    ``_totals`` is a lifetime counter that may already be non-zero before the
    flood (e.g. from system messages or prior test setup), so a baseline-relative
    comparison is required — an absolute ``>= expected`` check can return early.
    """
    target = baseline + expected_delta
    async with asyncio.timeout(timeout):
        while True:
            if daemon._buffer is not None and daemon._buffer._totals.get(channel, 0) >= target:
                return
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_buffer_drops_oldest_on_overflow(server, make_client, tmp_path, monkeypatch):
    """Flooding 2× buffer size keeps the most-recent N and drops the oldest.

    The deque has ``maxlen=BUFFER_SIZE``, so after FLOOD_COUNT > BUFFER_SIZE
    adds, exactly BUFFER_SIZE messages must remain. The earliest message
    (index 0000) must have been evicted; the latest (index FLOOD_COUNT-1)
    must be retained.
    """
    _redirect_pidfile(monkeypatch, tmp_path)
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
        buffer_size=BUFFER_SIZE,
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tmp_path / "sock"
    sock_dir.mkdir()
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        # Wait for daemon to actually join #general — start() returns before
        # the IRC welcome → JOIN handshake completes. Flooding before this
        # window means the server has no member to deliver PRIVMSGs to.
        await _wait_for_daemon_joined(server, "#general", agent.nick)

        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)

        # Capture the lifetime add count *now*, before the flood. _totals is
        # never reset, so comparing against an absolute target (>= FLOOD_COUNT)
        # races with any pre-flood traffic on the channel.
        baseline = daemon._buffer._totals.get("#general", 0)

        for i in range(FLOOD_COUNT):
            await human.send(f"PRIVMSG #general :flood-msg-{i:04d}")

        await _wait_for_buffer_delta(daemon, "#general", baseline, FLOOD_COUNT)

        # MessageBuffer.read advances a per-channel cursor; only the first
        # call after the flood returns the retained slice. Subsequent reads
        # return [].
        sock_path = os.path.join(str(sock_dir), "culture-testserv-bot.sock")
        skill = SkillClient(sock_path)
        await skill.connect()
        try:
            result = await skill.irc_read("#general", limit=FLOOD_COUNT)
            assert result["ok"]
            messages = result["data"]["messages"]
            texts = [m["text"] for m in messages]
            assert len(messages) == BUFFER_SIZE, f"got {len(messages)} messages: {texts}"
            assert any(f"flood-msg-{FLOOD_COUNT - 1:04d}" in t for t in texts)
            assert not any("flood-msg-0000" in t for t in texts)
        finally:
            await skill.close()
    finally:
        await daemon.stop()
