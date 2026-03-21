import pytest
import tempfile
import os
from pathlib import Path


def test_load_config_from_yaml():
    """Load a complete agents.yaml and verify all fields parse."""
    from clients.claude.config import load_config

    yaml_content = """\
server:
  host: 127.0.0.1
  port: 6667

supervisor:
  model: claude-sonnet-4-6
  thinking: medium
  window_size: 20
  eval_interval: 5
  escalation_threshold: 3

webhooks:
  url: "https://example.com/webhook"
  irc_channel: "#alerts"
  events:
    - agent_spiraling
    - agent_error

buffer_size: 300

agents:
  - nick: spark-claude
    directory: /tmp/test
    channels:
      - "#general"
      - "#dev"
    model: claude-opus-4-6
    thinking: medium
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            assert config.server.host == "127.0.0.1"
            assert config.server.port == 6667
            assert config.supervisor.model == "claude-sonnet-4-6"
            assert config.supervisor.window_size == 20
            assert config.supervisor.eval_interval == 5
            assert config.supervisor.escalation_threshold == 3
            assert config.webhooks.url == "https://example.com/webhook"
            assert config.webhooks.irc_channel == "#alerts"
            assert len(config.webhooks.events) == 2
            assert config.buffer_size == 300
            assert len(config.agents) == 1
            agent = config.agents[0]
            assert agent.nick == "spark-claude"
            assert agent.directory == "/tmp/test"
            assert agent.channels == ["#general", "#dev"]
            assert agent.model == "claude-opus-4-6"
            assert agent.thinking == "medium"
        finally:
            os.unlink(f.name)


def test_load_config_defaults():
    """Missing optional fields get defaults."""
    from clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-claude
    directory: /tmp
    channels:
      - "#general"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            assert config.server.host == "localhost"
            assert config.server.port == 6667
            assert config.supervisor.model == "claude-sonnet-4-6"
            assert config.supervisor.thinking == "medium"
            assert config.supervisor.window_size == 20
            assert config.supervisor.eval_interval == 5
            assert config.supervisor.escalation_threshold == 3
            assert config.webhooks.url is None
            assert config.webhooks.irc_channel == "#alerts"
            assert config.buffer_size == 500
            agent = config.agents[0]
            assert agent.model == "claude-opus-4-6"
            assert agent.thinking == "medium"
        finally:
            os.unlink(f.name)


def test_get_agent_by_nick():
    """Look up an agent config by nick."""
    from clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-claude
    directory: /tmp/a
    channels: ["#general"]
  - nick: spark-claude2
    directory: /tmp/b
    channels: ["#dev"]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            agent = config.get_agent("spark-claude2")
            assert agent is not None
            assert agent.directory == "/tmp/b"
            assert config.get_agent("nonexistent") is None
        finally:
            os.unlink(f.name)
