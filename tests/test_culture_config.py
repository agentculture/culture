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


def test_load_culture_yaml_single_agent(tmp_path):
    """Load single-agent culture.yaml."""
    from culture.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("""\
suffix: myagent
backend: claude
model: claude-opus-4-6
channels: ["#general", "#dev"]
thinking: medium
system_prompt: "You are helpful."
tags: [test]
""")
    agents = load_culture_yaml(str(tmp_path))
    assert len(agents) == 1
    assert agents[0].suffix == "myagent"
    assert agents[0].backend == "claude"
    assert agents[0].model == "claude-opus-4-6"
    assert agents[0].channels == ["#general", "#dev"]
    assert agents[0].thinking == "medium"
    assert agents[0].system_prompt == "You are helpful."
    assert agents[0].tags == ["test"]
    assert agents[0].directory == str(tmp_path)


def test_load_culture_yaml_multi_agent(tmp_path):
    """Load multi-agent culture.yaml with agents list."""
    from culture.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("""\
agents:
  - suffix: culture
    backend: claude
    model: claude-opus-4-6
  - suffix: codex
    backend: codex
    model: gpt-5.4
""")
    agents = load_culture_yaml(str(tmp_path))
    assert len(agents) == 2
    assert agents[0].suffix == "culture"
    assert agents[0].backend == "claude"
    assert agents[1].suffix == "codex"
    assert agents[1].backend == "codex"
    assert agents[1].model == "gpt-5.4"


def test_load_culture_yaml_by_suffix(tmp_path):
    """Load specific agent from multi-agent culture.yaml."""
    from culture.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("""\
agents:
  - suffix: culture
    backend: claude
  - suffix: codex
    backend: codex
""")
    agents = load_culture_yaml(str(tmp_path), suffix="codex")
    assert len(agents) == 1
    assert agents[0].suffix == "codex"


def test_load_culture_yaml_extras(tmp_path):
    """Unknown fields stored in extras dict."""
    from culture.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("""\
suffix: daria
backend: acp
model: claude-sonnet-4-6
acp_command: ["opencode", "acp"]
custom_field: hello
""")
    agents = load_culture_yaml(str(tmp_path))
    assert agents[0].acp_command == ["opencode", "acp"]
    assert agents[0].extras["custom_field"] == "hello"


def test_load_culture_yaml_missing_file(tmp_path):
    """Missing culture.yaml raises FileNotFoundError."""
    from culture.config import load_culture_yaml

    with pytest.raises(FileNotFoundError):
        load_culture_yaml(str(tmp_path))


def test_load_culture_yaml_suffix_not_found(tmp_path):
    """Requesting nonexistent suffix raises ValueError."""
    from culture.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("suffix: culture\nbackend: claude\n")

    with pytest.raises(ValueError, match="not found"):
        load_culture_yaml(str(tmp_path), suffix="nonexistent")


def test_load_server_config(tmp_path):
    """Load server.yaml with manifest."""
    from culture.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("""\
server:
  name: spark
  host: 127.0.0.1
  port: 6667

supervisor:
  model: claude-sonnet-4-6
  thinking: medium

webhooks:
  url: https://example.com/hook
  irc_channel: "#alerts"
  events: [agent_error]

buffer_size: 300
poll_interval: 30

agents:
  culture: /tmp/proj-a
  daria: /tmp/proj-b
""")
    config = load_server_config(str(server_yaml))
    assert config.server.name == "spark"
    assert config.server.host == "127.0.0.1"
    assert config.supervisor.model == "claude-sonnet-4-6"
    assert config.webhooks.url == "https://example.com/hook"
    assert config.buffer_size == 300
    assert config.poll_interval == 30
    assert config.manifest == {"culture": "/tmp/proj-a", "daria": "/tmp/proj-b"}
    assert config.agents == []


def test_load_server_config_defaults(tmp_path):
    """Minimal server.yaml gets defaults."""
    from culture.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("server:\n  name: spark\n")
    config = load_server_config(str(server_yaml))
    assert config.server.name == "spark"
    assert config.server.host == "localhost"
    assert config.buffer_size == 500
    assert config.manifest == {}


def test_resolve_agents(tmp_path):
    """resolve_agents reads culture.yaml from manifest paths."""
    from culture.config import ServerConfig, ServerConnConfig, resolve_agents

    proj_a = tmp_path / "proj-a"
    proj_a.mkdir()
    (proj_a / "culture.yaml").write_text("suffix: culture\nbackend: claude\n")

    proj_b = tmp_path / "proj-b"
    proj_b.mkdir()
    (proj_b / "culture.yaml").write_text(
        "suffix: daria\nbackend: acp\nacp_command: ['opencode', 'acp']\n"
    )

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"culture": str(proj_a), "daria": str(proj_b)},
    )
    resolve_agents(config)

    assert len(config.agents) == 2
    culture = config.get_agent("spark-culture")
    assert culture is not None
    assert culture.backend == "claude"
    assert culture.directory == str(proj_a.resolve())

    daria = config.get_agent("spark-daria")
    assert daria is not None
    assert daria.backend == "acp"
    assert daria.directory == str(proj_b.resolve())


def test_resolve_agents_missing_culture_yaml(tmp_path):
    """Missing culture.yaml logs warning, agent skipped."""
    from culture.config import ServerConfig, ServerConnConfig, resolve_agents

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"ghost": str(tmp_path / "nonexistent")},
    )
    resolve_agents(config)
    assert len(config.agents) == 0


def test_resolve_agents_warning_message_includes_unregister_hint(tmp_path, caplog):
    """Loader warnings tell the user the exact command to fix the manifest."""
    import logging

    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        reset_manifest_warning_state,
        resolve_agents,
    )

    reset_manifest_warning_state()
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"ghost": str(tmp_path / "nonexistent")},
    )
    with caplog.at_level(logging.WARNING, logger="culture"):
        resolve_agents(config)

    messages = [r.getMessage() for r in caplog.records]
    assert any("culture agent unregister ghost" in m for m in messages)


def test_resolve_agents_warns_once_per_process(tmp_path, caplog):
    """Same broken manifest entry must not warn twice in one process."""
    import logging

    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        reset_manifest_warning_state,
        resolve_agents,
    )

    reset_manifest_warning_state()
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"ghost": str(tmp_path / "nonexistent")},
    )

    with caplog.at_level(logging.WARNING, logger="culture"):
        resolve_agents(config)
        first = len(caplog.records)
        resolve_agents(config)
        second = len(caplog.records)

    assert first == 1
    assert second == 1, "second resolve_agents should be silent"


def test_resolve_agents_suffix_mismatch_warns_with_unregister_hint(tmp_path, caplog):
    """When culture.yaml exists but doesn't declare the requested suffix, the
    warning still tells the user how to clean up the stale entry."""
    import logging

    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        reset_manifest_warning_state,
        resolve_agents,
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text("suffix: actual\nbackend: claude\n")

    reset_manifest_warning_state()
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"expected": str(proj)},
    )
    with caplog.at_level(logging.WARNING, logger="culture"):
        resolve_agents(config)

    messages = [r.getMessage() for r in caplog.records]
    assert any("culture agent unregister expected" in m for m in messages)
    assert config.agents == []


def test_reset_manifest_warning_state_re_enables_warning(tmp_path, caplog):
    """reset_manifest_warning_state lets a previously-warned entry warn again."""
    import logging

    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        reset_manifest_warning_state,
        resolve_agents,
    )

    reset_manifest_warning_state()
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"ghost": str(tmp_path / "nonexistent")},
    )
    with caplog.at_level(logging.WARNING, logger="culture"):
        resolve_agents(config)
        resolve_agents(config)
        reset_manifest_warning_state()
        resolve_agents(config)

    assert len(caplog.records) == 2


def test_resolve_agents_multi_agent_directory(tmp_path):
    """Two manifest entries pointing to same multi-agent directory."""
    from culture.config import ServerConfig, ServerConnConfig, resolve_agents

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text("""\
agents:
  - suffix: culture
    backend: claude
  - suffix: codex
    backend: codex
    model: gpt-5.4
""")

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"culture": str(proj), "codex": str(proj)},
    )
    resolve_agents(config)

    assert len(config.agents) == 2
    assert config.get_agent("spark-culture").backend == "claude"
    assert config.get_agent("spark-codex").backend == "codex"
    assert config.get_agent("spark-codex").model == "gpt-5.4"


def test_load_config_server_yaml(tmp_path):
    """load_config auto-detects server.yaml format."""
    from culture.config import load_config

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text("suffix: culture\nbackend: claude\n")

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text(f"""\
server:
  name: spark
  host: localhost
  port: 6667
agents:
  culture: {proj}
""")
    config = load_config(str(server_yaml))
    assert config.server.name == "spark"
    assert len(config.agents) == 1
    assert config.agents[0].nick == "spark-culture"


def test_load_config_legacy_agents_yaml(tmp_path):
    """load_config falls back to legacy agents.yaml parsing."""
    from culture.config import load_config

    agents_yaml = tmp_path / "agents.yaml"
    agents_yaml.write_text("""\
server:
  name: spark
  host: localhost
  port: 6667
agents:
  - nick: spark-culture
    directory: /tmp/work
    channels: ["#general"]
    model: claude-opus-4-6
""")
    config = load_config(str(agents_yaml))
    assert config.server.name == "spark"
    assert len(config.agents) == 1
    assert config.agents[0].nick == "spark-culture"


def test_load_config_or_default_missing(tmp_path):
    """Missing file with no fallback returns default config."""
    from culture.config import load_config_or_default

    # Pass a nonexistent fallback to avoid picking up real ~/.culture/agents.yaml
    config = load_config_or_default(
        str(tmp_path / "missing.yaml"),
        fallback=str(tmp_path / "also-missing.yaml"),
    )
    assert config.server.name == "culture"
    assert config.agents == []


def test_save_server_config(tmp_path):
    """save_server_config writes server.yaml atomically."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    path = tmp_path / "server.yaml"
    config = ServerConfig(
        server=ServerConnConfig(name="spark", host="10.0.0.1", port=6668),
        manifest={"culture": "/tmp/proj"},
    )
    save_server_config(str(path), config)

    loaded = load_server_config(str(path))
    assert loaded.server.name == "spark"
    assert loaded.server.host == "10.0.0.1"
    assert loaded.manifest == {"culture": "/tmp/proj"}


def test_save_culture_yaml_single(tmp_path):
    """Save single-agent culture.yaml."""
    from culture.config import AgentConfig, load_culture_yaml, save_culture_yaml

    agent = AgentConfig(suffix="myagent", backend="claude", model="claude-opus-4-6")
    save_culture_yaml(str(tmp_path), [agent])

    loaded = load_culture_yaml(str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0].suffix == "myagent"
    assert loaded[0].backend == "claude"


def test_save_culture_yaml_multi(tmp_path):
    """Save multi-agent culture.yaml."""
    from culture.config import AgentConfig, load_culture_yaml, save_culture_yaml

    agents = [
        AgentConfig(suffix="culture", backend="claude"),
        AgentConfig(suffix="codex", backend="codex", model="gpt-5.4"),
    ]
    save_culture_yaml(str(tmp_path), agents)

    loaded = load_culture_yaml(str(tmp_path))
    assert len(loaded) == 2
    assert loaded[0].suffix == "culture"
    assert loaded[1].suffix == "codex"


def test_save_culture_yaml_preserves_extras(tmp_path):
    """Backend-specific fields round-trip through extras."""
    from culture.config import AgentConfig, load_culture_yaml, save_culture_yaml

    agent = AgentConfig(
        suffix="daria",
        backend="acp",
        extras={"acp_command": ["opencode", "acp"]},
    )
    save_culture_yaml(str(tmp_path), [agent])

    loaded = load_culture_yaml(str(tmp_path))
    assert loaded[0].acp_command == ["opencode", "acp"]


def test_add_to_manifest(tmp_path):
    """Add entry to server.yaml manifest."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        add_to_manifest,
        load_server_config,
        save_server_config,
    )

    path = tmp_path / "server.yaml"
    config = ServerConfig(server=ServerConnConfig(name="spark"))
    save_server_config(str(path), config)

    add_to_manifest(str(path), "culture", "/tmp/proj")

    loaded = load_server_config(str(path))
    assert loaded.manifest == {"culture": "/tmp/proj"}


def test_add_to_manifest_collision(tmp_path):
    """Duplicate suffix raises ValueError."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        add_to_manifest,
        save_server_config,
    )

    path = tmp_path / "server.yaml"
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"culture": "/tmp/proj"},
    )
    save_server_config(str(path), config)

    with pytest.raises(ValueError, match="already registered"):
        add_to_manifest(str(path), "culture", "/tmp/other")


def test_remove_from_manifest(tmp_path):
    """Remove entry from manifest."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        remove_from_manifest,
        save_server_config,
    )

    path = tmp_path / "server.yaml"
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"culture": "/tmp/a", "daria": "/tmp/b"},
    )
    save_server_config(str(path), config)

    remove_from_manifest(str(path), "culture")

    loaded = load_server_config(str(path))
    assert loaded.manifest == {"daria": "/tmp/b"}


def test_remove_from_manifest_not_found(tmp_path):
    """Removing nonexistent suffix raises ValueError."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        remove_from_manifest,
        save_server_config,
    )

    path = tmp_path / "server.yaml"
    config = ServerConfig(server=ServerConnConfig(name="spark"))
    save_server_config(str(path), config)

    with pytest.raises(ValueError, match="not found"):
        remove_from_manifest(str(path), "ghost")
