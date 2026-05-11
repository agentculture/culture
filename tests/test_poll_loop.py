import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from cultureagent.clients.claude.daemon import AgentDaemon

from culture.clients.claude.config import (
    AgentConfig,
    DaemonConfig,
    ServerConnConfig,
)
from culture.clients.shared.attention import AttentionConfig

# These tests exercise the legacy fixed-interval poll loop. The new
# attention-driven loop ticks every ``attention.tick_s`` seconds (default 5s),
# which would never satisfy the sub-2s timing assertions below, so we
# explicitly opt every DaemonConfig in this file into the legacy path.
_LEGACY_ATTENTION = AttentionConfig(enabled=False)


def _inject_fake_runner(daemon):
    """Inject a fake agent runner that records prompts."""
    runner = MagicMock()
    runner.is_running.return_value = True
    runner.send_prompt = AsyncMock()
    runner.stop = AsyncMock()
    daemon._agent_runner = runner
    return runner


@pytest.mark.asyncio
async def test_poll_loop_sends_prompt_on_unread(server, make_client):
    """Poll loop should detect unread messages and send them to the agent."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        poll_interval=1,  # 1 second for fast testing
        attention=_LEGACY_ATTENTION,
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    runner = _inject_fake_runner(daemon)
    await asyncio.sleep(0.5)

    # Human sends a message (no @mention)
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #general :hello everyone")

    # Wait for poll to fire
    await asyncio.sleep(1.5)

    # Poll loop should have sent the prompt to the agent runner
    assert runner.send_prompt.call_count >= 1
    prompt = runner.send_prompt.call_args[0][0]
    assert "hello everyone" in prompt
    assert "[IRC Channel Poll: #general]" in prompt

    await daemon.stop()


@pytest.mark.asyncio
async def test_poll_loop_skips_when_paused(server, make_client):
    """Poll loop should not process messages when the agent is paused."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        poll_interval=1,
        attention=_LEGACY_ATTENTION,
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    runner = _inject_fake_runner(daemon)

    # Pause the daemon
    daemon._paused = True

    await asyncio.sleep(0.5)

    # Human sends a message
    human = await make_client(nick="testserv-ori", user="ori")
    await human.send("JOIN #general")
    await human.recv_all(timeout=0.3)
    await human.send("PRIVMSG #general :paused message")
    await asyncio.sleep(1.5)  # Wait past poll interval

    # Poll should NOT have sent any prompts
    runner.send_prompt.assert_not_called()

    # Buffer should still have unread messages
    msgs = daemon._buffer.read("#general")
    assert len(msgs) >= 1
    assert any("paused message" in m.text for m in msgs)

    await daemon.stop()


@pytest.mark.asyncio
async def test_poll_loop_skips_empty_buffer(server):
    """Poll loop should not send prompts when buffer is empty."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        poll_interval=1,
        attention=_LEGACY_ATTENTION,
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    runner = _inject_fake_runner(daemon)
    await asyncio.sleep(1.5)  # Wait past poll interval

    # No messages were sent, so poll should not trigger
    runner.send_prompt.assert_not_called()

    await daemon.stop()


@pytest.mark.asyncio
async def test_poll_loop_disabled_with_zero_interval(server):
    """Poll loop should exit immediately when poll_interval is 0."""
    config = DaemonConfig(
        server=ServerConnConfig(host="127.0.0.1", port=server.config.port),
        poll_interval=0,
        attention=_LEGACY_ATTENTION,
    )
    agent = AgentConfig(nick="testserv-bot", directory="/tmp", channels=["#general"])
    sock_dir = tempfile.mkdtemp()
    daemon = AgentDaemon(config, agent, socket_dir=sock_dir, skip_claude=True)
    await daemon.start()
    await asyncio.sleep(0.5)

    # Poll task should have completed (returned immediately)
    assert daemon._poll_task.done()

    await daemon.stop()
