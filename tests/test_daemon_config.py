import os
import shutil
import tempfile

import pytest


def test_load_config_from_yaml():
    """Load a complete agents.yaml and verify all fields parse."""
    from culture.clients.claude.config import load_config

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
  - nick: spark-culture
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
            assert agent.nick == "spark-culture"
            assert agent.directory == "/tmp/test"
            assert agent.channels == ["#general", "#dev"]
            assert agent.model == "claude-opus-4-6"
            assert agent.thinking == "medium"
        finally:
            os.unlink(f.name)


def test_load_config_defaults():
    """Missing optional fields get defaults."""
    from culture.clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-culture
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
    from culture.clients.claude.config import load_config

    yaml_content = """\
agents:
  - nick: spark-culture
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
    from culture.clients.claude.config import load_config

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
    """Load YAML without server.name and verify default 'culture'."""
    from culture.clients.claude.config import load_config

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
            assert config.server.name == "culture"
        finally:
            os.unlink(f.name)


def test_sanitize_agent_name():
    """Test sanitize_agent_name with various inputs."""
    from culture.clients.claude.config import sanitize_agent_name

    assert sanitize_agent_name("My Project") == "my-project"
    assert sanitize_agent_name("culture") == "culture"
    assert sanitize_agent_name(".hidden") == "hidden"
    assert sanitize_agent_name("UPPER_case") == "upper-case"

    with pytest.raises(ValueError):
        sanitize_agent_name("")

    with pytest.raises(ValueError):
        sanitize_agent_name("...")


def test_save_and_load_roundtrip():
    """Save a DaemonConfig, load it back, verify all fields match."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        SupervisorConfig,
        WebhookConfig,
        load_config,
        save_config,
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
                    nick="spark-culture",
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
        assert loaded.agents[0].nick == "spark-culture"
        assert loaded.agents[0].directory == "/tmp/work"
        assert loaded.agents[0].channels == ["#general", "#dev"]
    finally:
        shutil.rmtree(tmpdir)


def test_load_config_or_default_missing_file():
    """Nonexistent path returns default config."""
    from culture.clients.claude.config import load_config_or_default

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "nonexistent.yaml")
        config = load_config_or_default(path)
        assert config.server.name == "culture"
        assert config.server.host == "localhost"
        assert config.server.port == 6667
        assert config.agents == []
        assert config.buffer_size == 500
    finally:
        shutil.rmtree(tmpdir)


def test_add_agent_to_empty_config():
    """No file exists, add_agent_to_config creates it with the agent."""
    from culture.clients.claude.config import (
        AgentConfig,
        add_agent_to_config,
        load_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        agent = AgentConfig(
            nick="spark-culture",
            directory="/tmp/work",
            channels=["#general"],
        )

        config = add_agent_to_config(path, agent)
        assert len(config.agents) == 1
        assert config.agents[0].nick == "spark-culture"

        # Verify file was written
        loaded = load_config(path)
        assert len(loaded.agents) == 1
        assert loaded.agents[0].nick == "spark-culture"
    finally:
        shutil.rmtree(tmpdir)


def test_add_agent_to_existing_config():
    """Existing config with one agent, add second, both preserved."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        add_agent_to_config,
        load_config,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")

        # Create initial config with one agent
        initial = DaemonConfig(
            agents=[
                AgentConfig(nick="spark-culture", directory="/tmp/a", channels=["#general"]),
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
        assert nicks == {"spark-culture", "spark-ori"}
    finally:
        shutil.rmtree(tmpdir)


def test_add_agent_nick_collision():
    """Duplicate nick raises ValueError."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        add_agent_to_config,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")

        # Create config with existing agent
        initial = DaemonConfig(
            agents=[
                AgentConfig(nick="spark-culture", directory="/tmp/a", channels=["#general"]),
            ],
        )
        save_config(path, initial)

        # Try to add agent with same nick
        duplicate = AgentConfig(
            nick="spark-culture",
            directory="/tmp/b",
            channels=["#dev"],
        )
        with pytest.raises(ValueError, match="already exists"):
            add_agent_to_config(path, duplicate)
    finally:
        shutil.rmtree(tmpdir)


def test_rename_server_updates_config():
    """rename_server changes server.name and agent nick prefixes."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        load_config,
        rename_server,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="culture"),
                agents=[
                    AgentConfig(
                        nick="culture-culture",
                        directory=os.path.join(tmpdir, "a"),
                        channels=["#general"],
                    ),
                ],
            ),
        )

        old_name, renamed = rename_server(path, "spark")
        assert old_name == "culture"
        assert renamed == [("culture-culture", "spark-culture")]

        loaded = load_config(path)
        assert loaded.server.name == "spark"
        assert loaded.agents[0].nick == "spark-culture"
    finally:
        shutil.rmtree(tmpdir)


def test_rename_server_multiple_agents():
    """rename_server renames all agents with the old prefix."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        rename_server,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="old"),
                agents=[
                    AgentConfig(
                        nick="old-claude",
                        directory=os.path.join(tmpdir, "a"),
                        channels=["#general"],
                    ),
                    AgentConfig(
                        nick="old-ori", directory=os.path.join(tmpdir, "b"), channels=["#general"]
                    ),
                ],
            ),
        )

        old_name, renamed = rename_server(path, "new")
        assert old_name == "old"
        assert len(renamed) == 2
        assert ("old-claude", "new-claude") in renamed
        assert ("old-ori", "new-ori") in renamed
    finally:
        shutil.rmtree(tmpdir)


def test_rename_server_no_agents():
    """rename_server works with empty agent list."""
    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        load_config,
        rename_server,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(server=ServerConnConfig(name="culture"), agents=[]),
        )

        old_name, renamed = rename_server(path, "spark")
        assert old_name == "culture"
        assert renamed == []

        loaded = load_config(path)
        assert loaded.server.name == "spark"
    finally:
        shutil.rmtree(tmpdir)


def test_rename_server_noop_same_name():
    """rename_server is a no-op when the name hasn't changed."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        rename_server,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(
                        nick="spark-claude",
                        directory=os.path.join(tmpdir, "a"),
                        channels=["#general"],
                    ),
                ],
            ),
        )

        old_name, renamed = rename_server(path, "spark")
        assert old_name == "spark"
        assert renamed == []
    finally:
        shutil.rmtree(tmpdir)


def test_rename_agent_suffix():
    """rename_agent changes the nick while keeping the same server prefix."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        load_config,
        rename_agent,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(
                        nick="spark-culture",
                        directory=os.path.join(tmpdir, "a"),
                        channels=["#general"],
                    ),
                ],
            ),
        )

        rename_agent(path, "spark-culture", "spark-claude")

        loaded = load_config(path)
        assert loaded.agents[0].nick == "spark-claude"
    finally:
        shutil.rmtree(tmpdir)


def test_rename_agent_reassign_server():
    """rename_agent can move an agent to a different server prefix."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        load_config,
        rename_agent,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="culture"),
                agents=[
                    AgentConfig(
                        nick="culture-claude",
                        directory=os.path.join(tmpdir, "a"),
                        channels=["#general"],
                    ),
                ],
            ),
        )

        rename_agent(path, "culture-claude", "spark-claude")

        loaded = load_config(path)
        assert loaded.agents[0].nick == "spark-claude"
    finally:
        shutil.rmtree(tmpdir)


def test_rename_agent_collision():
    """rename_agent raises ValueError on nick collision."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        rename_agent,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(
                        nick="spark-culture",
                        directory=os.path.join(tmpdir, "a"),
                        channels=["#general"],
                    ),
                    AgentConfig(
                        nick="spark-claude",
                        directory=os.path.join(tmpdir, "b"),
                        channels=["#general"],
                    ),
                ],
            ),
        )

        with pytest.raises(ValueError, match="already exists"):
            rename_agent(path, "spark-culture", "spark-claude")
    finally:
        shutil.rmtree(tmpdir)


def test_rename_agent_not_found():
    """rename_agent raises ValueError when old nick doesn't exist."""
    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        rename_agent,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(server=ServerConnConfig(name="spark"), agents=[]),
        )

        with pytest.raises(ValueError, match="not found"):
            rename_agent(path, "spark-culture", "spark-claude")
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# ACP config load_config strips unknown fields (#157)
# -----------------------------------------------------------------------


def test_acp_load_config_strips_unknown_fields():
    """ACP load_config should not crash on YAML with claude-specific fields (#157)."""
    import yaml as _yaml

    from culture.clients.acp.config import load_config

    yaml_content = {
        "server": {"host": "127.0.0.1", "port": 6667},
        "agents": [
            {
                "nick": "spark-daria",
                "agent": "acp",
                "acp_command": ["opencode", "acp"],
                "thinking": "medium",
                "archived": True,
                "archived_at": "2026-01-01",
                "archived_reason": "test",
            }
        ],
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        _yaml.dump(yaml_content, f)
        f.flush()
        try:
            config = load_config(f.name)
            assert len(config.agents) == 1
            assert config.agents[0].nick == "spark-daria"
            assert config.agents[0].acp_command == ["opencode", "acp"]
        finally:
            os.unlink(f.name)


# -----------------------------------------------------------------------
# _coerce_to_acp_agent preserves icon (#155)
# -----------------------------------------------------------------------


def test_coerce_to_acp_agent_preserves_icon():
    """_coerce_to_acp_agent copies the icon field (#155)."""
    from culture.cli.agent import _coerce_to_acp_agent
    from culture.config import AgentConfig

    agent = AgentConfig(nick="spark-test", icon="robot")
    acp_agent = _coerce_to_acp_agent(agent)
    assert acp_agent.icon == "robot"


def test_coerce_to_acp_agent_icon_none():
    """_coerce_to_acp_agent handles icon=None gracefully."""
    from culture.cli.agent import _coerce_to_acp_agent
    from culture.config import AgentConfig

    agent = AgentConfig(nick="spark-test")
    acp_agent = _coerce_to_acp_agent(agent)
    assert acp_agent.icon is None


# -----------------------------------------------------------------------
# _make_backend_config passes all fields (#156)
# -----------------------------------------------------------------------


def test_make_backend_config_passes_all_fields():
    """_make_backend_config includes supervisor, poll_interval, sleep schedule (#156)."""
    from culture.cli.agent import _make_backend_config
    from culture.clients.acp.config import DaemonConfig as ACPDaemonConfig
    from culture.config import DaemonConfig, ServerConnConfig, SupervisorConfig

    config = DaemonConfig(
        server=ServerConnConfig(name="spark"),
        supervisor=SupervisorConfig(model="custom-model"),
        poll_interval=300,
        sleep_start="22:00",
        sleep_end="09:00",
    )
    result = _make_backend_config(config, ACPDaemonConfig)
    assert result.poll_interval == 300
    assert result.sleep_start == "22:00"
    assert result.sleep_end == "09:00"
