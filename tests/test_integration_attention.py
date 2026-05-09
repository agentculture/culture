"""End-to-end attention behaviors — proves the AttentionTracker state
machine through the full daemon → IRCTransport → tracker chain.

Replaces the integration-shaped portions of tests/harness/test_attention.py
and tests/harness/test_attention_config.py at the integration layer; the
unit tests themselves move to cultureagent in Phase 1.
"""

import asyncio
import time

import pytest

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
    WebhookConfig,
)
from culture.clients.claude.daemon import AgentDaemon
from culture.clients.shared.attention import Band

# Decay overrides: hold_s=2 across HOT/WARM/COOL with tick_s=2 and
# interval_s=2. Validates monotonicity (HOT≤WARM≤COOL≤IDLE intervals)
# and tick_s ≤ min(intervals). Shape mirrors `_build_attention_config`
# parsing in culture/clients/claude/config.py. With hold_s=2 and a
# 3-second sleep, exactly one decay step fires (HOT → WARM): the
# tracker advances `last_promote_at += hold_s`, leaving ~1s of
# remaining elapsed time, which is ≤ hold_s and so terminates the
# decay loop. Picking 3s of sleep against 2s of hold leaves a full
# second of CI slack before a second step would fire.
DECAY_OVERRIDES = {
    "tick_s": 2,
    "bands": {
        "hot": {"interval_s": 2, "hold_s": 2},
        "warm": {"interval_s": 2, "hold_s": 2},
        "cool": {"interval_s": 2, "hold_s": 2},
        "idle": {"interval_s": 2},
    },
}


def _redirect_pidfile(monkeypatch, tmp_path):
    """Redirect ``culture.pidfile.PID_DIR`` so daemons don't write into the
    real ``~/.culture/pids`` from a unit test. ``write_pid`` reads
    ``PID_DIR`` from its own module at call time, so an attribute patch
    is sufficient — no other modules cache the value."""
    monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "pids"))


async def _wait_for_band(daemon, target, expected, timeout=5.0):
    """Bounded poll until ``daemon._attention.snapshot()[target].band == expected``.

    Replaces fixed ``asyncio.sleep`` waits for IRC processing — those are
    racy on slow CI because PRIVMSG → on_mention → AttentionTracker is
    asynchronous. A short polling loop bounded by a timeout converges as
    soon as the state is correct, with a deterministic upper bound.
    """
    async with asyncio.timeout(timeout):
        while True:
            state = daemon._attention.snapshot().get(target)
            if state is not None and state.band == expected:
                return
            await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_mention_bumps_attention_band(server, make_client, tmp_path, monkeypatch):
    """A direct @mention promotes the channel to Band.HOT."""
    _redirect_pidfile(monkeypatch, tmp_path)
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tmp_path / "sock1"
    sock_dir.mkdir()
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        # Initial state: channel seeded at IDLE.
        snapshot = daemon._attention.snapshot()
        assert snapshot["#general"].band == Band.IDLE

        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        # Mention requires `@nick` syntax — see _detect_and_fire_mention in
        # culture/clients/shared/irc_transport.py.
        await human.send("PRIVMSG #general :@testserv-bot are you there?")

        await _wait_for_band(daemon, "#general", Band.HOT)
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_attention_decays_after_hold_window(server, make_client, tmp_path, monkeypatch):
    """A direct stimulus followed by a 3s pause walks exactly one band cooler.

    Decay is applied lazily inside ``due_targets(now)`` (not ``snapshot()``).
    Calling ``due_targets`` directly forces decay deterministically, avoiding
    races with the daemon's own tick scheduler.
    """
    _redirect_pidfile(monkeypatch, tmp_path)
    agent = AgentConfig(
        nick="testserv-bot",
        directory="/tmp",
        channels=["#general"],
        attention_overrides=DECAY_OVERRIDES,
    )
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    sock_dir = tmp_path / "sock2"
    sock_dir.mkdir()
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        await human.send("PRIVMSG #general :@testserv-bot ping")
        await _wait_for_band(daemon, "#general", Band.HOT)

        # Sleep past hold_s=2; after exactly one decay iteration the tracker
        # advances last_promote_at += hold_s, leaving ~1s of remaining
        # elapsed time which is ≤ hold_s — so the loop terminates at WARM.
        await asyncio.sleep(3.0)
        daemon._attention.due_targets(time.monotonic())

        snapshot = daemon._attention.snapshot()
        assert snapshot["#general"].band == Band.WARM
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_per_channel_state_diverges_under_different_stimuli(
    server, make_client, tmp_path, monkeypatch
):
    """Two channels develop different attention states from different stimuli.

    AttentionTracker maintains per-target TargetState, so a mention on one
    channel must leave a sibling channel untouched. This is the integration
    proxy for "dynamic levels per channel" — the per-channel state machine,
    not the config-level overrides (which are per-agent, not per-channel).
    """
    _redirect_pidfile(monkeypatch, tmp_path)
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general", "#quiet"])
    sock_dir = tmp_path / "sock3"
    sock_dir.mkdir()
    daemon = AgentDaemon(config, agent, socket_dir=str(sock_dir), skip_claude=True)
    await daemon.start()
    try:
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.send("JOIN #quiet")
        await human.recv_all(timeout=0.3)
        # Stimulate only #general; #quiet stays IDLE.
        await human.send("PRIVMSG #general :@testserv-bot here")

        await _wait_for_band(daemon, "#general", Band.HOT)
        # #quiet has had no stimulus — assert it's still IDLE (the seed band).
        assert daemon._attention.snapshot()["#quiet"].band == Band.IDLE
    finally:
        await daemon.stop()
