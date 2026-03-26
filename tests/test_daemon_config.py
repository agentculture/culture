import pytest
import tempfile
import os
import shutil
from pathlib import Path


def test_load_config_from_yaml():
    """Load a complete agents.yaml and verify all fields parse."""
    from agentirc.clients.claude.config import load_config

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
  - nick: spark-agentirc
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
            assert agent.nick == "spark-agentirc"
            assert agent.directory == "/tmp/test"
            assert agent.channels == ["#general", "#dev"]
            assert agent.model == "claude-opus-4-6"
            assert agent.thinking == "medium"
        finally:
            os.unlink(f.name)


def test_load_config_defaults():
    """Missing optional fields get defaults."""
    from agentirc.clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-agentirc
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
    from agentirc.clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-agentirc
    directory: /tmp/a
    channels: ["#general"]
  - nick: spark-assimilai
    directory: /tmp/b
    channels: ["#dev"]
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            agent = config.get_agent("spark-assimilai")
            assert agent is not None
            assert agent.directory == "/tmp/b"
            assert config.get_agent("nonexistent") is None
        finally:
            os.unlink(f.name)


def test_server_name_field():
    """Load YAML with server.name and verify it parses."""
    from agentirc.clients.claude.config import load_config

    yaml_content = """\
server:
  name: my-server
  host: 127.0.0.1
  port: 6667

agents: []
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            assert config.server.name == "my-server"
            assert config.server.host == "127.0.0.1"
            assert config.server.port == 6667
        finally:
            os.unlink(f.name)


def test_server_name_default():
    """Load YAML without server.name and verify default 'agentirc'."""
    from agentirc.clients.claude.config import load_config

    yaml_content = """\
server:
  host: 127.0.0.1
  port: 6667

agents: []
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        f.flush()
        try:
            config = load_config(f.name)
            assert config.server.name == "agentirc"
        finally:
            os.unlink(f.name)


def test_sanitize_agent_name():
    """Test sanitize_agent_name with various inputs."""
    from agentirc.clients.claude.config import sanitize_agent_name

    assert sanitize_agent_name("My Project") == "my-project"
    assert sanitize_agent_name("agentirc") == "agentirc"
    assert sanitize_agent_name(".hidden") == "hidden"
    assert sanitize_agent_name("UPPER_case") == "upper-case"

    with pytest.raises(ValueError):
        sanitize_agent_name("")

    with pytest.raises(ValueError):
        sanitize_agent_name("...")


def test_save_and_load_roundtrip():
    """Save a DaemonConfig, load it back, verify all fields match."""
    from agentirc.clients.claude.config import (
        AgentConfig, DaemonConfig, ServerConnConfig,
        SupervisorConfig, WebhookConfig,
        save_config, load_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "config.yaml")

        original = DaemonConfig(
            server=ServerConnConfig(name="test-server", host="10.0.0.1", port=6668),
            supervisor=SupervisorConfig(model="claude-opus-4-6", thinking="high"),
            webhooks=WebhookConfig(url="https://hook.example.com", irc_channel="#ops"),
            buffer_size=200,
            agents=[
                AgentConfig(
                    nick="spark-agentirc",
                    directory="/tmp/work",
                    channels=["#general", "#dev"],
                    model="claude-opus-4-6",
                    thinking="medium",
                ),
            ],
        )

        save_config(path, original)
        loaded = load_config(path)

        assert loaded.server.name == "test-server"
        assert loaded.server.host == "10.0.0.1"
        assert loaded.server.port == 6668
        assert loaded.supervisor.model == "claude-opus-4-6"
        assert loaded.supervisor.thinking == "high"
        assert loaded.webhooks.url == "https://hook.example.com"
        assert loaded.webhooks.irc_channel == "#ops"
        assert loaded.buffer_size == 200
        assert len(loaded.agents) == 1
        assert loaded.agents[0].nick == "spark-agentirc"
        assert loaded.agents[0].directory == "/tmp/work"
        assert loaded.agents[0].channels == ["#general", "#dev"]
    finally:
        shutil.rmtree(tmpdir)


def test_load_config_or_default_missing_file():
    """Nonexistent path returns default config."""
    from agentirc.clients.claude.config import load_config_or_default

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "nonexistent.yaml")
        config = load_config_or_default(path)
        assert config.server.name == "agentirc"
        assert config.server.host == "localhost"
        assert config.server.port == 6667
        assert config.agents == []
        assert config.buffer_size == 500
    finally:
        shutil.rmtree(tmpdir)


def test_add_agent_to_empty_config():
    """No file exists, add_agent_to_config creates it with the agent."""
    from agentirc.clients.claude.config import (
        AgentConfig, add_agent_to_config, load_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        agent = AgentConfig(
            nick="spark-agentirc",
            directory="/tmp/work",
            channels=["#general"],
        )

        config = add_agent_to_config(path, agent)
        assert len(config.agents) == 1
        assert config.agents[0].nick == "spark-agentirc"

        # Verify file was written
        loaded = load_config(path)
        assert len(loaded.agents) == 1
        assert loaded.agents[0].nick == "spark-agentirc"
    finally:
        shutil.rmtree(tmpdir)


def test_add_agent_to_existing_config():
    """Existing config with one agent, add second, both preserved."""
    from agentirc.clients.claude.config import (
        AgentConfig, add_agent_to_config, save_config,
        DaemonConfig, load_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")

        # Create initial config with one agent
        initial = DaemonConfig(
            agents=[
                AgentConfig(nick="spark-agentirc", directory="/tmp/a", channels=["#general"]),
            ],
        )
        save_config(path, initial)

        # Add a second agent
        new_agent = AgentConfig(
            nick="spark-ori",
            directory="/tmp/b",
            channels=["#dev"],
        )
        config = add_agent_to_config(path, new_agent)
        assert len(config.agents) == 2

        # Verify both agents are persisted
        loaded = load_config(path)
        assert len(loaded.agents) == 2
        nicks = {a.nick for a in loaded.agents}
        assert nicks == {"spark-agentirc", "spark-ori"}
    finally:
        shutil.rmtree(tmpdir)


def test_add_agent_nick_collision():
    """Duplicate nick raises ValueError."""
    from agentirc.clients.claude.config import (
        AgentConfig, add_agent_to_config, save_config, DaemonConfig,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")

        # Create config with existing agent
        initial = DaemonConfig(
            agents=[
                AgentConfig(nick="spark-agentirc", directory="/tmp/a", channels=["#general"]),
            ],
        )
        save_config(path, initial)

        # Try to add agent with same nick
        duplicate = AgentConfig(
            nick="spark-agentirc",
            directory="/tmp/b",
            channels=["#dev"],
        )
        with pytest.raises(ValueError, match="already exists"):
            add_agent_to_config(path, duplicate)
    finally:
        shutil.rmtree(tmpdir)
