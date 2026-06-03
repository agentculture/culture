"""Tests for @mention validation warnings."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from culture.clients.claude.config import AgentConfig, DaemonConfig
from culture.clients.claude.daemon import AgentDaemon
from culture.clients.claude.message_buffer import MessageBuffer


@pytest.fixture
def daemon_with_buffer():
    config = DaemonConfig()
    agent = AgentConfig(
        nick="test-agent",
        agent="claude",
        directory="/tmp/test",
        channels=["#general"],
    )
    d = AgentDaemon(config, agent, skip_claude=True)
    d._buffer = MessageBuffer()
    d._buffer.add("#general", "alice", "hello")
    d._buffer.add("#general", "bob", "hi")
    d._transport = MagicMock()
    d._transport.channels = ["#general"]
    d._transport.send_privmsg = AsyncMock()
    return d


@pytest.mark.asyncio
async def test_send_with_unknown_mention_includes_warning(daemon_with_buffer):
    """Messages mentioning unknown nicks should include warnings."""
    resp = await daemon_with_buffer._ipc_irc_send(
        "req-m1",
        {"channel": "#general", "message": "hey @nonexistent-user check this"},
    )
    assert resp["ok"] is True
    assert "warnings" in resp
    assert any("nonexistent-user" in w for w in resp["warnings"])


@pytest.mark.asyncio
async def test_send_with_known_mention_no_warning(daemon_with_buffer):
    """Messages mentioning known nicks should not include warnings."""
    resp = await daemon_with_buffer._ipc_irc_send(
        "req-m2",
        {"channel": "#general", "message": "hey @alice check this"},
    )
    assert resp["ok"] is True
    assert not resp.get("warnings")


@pytest.mark.asyncio
async def test_send_without_mentions_no_warning(daemon_with_buffer):
    """Messages without @mentions should not include warnings."""
    resp = await daemon_with_buffer._ipc_irc_send(
        "req-m3",
        {"channel": "#general", "message": "hello world"},
    )
    assert resp["ok"] is True
    assert "warnings" not in resp
