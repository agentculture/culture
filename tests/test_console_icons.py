"""Tests for icon field in AgentConfig and ICON send on connect."""

import tempfile
from pathlib import Path

import pytest
import yaml

from culture.clients.acp.config import AgentConfig as AcpAgentConfig
from culture.clients.acp.config import load_config as acp_load_config
from culture.clients.claude.config import AgentConfig as ClaudeAgentConfig
from culture.clients.claude.config import load_config as claude_load_config
from culture.clients.codex.config import AgentConfig as CodexAgentConfig
from culture.clients.codex.config import load_config as codex_load_config
from culture.clients.copilot.config import AgentConfig as CopilotAgentConfig
from culture.clients.copilot.config import load_config as copilot_load_config

# ---------------------------------------------------------------------------
# AgentConfig icon field — all backends
# ---------------------------------------------------------------------------


def test_claude_agent_config_has_icon_field():
    cfg = ClaudeAgentConfig(nick="spark-claude", icon="★")
    assert cfg.icon == "★"


def test_claude_agent_config_icon_default_none():
    cfg = ClaudeAgentConfig(nick="spark-claude")
    assert cfg.icon is None


def test_codex_agent_config_has_icon_field():
    cfg = CodexAgentConfig(nick="spark-codex", icon="◆")
    assert cfg.icon == "◆"


def test_codex_agent_config_icon_default_none():
    cfg = CodexAgentConfig(nick="spark-codex")
    assert cfg.icon is None


def test_copilot_agent_config_has_icon_field():
    cfg = CopilotAgentConfig(nick="spark-copilot", icon="●")
    assert cfg.icon == "●"


def test_copilot_agent_config_icon_default_none():
    cfg = CopilotAgentConfig(nick="spark-copilot")
    assert cfg.icon is None


def test_acp_agent_config_has_icon_field():
    cfg = AcpAgentConfig(nick="spark-acp", icon="▲")
    assert cfg.icon == "▲"


def test_acp_agent_config_icon_default_none():
    cfg = AcpAgentConfig(nick="spark-acp")
    assert cfg.icon is None


# ---------------------------------------------------------------------------
# load_config YAML → icon field round-trip
# ---------------------------------------------------------------------------


def _write_yaml(data: dict) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump(data, f)
        return f.name


def test_claude_agent_config_from_yaml_with_icon():
    """Icon field should be loaded from YAML config for claude backend."""
    data = {
        "server": {"name": "spark", "host": "localhost", "port": 6667},
        "agents": [{"nick": "spark-claude", "icon": "★"}],
    }
    path = _write_yaml(data)
    try:
        config = claude_load_config(path)
        assert config.agents[0].icon == "★"
    finally:
        Path(path).unlink()


def test_claude_agent_config_from_yaml_without_icon():
    """Missing icon in YAML should default to None."""
    data = {
        "server": {"name": "spark", "host": "localhost", "port": 6667},
        "agents": [{"nick": "spark-claude"}],
    }
    path = _write_yaml(data)
    try:
        config = claude_load_config(path)
        assert config.agents[0].icon is None
    finally:
        Path(path).unlink()


def test_codex_agent_config_from_yaml_with_icon():
    data = {
        "server": {"name": "spark", "host": "localhost", "port": 6667},
        "agents": [{"nick": "spark-codex", "icon": "◆"}],
    }
    path = _write_yaml(data)
    try:
        config = codex_load_config(path)
        assert config.agents[0].icon == "◆"
    finally:
        Path(path).unlink()


def test_copilot_agent_config_from_yaml_with_icon():
    data = {
        "server": {"name": "spark", "host": "localhost", "port": 6667},
        "agents": [{"nick": "spark-copilot", "icon": "●"}],
    }
    path = _write_yaml(data)
    try:
        config = copilot_load_config(path)
        assert config.agents[0].icon == "●"
    finally:
        Path(path).unlink()


def test_acp_agent_config_from_yaml_with_icon():
    data = {
        "server": {"name": "spark", "host": "localhost", "port": 6667},
        "agents": [{"nick": "spark-acp", "acp_command": ["opencode", "acp"], "icon": "▲"}],
    }
    path = _write_yaml(data)
    try:
        config = acp_load_config(path)
        assert config.agents[0].icon == "▲"
    finally:
        Path(path).unlink()


# ---------------------------------------------------------------------------
# IRCTransport: icon param stored + ICON sent on welcome
# ---------------------------------------------------------------------------


def _make_transport(backend: str, icon=None):
    """Build an IRCTransport for the given backend with a mock MessageBuffer."""
    if backend == "claude":
        from culture.clients.claude.irc_transport import IRCTransport
        from culture.clients.claude.message_buffer import MessageBuffer
    elif backend == "codex":
        from culture.clients.codex.irc_transport import IRCTransport
        from culture.clients.codex.message_buffer import MessageBuffer
    elif backend == "copilot":
        from culture.clients.copilot.irc_transport import IRCTransport
        from culture.clients.copilot.message_buffer import MessageBuffer
    elif backend == "acp":
        from culture.clients.acp.irc_transport import IRCTransport
        from culture.clients.acp.message_buffer import MessageBuffer
    else:
        raise ValueError(f"unknown backend: {backend}")

    buf = MessageBuffer(max_per_channel=10)
    transport = IRCTransport(
        host="localhost",
        port=6667,
        nick=f"spark-{backend}",
        user=f"spark-{backend}",
        channels=["#general"],
        buffer=buf,
        icon=icon,
    )
    return transport


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_transport_stores_icon(backend):
    transport = _make_transport(backend, icon="★")
    assert transport.icon == "★"


@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
def test_transport_icon_default_none(backend):
    transport = _make_transport(backend)
    assert transport.icon is None


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
async def test_transport_sends_icon_on_welcome(backend):
    """_on_welcome should send ICON <icon> when icon is set."""
    transport = _make_transport(backend, icon="★")

    sent_lines = []

    async def fake_send_raw(line):
        sent_lines.append(line)

    transport._send_raw = fake_send_raw

    from culture.protocol.message import Message

    welcome_msg = Message.parse(f":spark 001 spark-{backend} :Welcome")
    await transport._on_welcome(welcome_msg)

    assert "ICON ★" in sent_lines


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["claude", "codex", "copilot", "acp"])
async def test_transport_no_icon_not_sent_on_welcome(backend):
    """_on_welcome should NOT send ICON when icon is None."""
    transport = _make_transport(backend, icon=None)

    sent_lines = []

    async def fake_send_raw(line):
        sent_lines.append(line)

    transport._send_raw = fake_send_raw

    from culture.protocol.message import Message

    welcome_msg = Message.parse(f":spark 001 spark-{backend} :Welcome")
    await transport._on_welcome(welcome_msg)

    assert not any(line.startswith("ICON") for line in sent_lines)
