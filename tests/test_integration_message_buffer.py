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


async def _wait_for_buffer_total(daemon, channel, expected_total, timeout=5.0):
    """Poll until ``daemon._buffer`` has ingested ``expected_total`` messages on
    ``channel``. Replaces fixed ``asyncio.sleep`` waits — PRIVMSG → transport
    → buffer ingestion is asynchronous, so a bounded poll is deterministic
    instead of racy. ``_totals`` is the lifetime add count, never decreases,
    so ``>=`` is the correct comparator."""
    async with asyncio.timeout(timeout):
        while True:
            if (
                daemon._buffer is not None
                and daemon._buffer._totals.get(channel, 0) >= expected_total
            ):
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
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)

        for i in range(FLOOD_COUNT):
            await human.send(f"PRIVMSG #general :flood-msg-{i:04d}")

        await _wait_for_buffer_total(daemon, "#general", FLOOD_COUNT)

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
