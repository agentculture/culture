"""Tests for manifest-format config operations (server.yaml + culture.yaml)."""

import os
import shutil
import tempfile

import pytest
import yaml

from culture.config import (
    AgentConfig,
    ServerConfig,
    ServerConnConfig,
    add_to_manifest,
    archive_manifest_agent,
    archive_manifest_server,
    load_config,
    load_config_or_default,
    load_culture_yaml,
    migrate_legacy_to_manifest,
    remove_from_manifest,
    remove_manifest_agent,
    rename_manifest_agent,
    rename_manifest_server,
    save_culture_yaml,
    save_server_config,
    unarchive_manifest_agent,
    unarchive_manifest_server,
)


@pytest.fixture()
def tmpdir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


def _write_yaml(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False)


def _make_manifest_setup(tmpdir):
    """Create a manifest-format server.yaml with one agent directory."""
    agent_dir = os.path.join(tmpdir, "project")
    os.makedirs(agent_dir, exist_ok=True)

    # Write culture.yaml in agent directory
    save_culture_yaml(
        agent_dir,
        [
            AgentConfig(suffix="bot", backend="claude", channels=["#general"]),
        ],
    )

    # Write server.yaml with manifest
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"bot": agent_dir},
    )
    server_path = os.path.join(tmpdir, "server.yaml")
    save_server_config(server_path, config)
    return server_path, agent_dir


# -----------------------------------------------------------------------
# Auto-migration
# -----------------------------------------------------------------------


def test_load_config_auto_migrates_legacy(tmpdir):
    """Legacy format is auto-migrated to manifest format on load."""
    agent_dir = os.path.join(tmpdir, "project")
    os.makedirs(agent_dir, exist_ok=True)

    legacy_path = os.path.join(tmpdir, "agents.yaml")
    _write_yaml(
        legacy_path,
        {
            "server": {"name": "spark"},
            "agents": [
                {
                    "nick": "spark-bot",
                    "directory": agent_dir,
                    "agent": "claude",
                    "channels": ["#general"],
                },
            ],
        },
    )

    config = load_config(legacy_path)
    assert config.server.name == "spark"
    assert "bot" in config.manifest
    assert config.manifest["bot"] == agent_dir

    # culture.yaml should have been created
    agents = load_culture_yaml(agent_dir)
    assert len(agents) == 1
    assert agents[0].suffix == "bot"
    assert agents[0].backend == "claude"

    # The file should now be in manifest format (re-load without migration)
    with open(legacy_path) as f:
        raw = yaml.safe_load(f)
    assert isinstance(raw.get("agents"), dict)


def test_migrate_legacy_preserves_extras(tmpdir):
    """Extra fields (e.g. acp_command) survive migration."""
    agent_dir = os.path.join(tmpdir, "project")
    os.makedirs(agent_dir, exist_ok=True)

    legacy_path = os.path.join(tmpdir, "agents.yaml")
    _write_yaml(
        legacy_path,
        {
            "server": {"name": "spark"},
            "agents": [
                {
                    "nick": "spark-acp",
                    "directory": agent_dir,
                    "agent": "acp",
                    "channels": ["#general"],
                    "acp_command": ["opencode", "acp"],
                },
            ],
        },
    )

    migrate_legacy_to_manifest(legacy_path)

    agents = load_culture_yaml(agent_dir)
    assert agents[0].backend == "acp"
    assert agents[0].extras.get("acp_command") == ["opencode", "acp"]


# -----------------------------------------------------------------------
# Manifest CRUD: add / remove
# -----------------------------------------------------------------------


def test_add_to_manifest_creates_entry(tmpdir):
    """add_to_manifest adds a suffix→directory mapping."""
    server_path = os.path.join(tmpdir, "server.yaml")
    _write_yaml(server_path, {"server": {"name": "spark"}, "agents": {}})

    add_to_manifest(server_path, "bot", "/tmp/bot")

    with open(server_path) as f:
        raw = yaml.safe_load(f)
    assert raw["agents"]["bot"] == "/tmp/bot"


def test_add_to_manifest_duplicate_raises(tmpdir):
    """Adding a suffix that already exists raises ValueError."""
    server_path = os.path.join(tmpdir, "server.yaml")
    _write_yaml(server_path, {"server": {"name": "spark"}, "agents": {"bot": "/tmp/bot"}})

    with pytest.raises(ValueError, match="already registered"):
        add_to_manifest(server_path, "bot", "/tmp/other")


def test_remove_manifest_agent_by_nick(tmpdir):
    """remove_manifest_agent removes the agent from the manifest."""
    server_path, _ = _make_manifest_setup(tmpdir)

    remove_manifest_agent(server_path, "spark-bot")

    with open(server_path) as f:
        raw = yaml.safe_load(f)
    assert "bot" not in raw.get("agents", {})


def test_remove_manifest_agent_not_found(tmpdir):
    """Removing a nonexistent agent raises ValueError."""
    server_path, _ = _make_manifest_setup(tmpdir)

    with pytest.raises(ValueError, match="not found"):
        remove_manifest_agent(server_path, "spark-nonexistent")


# -----------------------------------------------------------------------
# Archive / Unarchive
# -----------------------------------------------------------------------


def test_archive_manifest_agent(tmpdir):
    """archive_manifest_agent sets archived flag in culture.yaml."""
    server_path, agent_dir = _make_manifest_setup(tmpdir)

    archive_manifest_agent(server_path, "spark-bot", reason="testing")

    agents = load_culture_yaml(agent_dir)
    assert agents[0].archived is True
    assert agents[0].archived_reason == "testing"
    assert agents[0].archived_at != ""


def test_unarchive_manifest_agent(tmpdir):
    """unarchive_manifest_agent clears archived flag."""
    server_path, agent_dir = _make_manifest_setup(tmpdir)

    # Archive first
    archive_manifest_agent(server_path, "spark-bot")

    # Then unarchive
    unarchive_manifest_agent(server_path, "spark-bot")

    agents = load_culture_yaml(agent_dir)
    assert agents[0].archived is False
    assert agents[0].archived_at == ""
    assert agents[0].archived_reason == ""


def test_unarchive_not_archived_raises(tmpdir):
    """Unarchiving an agent that isn't archived raises ValueError."""
    server_path, _ = _make_manifest_setup(tmpdir)

    with pytest.raises(ValueError, match="not archived"):
        unarchive_manifest_agent(server_path, "spark-bot")


# -----------------------------------------------------------------------
# Rename
# -----------------------------------------------------------------------


def test_rename_manifest_agent(tmpdir):
    """rename_manifest_agent updates manifest key and culture.yaml suffix."""
    server_path, agent_dir = _make_manifest_setup(tmpdir)

    rename_manifest_agent(server_path, "spark-bot", "spark-newbot")

    # Manifest updated
    with open(server_path) as f:
        raw = yaml.safe_load(f)
    assert "newbot" in raw["agents"]
    assert "bot" not in raw["agents"]

    # culture.yaml suffix updated
    agents = load_culture_yaml(agent_dir)
    assert agents[0].suffix == "newbot"


def test_rename_manifest_agent_collision(tmpdir):
    """Renaming to an existing suffix raises ValueError."""
    server_path, _ = _make_manifest_setup(tmpdir)

    # Add a second agent
    agent_dir2 = os.path.join(tmpdir, "project2")
    os.makedirs(agent_dir2, exist_ok=True)
    save_culture_yaml(
        agent_dir2,
        [
            AgentConfig(suffix="other", backend="claude"),
        ],
    )
    add_to_manifest(server_path, "other", agent_dir2)

    with pytest.raises(ValueError, match="already exists"):
        rename_manifest_agent(server_path, "spark-bot", "spark-other")


# -----------------------------------------------------------------------
# Server rename
# -----------------------------------------------------------------------


def test_rename_manifest_server(tmpdir):
    """rename_manifest_server changes server name and reports nick changes."""
    server_path, _ = _make_manifest_setup(tmpdir)

    old_name, renamed = rename_manifest_server(server_path, "thor")

    assert old_name == "spark"
    assert ("spark-bot", "thor-bot") in renamed

    # Verify server.yaml updated
    with open(server_path) as f:
        raw = yaml.safe_load(f)
    assert raw["server"]["name"] == "thor"


def test_rename_manifest_server_noop(tmpdir):
    """Renaming to the same name is a no-op."""
    server_path, _ = _make_manifest_setup(tmpdir)

    old_name, renamed = rename_manifest_server(server_path, "spark")

    assert old_name == "spark"
    assert renamed == []


# -----------------------------------------------------------------------
# Load manifest format directly
# -----------------------------------------------------------------------


def test_load_manifest_format(tmpdir):
    """load_config properly loads manifest-format server.yaml."""
    server_path, agent_dir = _make_manifest_setup(tmpdir)

    config = load_config(server_path)

    assert config.server.name == "spark"
    assert len(config.agents) == 1
    assert config.agents[0].nick == "spark-bot"
    assert config.agents[0].directory == agent_dir


def test_load_config_or_default_missing(tmpdir):
    """Missing config returns default ServerConfig."""
    path = os.path.join(tmpdir, "nonexistent.yaml")
    config = load_config_or_default(path, fallback=os.path.join(tmpdir, "also-missing.yaml"))
    assert config.server.name == "culture"
    assert config.agents == []


# ---------------------------------------------------------------------------
# Phase 4a — archive_manifest_server / unarchive_manifest_server
# ---------------------------------------------------------------------------


def _bootstrap_server_with_agent(tmpdir, server_name="spark", suffix="ada"):
    """Create a server.yaml with one registered agent + a per-directory culture.yaml.

    Returns (server_yaml_path, agent_directory).
    """
    agent_dir = os.path.join(tmpdir, "agent_dir")
    os.makedirs(agent_dir)
    with open(os.path.join(agent_dir, "culture.yaml"), "w", encoding="utf-8") as f:
        f.write(
            yaml.safe_dump(
                {
                    "agents": [
                        {"suffix": suffix, "backend": "claude", "channels": ["#general"]},
                    ]
                }
            )
        )

    server_yaml = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_yaml,
        ServerConfig(
            server=ServerConnConfig(name=server_name),
            manifest={suffix: agent_dir},
        ),
    )
    return server_yaml, agent_dir


def test_archive_manifest_server_archives_agents_and_server(tmpdir):
    server_yaml, agent_dir = _bootstrap_server_with_agent(tmpdir)

    archived = archive_manifest_server(server_yaml, reason="cleanup")

    assert archived == ["spark-ada"]

    # The server-level config is now flagged archived.
    cfg = load_config(server_yaml)
    assert cfg.server.archived is True
    assert cfg.server.archived_reason == "cleanup"
    assert cfg.server.archived_at  # non-empty date

    # The per-directory culture.yaml has the agent archived too.
    agents = load_culture_yaml(agent_dir)
    assert agents and agents[0].archived is True
    assert agents[0].archived_reason == "cleanup"


def test_archive_manifest_server_skips_already_archived_agents(tmpdir):
    server_yaml, agent_dir = _bootstrap_server_with_agent(tmpdir)
    # Pre-archive the agent manually.
    agents = load_culture_yaml(agent_dir)
    agents[0].archived = True
    agents[0].archived_at = "2025-01-01"
    save_culture_yaml(agent_dir, agents)

    archived = archive_manifest_server(server_yaml, reason="cleanup")
    # No new nicks — agent was already archived.
    assert archived == []


def test_archive_manifest_server_handles_missing_culture_yaml(tmpdir):
    """If a manifest entry points to a directory whose culture.yaml is gone,
    archive_manifest_server should still archive the server itself."""
    agent_dir = os.path.join(tmpdir, "ghost")  # directory doesn't exist
    server_yaml = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_yaml,
        ServerConfig(
            server=ServerConnConfig(name="spark"),
            manifest={"ghost": agent_dir},
        ),
    )

    archived = archive_manifest_server(server_yaml)
    # No agent nicks reported (yaml file missing) — but the server is archived.
    assert archived == []
    cfg = load_config(server_yaml)
    assert cfg.server.archived is True


def test_unarchive_manifest_server_restores_agents_and_server(tmpdir):
    server_yaml, agent_dir = _bootstrap_server_with_agent(tmpdir)
    archive_manifest_server(server_yaml, reason="cleanup")

    unarchived = unarchive_manifest_server(server_yaml)

    assert unarchived == ["spark-ada"]
    cfg = load_config(server_yaml)
    assert cfg.server.archived is False
    assert cfg.server.archived_at == ""
    assert cfg.server.archived_reason == ""

    agents = load_culture_yaml(agent_dir)
    assert agents and agents[0].archived is False
    assert agents[0].archived_at == ""


def test_unarchive_manifest_server_returns_empty_when_nothing_archived(tmpdir):
    server_yaml, _ = _bootstrap_server_with_agent(tmpdir)
    # Never archived
    unarchived = unarchive_manifest_server(server_yaml)
    assert unarchived == []


def test_unarchive_manifest_server_skips_missing_culture_yaml(tmpdir):
    """If a manifest entry points to a missing directory, unarchive still flips the server."""
    agent_dir = os.path.join(tmpdir, "ghost")
    server_yaml = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_yaml,
        ServerConfig(
            server=ServerConnConfig(name="spark", archived=True, archived_at="2025-01-01"),
            manifest={"ghost": agent_dir},
        ),
    )

    unarchived = unarchive_manifest_server(server_yaml)
    assert unarchived == []
    cfg = load_config(server_yaml)
    assert cfg.server.archived is False


def test_load_legacy_config_direct(tmpdir):
    """_load_legacy_config parses the old list-of-dicts agents.yaml format directly."""
    from culture.config import _load_legacy_config

    legacy_path = os.path.join(tmpdir, "agents.yaml")
    with open(legacy_path, "w", encoding="utf-8") as f:
        f.write(
            yaml.safe_dump(
                {
                    "server": {"name": "spark", "host": "1.2.3.4", "port": 7000},
                    "buffer_size": 250,
                    "poll_interval": 30,
                    "sleep_start": "22:00",
                    "sleep_end": "07:00",
                    "agents": [
                        {
                            "nick": "spark-ada",
                            "directory": "/tmp/ada",
                            "agent": "codex",  # legacy field name → backend
                            "channels": ["#ops"],
                            "custom_extra": "preserved",
                        }
                    ],
                }
            )
        )

    config = _load_legacy_config(legacy_path)

    assert config.server.name == "spark"
    assert config.server.host == "1.2.3.4"
    assert config.server.port == 7000
    assert config.buffer_size == 250
    assert config.poll_interval == 30
    assert config.sleep_start == "22:00"
    assert config.sleep_end == "07:00"
    assert len(config.agents) == 1
    agent = config.agents[0]
    # Legacy `agent` key → `backend` field
    assert agent.backend == "codex"
    # Unknown keys go into extras
    assert agent.extras == {"custom_extra": "preserved"}


def test_load_legacy_config_empty_agents(tmpdir):
    """An agents.yaml with no `agents` key parses to a ServerConfig with []."""
    from culture.config import _load_legacy_config

    legacy_path = os.path.join(tmpdir, "agents.yaml")
    with open(legacy_path, "w", encoding="utf-8") as f:
        f.write(yaml.safe_dump({"server": {"name": "spark"}}))

    config = _load_legacy_config(legacy_path)
    assert config.server.name == "spark"
    assert config.agents == []


def test_archive_manifest_server_with_extra_unrelated_agent_in_directory(tmpdir):
    """A culture.yaml may contain agents for multiple suffixes; archive only
    flips the ones whose suffix matches a manifest entry."""
    agent_dir = os.path.join(tmpdir, "agent_dir")
    os.makedirs(agent_dir)
    with open(os.path.join(agent_dir, "culture.yaml"), "w", encoding="utf-8") as f:
        f.write(
            yaml.safe_dump(
                {
                    "agents": [
                        {"suffix": "ada", "backend": "claude"},
                        {"suffix": "bob", "backend": "claude"},  # NOT in manifest
                    ]
                }
            )
        )
    server_yaml = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_yaml,
        ServerConfig(
            server=ServerConnConfig(name="spark"),
            manifest={"ada": agent_dir},  # only ada is registered
        ),
    )

    archived = archive_manifest_server(server_yaml)
    assert archived == ["spark-ada"]

    agents = load_culture_yaml(agent_dir)
    by_suffix = {a.suffix: a for a in agents}
    assert by_suffix["ada"].archived is True
    # bob is not in the manifest, so it stays untouched
    assert by_suffix["bob"].archived is False
