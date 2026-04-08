import os
import tempfile

import pytest


def test_agent_config_defaults():
    """AgentConfig has correct defaults and computed properties."""
    from culture.config import AgentConfig

    agent = AgentConfig()
    assert agent.suffix == ""
    assert agent.backend == "claude"
    assert agent.channels == ["#general"]
    assert agent.model == "claude-opus-4-6"
    assert agent.thinking == "medium"
    assert agent.system_prompt == ""
    assert agent.tags == []
    assert agent.icon is None
    assert agent.archived is False
    assert agent.extras == {}
    # Computed fields
    assert agent.nick == ""
    assert agent.directory == "."
    # Backward compat
    assert agent.agent == "claude"


def test_agent_config_acp_command_from_extras():
    """ACP command is read from extras dict."""
    from culture.config import AgentConfig

    agent = AgentConfig(extras={"acp_command": ["cline", "--acp"]})
    assert agent.acp_command == ["cline", "--acp"]

    # Default when not in extras
    agent2 = AgentConfig()
    assert agent2.acp_command == ["opencode", "acp"]


def test_server_config_defaults():
    """ServerConfig has correct defaults."""
    from culture.config import ServerConfig, ServerConnConfig

    config = ServerConfig()
    assert config.server.name == "culture"
    assert config.server.host == "localhost"
    assert config.server.port == 6667
    assert config.buffer_size == 500
    assert config.poll_interval == 60
    assert config.manifest == {}
    assert config.agents == []


def test_server_config_get_agent():
    """get_agent() looks up by nick."""
    from culture.config import AgentConfig, ServerConfig

    config = ServerConfig(
        agents=[
            AgentConfig(suffix="culture", nick="spark-culture"),
            AgentConfig(suffix="daria", nick="spark-daria"),
        ]
    )
    assert config.get_agent("spark-culture").suffix == "culture"
    assert config.get_agent("spark-daria").suffix == "daria"
    assert config.get_agent("nonexistent") is None


def test_daemon_config_alias():
    """DaemonConfig is an alias for ServerConfig."""
    from culture.config import DaemonConfig, ServerConfig

    assert DaemonConfig is ServerConfig
