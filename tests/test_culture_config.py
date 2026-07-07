import pytest


def test_agent_config_defaults():
    """AgentConfig has correct defaults and computed properties."""
    from culture_core.config import AgentConfig

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
    from culture_core.config import AgentConfig

    agent = AgentConfig(extras={"acp_command": ["cline", "--acp"]})
    assert agent.acp_command == ["cline", "--acp"]

    # Default when not in extras
    agent2 = AgentConfig()
    assert agent2.acp_command == ["opencode", "acp"]


def test_server_config_defaults():
    """ServerConfig has correct defaults."""
    from culture_core.config import ServerConfig, ServerConnConfig

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
    from culture_core.config import AgentConfig, ServerConfig

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
    from culture_core.config import DaemonConfig, ServerConfig

    assert DaemonConfig is ServerConfig


def test_load_culture_yaml_single_agent(tmp_path):
    """Load single-agent culture.yaml."""
    from culture_core.config import load_culture_yaml

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
    from culture_core.config import load_culture_yaml

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
    from culture_core.config import load_culture_yaml

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
    from culture_core.config import load_culture_yaml

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
    from culture_core.config import load_culture_yaml

    with pytest.raises(FileNotFoundError):
        load_culture_yaml(str(tmp_path))


def test_load_culture_yaml_suffix_not_found(tmp_path):
    """Requesting nonexistent suffix raises ValueError."""
    from culture_core.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("suffix: culture\nbackend: claude\n")

    with pytest.raises(ValueError, match="not found"):
        load_culture_yaml(str(tmp_path), suffix="nonexistent")


def test_load_server_config(tmp_path):
    """Load server.yaml with manifest."""
    from culture_core.config import load_server_config

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
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("server:\n  name: spark\n")
    config = load_server_config(str(server_yaml))
    assert config.server.name == "spark"
    assert config.server.host == "localhost"
    assert config.buffer_size == 500
    assert config.manifest == {}


def test_resolve_agents(tmp_path):
    """resolve_agents reads culture.yaml from manifest paths."""
    from culture_core.config import ServerConfig, ServerConnConfig, resolve_agents

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
    from culture_core.config import ServerConfig, ServerConnConfig, resolve_agents

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"ghost": str(tmp_path / "nonexistent")},
    )
    resolve_agents(config)
    assert len(config.agents) == 0


def test_resolve_agents_warning_message_includes_unregister_hint(tmp_path, caplog):
    """Loader warnings tell the user the exact command to fix the manifest."""
    import logging

    from culture_core.config import (
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
    assert any("culture agents unregister ghost" in m for m in messages)


def test_resolve_agents_warns_once_per_process(tmp_path, caplog):
    """Same broken manifest entry must not warn twice in one process."""
    import logging

    from culture_core.config import (
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

    from culture_core.config import (
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
    assert any("culture agents unregister expected" in m for m in messages)
    assert config.agents == []


def test_reset_manifest_warning_state_re_enables_warning(tmp_path, caplog):
    """reset_manifest_warning_state lets a previously-warned entry warn again."""
    import logging

    from culture_core.config import (
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
    from culture_core.config import ServerConfig, ServerConnConfig, resolve_agents

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
    from culture_core.config import load_config

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
    from culture_core.config import load_config

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
    from culture_core.config import load_config_or_default

    # Pass a nonexistent fallback to avoid picking up real ~/.culture/agents.yaml
    config = load_config_or_default(
        str(tmp_path / "missing.yaml"),
        fallback=str(tmp_path / "also-missing.yaml"),
    )
    assert config.server.name == "culture"
    assert config.agents == []


def test_save_server_config(tmp_path):
    """save_server_config writes server.yaml atomically."""
    from culture_core.config import (
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
    from culture_core.config import AgentConfig, load_culture_yaml, save_culture_yaml

    agent = AgentConfig(suffix="myagent", backend="claude", model="claude-opus-4-6")
    save_culture_yaml(str(tmp_path), [agent])

    loaded = load_culture_yaml(str(tmp_path))
    assert len(loaded) == 1
    assert loaded[0].suffix == "myagent"
    assert loaded[0].backend == "claude"


def test_save_culture_yaml_multi(tmp_path):
    """Save multi-agent culture.yaml."""
    from culture_core.config import AgentConfig, load_culture_yaml, save_culture_yaml

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
    from culture_core.config import AgentConfig, load_culture_yaml, save_culture_yaml

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
    from culture_core.config import (
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
    from culture_core.config import (
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
    from culture_core.config import (
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
    from culture_core.config import (
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


# -----------------------------------------------------------------------
# Token budgets (warn-only) + presence policy — resident-presence t2
# -----------------------------------------------------------------------


def test_agent_config_token_budget_defaults():
    """AgentConfig token-budget fields default to no budget / 80 percent."""
    from culture_core.config import AgentConfig

    agent = AgentConfig()
    assert agent.token_budget is None
    assert agent.token_budget_warn_pct == 80


def test_load_culture_yaml_token_budget_fields(tmp_path):
    """token_budget keys parse as typed fields; extras still work alongside."""
    from culture_core.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("""\
suffix: myagent
backend: claude
token_budget: 200000
token_budget_warn_pct: 75
custom_field: hello
""")
    agents = load_culture_yaml(str(tmp_path))
    assert agents[0].token_budget == 200000
    assert agents[0].token_budget_warn_pct == 75
    # Typed fields, not extras — and unknown keys still land in extras.
    assert "token_budget" not in agents[0].extras
    assert "token_budget_warn_pct" not in agents[0].extras
    assert agents[0].extras == {"custom_field": "hello"}


def test_load_culture_yaml_token_budget_defaults_when_absent(tmp_path):
    """culture.yaml without budget keys yields the field defaults."""
    from culture_core.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text("suffix: myagent\nbackend: claude\n")
    agents = load_culture_yaml(str(tmp_path))
    assert agents[0].token_budget is None
    assert agents[0].token_budget_warn_pct == 80


@pytest.mark.parametrize("bad", [0, -1, "many", 1.5, True])
def test_load_culture_yaml_token_budget_invalid_warns_and_degrades(tmp_path, bad, caplog):
    """Budget fields are warn-only observability config: an invalid
    token_budget logs a warning (naming the file, key, value, and valid
    range) and is reset to the default (None) — the agent still loads."""
    import logging

    import yaml

    from culture_core.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text(
        yaml.dump({"suffix": "myagent", "backend": "claude", "token_budget": bad})
    )
    with caplog.at_level(logging.WARNING, logger="culture"):
        agents = load_culture_yaml(str(tmp_path))

    assert len(agents) == 1
    assert agents[0].suffix == "myagent"
    assert agents[0].token_budget is None
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "token_budget" in m and "culture.yaml" in m and "positive integer" in m for m in messages
    )


@pytest.mark.parametrize("bad", [0, -5, 101, "80", 2.5, True])
def test_load_culture_yaml_token_budget_warn_pct_invalid_warns_and_degrades(tmp_path, bad, caplog):
    """token_budget_warn_pct outside 1..100 (or non-int) logs a warning and
    falls back to the default (80) — never a raise, the agent still loads."""
    import logging

    import yaml

    from culture_core.config import load_culture_yaml

    culture_yaml = tmp_path / "culture.yaml"
    culture_yaml.write_text(
        yaml.dump({"suffix": "myagent", "backend": "claude", "token_budget_warn_pct": bad})
    )
    with caplog.at_level(logging.WARNING, logger="culture"):
        agents = load_culture_yaml(str(tmp_path))

    assert len(agents) == 1
    assert agents[0].token_budget_warn_pct == 80
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "token_budget_warn_pct" in m and "culture.yaml" in m and "between 1 and 100" in m
        for m in messages
    )


def test_resolve_agents_invalid_token_budget_warns_and_loads(tmp_path, caplog):
    """A manifest entry with an invalid budget degrades (budget ignored) but
    the agent still loads — a budget typo must never drop an agent."""
    import logging

    from culture_core.config import (
        ServerConfig,
        ServerConnConfig,
        reset_manifest_warning_state,
        resolve_agents,
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text("suffix: culture\nbackend: claude\ntoken_budget: -1\n")

    reset_manifest_warning_state()
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"culture": str(proj)},
    )
    with caplog.at_level(logging.WARNING, logger="culture"):
        resolve_agents(config)

    assert len(config.agents) == 1
    agent = config.get_agent("spark-culture")
    assert agent is not None
    assert agent.token_budget is None
    messages = [r.getMessage() for r in caplog.records]
    assert any("token_budget" in m for m in messages)


def test_presence_config_defaults():
    """PresenceConfig defaults: 30s heartbeat, 90s stale-T."""
    from culture_core.config import PresenceConfig

    presence = PresenceConfig()
    assert presence.heartbeat_interval_seconds == 30
    assert presence.stale_after_seconds == 90


def test_server_config_presence_default_section():
    """ServerConfig carries a default presence section."""
    from culture_core.config import ServerConfig

    config = ServerConfig()
    assert config.presence.heartbeat_interval_seconds == 30
    assert config.presence.stale_after_seconds == 90


def test_load_server_config_presence_section(tmp_path):
    """server.yaml presence section parses into PresenceConfig."""
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("""\
server:
  name: spark

presence:
  heartbeat_interval_seconds: 10
  stale_after_seconds: 45
""")
    config = load_server_config(str(server_yaml))
    assert config.presence.heartbeat_interval_seconds == 10
    assert config.presence.stale_after_seconds == 45


def test_load_server_config_presence_defaults_when_absent(tmp_path):
    """server.yaml without a presence section gets the defaults."""
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("server:\n  name: spark\n")
    config = load_server_config(str(server_yaml))
    assert config.presence.heartbeat_interval_seconds == 30
    assert config.presence.stale_after_seconds == 90


@pytest.mark.parametrize("bad", [0, -3, "fast", 1.5, True])
def test_load_server_config_presence_heartbeat_invalid(tmp_path, bad):
    """Non-positive / non-int heartbeat_interval_seconds raises CultureError."""
    import yaml

    from culture_core.cli._errors import CultureError
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text(yaml.dump({"presence": {"heartbeat_interval_seconds": bad}}))
    with pytest.raises(
        CultureError, match=r"presence\.heartbeat_interval_seconds.*positive integer"
    ):
        load_server_config(str(server_yaml))


@pytest.mark.parametrize("bad", [0, -1, "soon", 2.5, True])
def test_load_server_config_presence_stale_invalid(tmp_path, bad):
    """Non-positive / non-int stale_after_seconds raises CultureError."""
    import yaml

    from culture_core.cli._errors import CultureError
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text(
        yaml.dump({"presence": {"heartbeat_interval_seconds": 30, "stale_after_seconds": bad}})
    )
    with pytest.raises(CultureError, match=r"presence\.stale_after_seconds.*positive integer"):
        load_server_config(str(server_yaml))


@pytest.mark.parametrize("stale", [30, 20])
def test_load_server_config_presence_stale_not_greater_than_heartbeat(tmp_path, stale):
    """stale_after_seconds must be strictly greater than the heartbeat interval."""
    import yaml

    from culture_core.cli._errors import CultureError
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text(
        yaml.dump({"presence": {"heartbeat_interval_seconds": 30, "stale_after_seconds": stale}})
    )
    with pytest.raises(
        CultureError,
        match=r"presence\.stale_after_seconds.*strictly greater than",
    ):
        load_server_config(str(server_yaml))


def test_load_server_config_presence_unknown_key(tmp_path):
    """An unknown presence key raises CultureError, not a TypeError traceback."""
    from culture_core.cli._errors import CultureError
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("presence:\n  heartbeat_seconds: 30\n")
    with pytest.raises(CultureError, match=r"presence"):
        load_server_config(str(server_yaml))


@pytest.mark.parametrize("bad", [False, [], 0])
def test_load_server_config_presence_falsy_non_mapping_rejected(tmp_path, bad):
    """presence: false / [] / 0 must raise the must-be-a-mapping CultureError,
    not silently coerce to the defaults like the old `or {}` did."""
    import yaml

    from culture_core.cli._errors import CultureError
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text(yaml.dump({"presence": bad}))
    with pytest.raises(CultureError, match=r"must be a mapping"):
        load_server_config(str(server_yaml))


def test_load_server_config_presence_null_section_gets_defaults(tmp_path):
    """An explicit empty `presence:` key (YAML null) means 'use defaults'."""
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("server:\n  name: spark\npresence:\n")
    config = load_server_config(str(server_yaml))
    assert config.presence.heartbeat_interval_seconds == 30
    assert config.presence.stale_after_seconds == 90


def test_load_server_config_presence_mixed_key_types_structured_error(tmp_path):
    """An unquoted numeric YAML key in the presence section must surface the
    structured unknown-key CultureError — not a TypeError from sorting
    mixed-type keys while building the error message."""
    from culture_core.cli._errors import CultureError
    from culture_core.config import load_server_config

    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("presence:\n  30: 5\n  stale_after_seconds: 60\n")
    with pytest.raises(CultureError, match=r"presence"):
        load_server_config(str(server_yaml))


def test_save_culture_yaml_token_budget_round_trip(tmp_path):
    """Budget fields survive a save/load round-trip as typed fields."""
    from culture_core.config import AgentConfig, load_culture_yaml, save_culture_yaml

    agent = AgentConfig(
        suffix="myagent",
        backend="claude",
        token_budget=200000,
        token_budget_warn_pct=75,
    )
    save_culture_yaml(str(tmp_path), [agent])

    loaded = load_culture_yaml(str(tmp_path))
    assert loaded[0].token_budget == 200000
    assert loaded[0].token_budget_warn_pct == 75
    assert "token_budget" not in loaded[0].extras
    assert "token_budget_warn_pct" not in loaded[0].extras


def test_save_culture_yaml_omits_default_token_budget(tmp_path):
    """Default budget values are not written to culture.yaml."""
    from culture_core.config import AgentConfig, save_culture_yaml

    agent = AgentConfig(suffix="myagent", backend="claude")
    save_culture_yaml(str(tmp_path), [agent])

    text = (tmp_path / "culture.yaml").read_text()
    assert "token_budget" not in text


def test_save_server_config_presence_round_trip(tmp_path):
    """Presence section survives a save/load round-trip."""
    from culture_core.config import (
        PresenceConfig,
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    path = tmp_path / "server.yaml"
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        presence=PresenceConfig(heartbeat_interval_seconds=15, stale_after_seconds=60),
    )
    save_server_config(str(path), config)

    loaded = load_server_config(str(path))
    assert loaded.presence.heartbeat_interval_seconds == 15
    assert loaded.presence.stale_after_seconds == 60
