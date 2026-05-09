"""End-to-end attention behaviors — proves the AttentionTracker state
machine through the full daemon → IRCTransport → tracker chain.

Replaces the integration-shaped portions of tests/harness/test_attention.py
and tests/harness/test_attention_config.py at the integration layer; the
unit tests themselves move to cultureagent in Phase 1.
"""

import asyncio
import tempfile
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

# Fast-decay overrides: 1s hold/interval/tick across all bands. Validates
# monotonicity (HOT≤WARM≤COOL≤IDLE intervals) and tick_s ≤ min(intervals).
# Shape mirrors `_build_attention_config` parsing in
# culture/clients/claude/config.py.
FAST_DECAY_OVERRIDES = {
    "tick_s": 1,
    "bands": {
        "hot": {"interval_s": 1, "hold_s": 1},
        "warm": {"interval_s": 1, "hold_s": 1},
        "cool": {"interval_s": 1, "hold_s": 1},
        "idle": {"interval_s": 1},
    },
}


@pytest.mark.asyncio
async def test_mention_bumps_attention_band(server, make_client):
    """A direct @mention promotes the channel to Band.HOT."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        snapshot = daemon._attention.snapshot()
        assert snapshot["#general"].band == Band.IDLE

        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        # Mention requires `@nick` syntax — see _detect_and_fire_mention in
        # culture/clients/shared/irc_transport.py.
        await human.send("PRIVMSG #general :@testserv-bot are you there?")
        await asyncio.sleep(0.5)

        snapshot = daemon._attention.snapshot()
        assert snapshot["#general"].band == Band.HOT
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_attention_decays_after_hold_window(server, make_client):
    """Without further stimulus, attention walks one band cooler past hold_s.

    Decay is applied lazily inside `due_targets(now)` (not snapshot()). The
    daemon's poll loop calls due_targets at every tick, so sleeping past
    hold_s is sufficient — but we also call due_targets directly to force
    decay deterministically (avoids races with the tick scheduler).
    """
    agent = AgentConfig(
        nick="testserv-bot",
        directory="/tmp",
        channels=["#general"],
        attention_overrides=FAST_DECAY_OVERRIDES,
    )
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.recv_all(timeout=0.3)
        await human.send("PRIVMSG #general :@testserv-bot ping")
        await asyncio.sleep(0.5)

        snapshot = daemon._attention.snapshot()
        assert snapshot["#general"].band == Band.HOT

        # Sleep past hold_s=1, then force decay evaluation. _apply_decay
        # walks one band per hold window; with all holds at 1s, two
        # seconds is enough for HOT → WARM (and possibly further).
        await asyncio.sleep(2.0)
        daemon._attention.due_targets(time.monotonic())

        snapshot = daemon._attention.snapshot()
        assert snapshot["#general"].band > Band.HOT
    finally:
        await daemon.stop()


@pytest.mark.asyncio
async def test_per_channel_state_diverges_under_different_stimuli(server, make_client):
    """Two channels develop different attention states from different stimuli.

    AttentionTracker maintains per-target TargetState, so a mention on one
    channel must leave a sibling channel untouched. This is the integration
    proxy for "dynamic levels per channel" — the per-channel state machine,
    not the config-level overrides (which are per-agent, not per-channel).
    """
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        webhooks=WebhookConfig(url=None),
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general", "#quiet"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)
    try:
        human = await make_client(nick="testserv-ori", user="ori")
        await human.send("JOIN #general")
        await human.send("JOIN #quiet")
        await human.recv_all(timeout=0.3)
        # Stimulate only #general; #quiet stays IDLE.
        await human.send("PRIVMSG #general :@testserv-bot here")
        await asyncio.sleep(0.5)

        snapshot = daemon._attention.snapshot()
        assert snapshot["#general"].band == Band.HOT
        assert snapshot["#quiet"].band == Band.IDLE
    finally:
        await daemon.stop()
