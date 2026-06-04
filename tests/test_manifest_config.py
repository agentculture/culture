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
    load_config,
    load_config_or_default,
    load_culture_yaml,
    migrate_legacy_to_manifest,
    remove_from_manifest,
    remove_manifest_agent,
    rename_manifest_agent,
    rename_manifest_server,
    rename_worker_boss_prefix,
    save_culture_yaml,
    save_server_config,
    unarchive_manifest_agent,
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


# -----------------------------------------------------------------------
# v9.1.6 — boss-prefix migration (BUG 2 fix)
# -----------------------------------------------------------------------


def _make_worker(tmpdir, suffix: str, boss: str):
    """Create a per-worker directory with a culture.yaml that records
    a ``boss:`` field (the field BUG 2 leaves stale after an in-place
    ``server.name`` change)."""
    wdir = os.path.join(tmpdir, "helpers", suffix)
    os.makedirs(wdir, exist_ok=True)
    save_culture_yaml(
        wdir,
        [AgentConfig(suffix=suffix, backend="claude", extras={"boss": boss})],
    )
    return wdir


def test_rename_worker_boss_prefix_rewrites_matching(tmpdir):
    """Workers whose stored boss: prefix matches old_prefix get
    rewritten; workers whose prefix does NOT match are untouched.

    This is the load-bearing AD-2 multi-project safety guarantee:
    when migrating ``local → plenty``, a worker with
    ``boss: fork-rearch-qa`` (a DIFFERENT project boss) must not be
    accidentally relabeled."""
    w1 = _make_worker(tmpdir, "w1", "local-boss")
    w2 = _make_worker(tmpdir, "w2", "local-st4ck-boss")
    w_foreign = _make_worker(tmpdir, "wforeign", "fork-rearch-qa")

    server_path = os.path.join(tmpdir, "server.yaml")
    config = ServerConfig(
        server=ServerConnConfig(name="local"),
        manifest={"w1": w1, "w2": w2, "wforeign": w_foreign},
    )
    save_server_config(server_path, config)

    rewrites = rename_worker_boss_prefix(server_path, "local", "plenty")

    # Only the two ``local-*`` workers were touched.
    assert len(rewrites) == 2
    rewritten_dirs = {d for d, _, _ in rewrites}
    assert w1 in rewritten_dirs
    assert w2 in rewritten_dirs
    assert w_foreign not in rewritten_dirs

    # Verify on disk.
    assert load_culture_yaml(w1)[0].extras["boss"] == "plenty-boss"
    # Multi-hyphen suffix is preserved past the first hyphen.
    assert load_culture_yaml(w2)[0].extras["boss"] == "plenty-st4ck-boss"
    # AD-2 isolation: foreign project boss unchanged.
    assert load_culture_yaml(w_foreign)[0].extras["boss"] == "fork-rearch-qa"


def test_rename_worker_boss_prefix_idempotent(tmpdir):
    """Running the migration twice does not double-rewrite."""
    w1 = _make_worker(tmpdir, "w1", "local-boss")
    server_path = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_path,
        ServerConfig(
            server=ServerConnConfig(name="plenty"),
            manifest={"w1": w1},
        ),
    )

    first = rename_worker_boss_prefix(server_path, "local", "plenty")
    second = rename_worker_boss_prefix(server_path, "local", "plenty")

    assert len(first) == 1
    assert len(second) == 0
    assert load_culture_yaml(w1)[0].extras["boss"] == "plenty-boss"


def test_rename_worker_boss_prefix_no_op_when_prefixes_match(tmpdir):
    """Same old/new prefix is a no-op (defensive — operator runs it
    twice as a safety check)."""
    w1 = _make_worker(tmpdir, "w1", "plenty-boss")
    server_path = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_path,
        ServerConfig(
            server=ServerConnConfig(name="plenty"),
            manifest={"w1": w1},
        ),
    )

    assert rename_worker_boss_prefix(server_path, "plenty", "plenty") == []
    assert load_culture_yaml(w1)[0].extras["boss"] == "plenty-boss"


def test_rename_worker_boss_prefix_partial_prefix_does_not_match(tmpdir):
    """``local`` should NOT match ``local2-*`` — the migration only
    triggers on EXACT-prefix-plus-hyphen. Defends against
    ``local`` operators accidentally clobbering ``local2`` projects."""
    w = _make_worker(tmpdir, "w1", "local2-boss")
    server_path = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_path,
        ServerConfig(
            server=ServerConnConfig(name="local"),
            manifest={"w1": w},
        ),
    )

    rewrites = rename_worker_boss_prefix(server_path, "local", "plenty")
    assert rewrites == []
    assert load_culture_yaml(w)[0].extras["boss"] == "local2-boss"


def test_rename_manifest_server_auto_migrates_boss_prefix(tmpdir):
    """The full ``culture server rename`` flow must now (v9.1.6)
    migrate worker boss: fields atomically. Pre-9.1.6 the server name
    got renamed but workers were stranded with stale prefixes — the
    root cause of BUG 2."""
    w1 = _make_worker(tmpdir, "w1", "local-boss")
    server_path = os.path.join(tmpdir, "server.yaml")
    save_server_config(
        server_path,
        ServerConfig(
            server=ServerConnConfig(name="local"),
            manifest={"w1": w1},
        ),
    )

    old_name, renamed = rename_manifest_server(server_path, "plenty")

    assert old_name == "local"
    assert renamed == [("local-w1", "plenty-w1")]
    # Both server config AND worker culture.yaml were updated.
    assert load_config(server_path).server.name == "plenty"
    assert load_culture_yaml(w1)[0].extras["boss"] == "plenty-boss"
