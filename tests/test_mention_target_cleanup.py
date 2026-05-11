"""Tests for _mention_targets deque cleanup on prompt failure.

When a prompt fails (timeout, error), the corresponding _mention_targets
entry must be cleaned up so that future responses route correctly.
"""

import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from cultureagent.clients.acp.daemon import ACPDaemon

from culture.clients.acp.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
)
from culture.clients.shared.attention import AttentionConfig


def _make_daemon(server_port: int) -> ACPDaemon:
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server_port),
        poll_interval=0,
        # Disable attention so the legacy poll loop honors poll_interval=0.
        attention=AttentionConfig(enabled=False),
    )
    agent = AgentConfig(
        nick="testserv-bot",
        directory="/tmp",
        channels=["#general"],
        acp_command=["echo"],
    )
    sock_dir = tempfile.mkdtemp()
    return ACPDaemon(config, agent, socket_dir=sock_dir, skip_agent=True)


def _inject_fake_runner(daemon):
    """Inject a fake agent runner that records prompts."""
    runner = MagicMock()
    runner.is_running.return_value = True
    runner.send_prompt = AsyncMock()
    runner.stop = AsyncMock()
    daemon._agent_runner = runner
    return runner


@pytest.mark.asyncio
async def test_on_turn_error_pops_stale_target(server):
    """_on_turn_error should pop the front entry from _mention_targets."""
    daemon = _make_daemon(server.config.port)
    await daemon.start()

    # Simulate: poll enqueued a target, then the prompt failed
    daemon._mention_targets.append("#general")
    daemon._mention_targets.append("#code-review")
    assert len(daemon._mention_targets) == 2

    await daemon._on_turn_error()
    assert len(daemon._mention_targets) == 1
    assert daemon._mention_targets[0] == "#code-review"

    await daemon.stop()


@pytest.mark.asyncio
async def test_on_turn_error_empty_deque_is_safe(server):
    """_on_turn_error should be a no-op on an empty deque."""
    daemon = _make_daemon(server.config.port)
    await daemon.start()

    assert len(daemon._mention_targets) == 0
    await daemon._on_turn_error()  # should not raise
    assert len(daemon._mention_targets) == 0

    await daemon.stop()


@pytest.mark.asyncio
async def test_relay_routes_correctly_after_error_cleanup(server, make_client):
    """After a failed prompt is cleaned up, the next response should route correctly."""
    daemon = _make_daemon(server.config.port)
    await daemon.start()
    _inject_fake_runner(daemon)
    await asyncio.sleep(0.5)

    # Simulate: system prompt enqueued None, then timed out and was cleaned up
    daemon._mention_targets.append(None)
    await daemon._on_turn_error()  # cleans up None

    # Now simulate a real mention that succeeds
    daemon._mention_targets.append("#general")

    # Simulate agent response
    sent_messages = []
    original_send = daemon._transport.send_privmsg

    async def capture_send(target, text):
        sent_messages.append((target, text))
        await original_send(target, text)

    daemon._transport.send_privmsg = capture_send

    msg = {
        "type": "assistant",
        "content": [{"type": "text", "text": "Hello from bot!"}],
    }
    await daemon._relay_response_to_irc(msg)

    assert len(sent_messages) == 1
    assert sent_messages[0][0] == "#general"
    assert "Hello from bot!" in sent_messages[0][1]
    assert len(daemon._mention_targets) == 0

    await daemon.stop()


@pytest.mark.asyncio
async def test_multiple_errors_drain_deque_correctly(server):
    """Multiple consecutive errors should each pop one entry."""
    daemon = _make_daemon(server.config.port)
    await daemon.start()

    # Simulate 3 failed prompts
    daemon._mention_targets.append(None)  # system prompt
    daemon._mention_targets.append("#general")  # poll 1
    daemon._mention_targets.append("#general")  # poll 2

    await daemon._on_turn_error()  # pops None
    await daemon._on_turn_error()  # pops #general
    assert len(daemon._mention_targets) == 1
    assert daemon._mention_targets[0] == "#general"

    await daemon._on_turn_error()  # pops last #general
    assert len(daemon._mention_targets) == 0

    await daemon.stop()
