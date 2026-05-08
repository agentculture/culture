"""Tests for entity archiving: agents, servers, and bots."""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

# -----------------------------------------------------------------------
# Config-level: archive/unarchive agent
# -----------------------------------------------------------------------


def test_archive_agent():
    """archive_agent sets archived fields and persists to YAML."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        archive_agent,
        load_config,
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
                    AgentConfig(nick="spark-claude", directory="/tmp/a", channels=["#general"]),
                ],
            ),
        )

        archive_agent(path, "spark-claude", reason="replaced by opus")

        loaded = load_config(path)
        agent = loaded.agents[0]
        assert agent.archived is True
        assert agent.archived_at != ""
        assert agent.archived_reason == "replaced by opus"
    finally:
        shutil.rmtree(tmpdir)


def test_archive_agent_not_found():
    """archive_agent raises ValueError for unknown nick."""
    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        archive_agent,
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
            archive_agent(path, "spark-nonexistent")
    finally:
        shutil.rmtree(tmpdir)


def test_unarchive_agent():
    """unarchive_agent clears archived fields."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        archive_agent,
        load_config,
        save_config,
        unarchive_agent,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(nick="spark-claude", directory="/tmp/a", channels=["#general"]),
                ],
            ),
        )

        archive_agent(path, "spark-claude", reason="test")
        unarchive_agent(path, "spark-claude")

        loaded = load_config(path)
        agent = loaded.agents[0]
        assert agent.archived is False
        assert agent.archived_at == ""
        assert agent.archived_reason == ""
    finally:
        shutil.rmtree(tmpdir)


def test_unarchive_agent_not_archived():
    """unarchive_agent raises ValueError if agent is not archived."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        save_config,
        unarchive_agent,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(nick="spark-claude", directory="/tmp/a", channels=["#general"]),
                ],
            ),
        )

        with pytest.raises(ValueError, match="not archived"):
            unarchive_agent(path, "spark-claude")
    finally:
        shutil.rmtree(tmpdir)


def test_unarchive_agent_not_found():
    """unarchive_agent raises ValueError for unknown nick."""
    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        save_config,
        unarchive_agent,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(server=ServerConnConfig(name="spark"), agents=[]),
        )

        with pytest.raises(ValueError, match="not found"):
            unarchive_agent(path, "spark-nonexistent")
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# Config-level: archive/unarchive server (cascade)
# -----------------------------------------------------------------------


def test_archive_server_cascades():
    """archive_server sets archived on server and all agents."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        archive_server,
        load_config,
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
                    AgentConfig(nick="spark-claude", directory="/tmp/a", channels=["#general"]),
                    AgentConfig(nick="spark-ori", directory="/tmp/b", channels=["#dev"]),
                ],
            ),
        )

        archived_nicks = archive_server(path, reason="decommissioned")

        assert set(archived_nicks) == {"spark-claude", "spark-ori"}

        loaded = load_config(path)
        assert loaded.server.archived is True
        assert loaded.server.archived_reason == "decommissioned"
        for agent in loaded.agents:
            assert agent.archived is True
            assert agent.archived_reason == "decommissioned"
    finally:
        shutil.rmtree(tmpdir)


def test_unarchive_server_cascades():
    """unarchive_server clears archived on server and all agents."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        archive_server,
        load_config,
        save_config,
        unarchive_server,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(nick="spark-claude", directory="/tmp/a", channels=["#general"]),
                    AgentConfig(nick="spark-ori", directory="/tmp/b", channels=["#dev"]),
                ],
            ),
        )

        archive_server(path, reason="test")
        unarchived_nicks = unarchive_server(path)

        assert set(unarchived_nicks) == {"spark-claude", "spark-ori"}

        loaded = load_config(path)
        assert loaded.server.archived is False
        assert loaded.server.archived_at == ""
        for agent in loaded.agents:
            assert agent.archived is False
            assert agent.archived_at == ""
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# Config-level: archive/unarchive bot
# -----------------------------------------------------------------------


def test_archive_bot():
    """Archive a bot by setting fields in bot.yaml."""
    from culture.bots.config import BotConfig, load_bot_config, save_bot_config

    tmpdir = tempfile.mkdtemp()
    try:
        yaml_path = Path(tmpdir) / "bot.yaml"
        config = BotConfig(
            name="spark-ori-ghci",
            owner="spark-ori",
            description="GitHub CI bot",
            created="2026-04-06",
            trigger_type="webhook",
            channels=["#general"],
        )
        save_bot_config(yaml_path, config)

        # Archive
        loaded = load_bot_config(yaml_path)
        loaded.archived = True
        loaded.archived_at = "2026-04-07"
        loaded.archived_reason = "no longer needed"
        save_bot_config(yaml_path, loaded)

        # Verify
        reloaded = load_bot_config(yaml_path)
        assert reloaded.archived is True
        assert reloaded.archived_at == "2026-04-07"
        assert reloaded.archived_reason == "no longer needed"
    finally:
        shutil.rmtree(tmpdir)


def test_unarchive_bot():
    """Unarchive a bot by clearing fields in bot.yaml."""
    from culture.bots.config import BotConfig, load_bot_config, save_bot_config

    tmpdir = tempfile.mkdtemp()
    try:
        yaml_path = Path(tmpdir) / "bot.yaml"
        config = BotConfig(
            name="spark-ori-ghci",
            owner="spark-ori",
            description="GitHub CI bot",
            created="2026-04-06",
            archived=True,
            archived_at="2026-04-07",
            archived_reason="test",
        )
        save_bot_config(yaml_path, config)

        # Unarchive
        loaded = load_bot_config(yaml_path)
        loaded.archived = False
        loaded.archived_at = ""
        loaded.archived_reason = ""
        save_bot_config(yaml_path, loaded)

        # Verify
        reloaded = load_bot_config(yaml_path)
        assert reloaded.archived is False
        assert reloaded.archived_at == ""
        assert reloaded.archived_reason == ""
    finally:
        shutil.rmtree(tmpdir)


def test_bot_config_backward_compat():
    """Loading a bot.yaml without archive fields defaults to not archived."""
    from culture.bots.config import load_bot_config

    tmpdir = tempfile.mkdtemp()
    try:
        yaml_path = os.path.join(tmpdir, "bot.yaml")
        # Write a legacy bot.yaml with no archive fields
        with open(yaml_path, "w") as f:
            f.write(
                "bot:\n"
                "  name: spark-ori-ghci\n"
                "  owner: spark-ori\n"
                "  description: GitHub CI bot\n"
                "  created: '2026-04-06'\n"
                "trigger:\n"
                "  type: webhook\n"
                "output:\n"
                "  channels:\n"
                "    - '#general'\n"
                "  dm_owner: false\n"
                "  fallback: json\n"
            )

        loaded = load_bot_config(yaml_path)
        assert loaded.archived is False
        assert loaded.archived_at == ""
        assert loaded.archived_reason == ""
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# Config-level: backward compat for agents.yaml
# -----------------------------------------------------------------------


def test_agent_config_backward_compat():
    """Loading agents.yaml without archive fields defaults to not archived."""
    from culture.clients.claude.config import load_config

    tmpdir = tempfile.mkdtemp()
    try:
        yaml_path = os.path.join(tmpdir, "agents.yaml")
        with open(yaml_path, "w") as f:
            f.write(
                "server:\n"
                "  name: spark\n"
                "  host: localhost\n"
                "  port: 6667\n"
                "agents:\n"
                "  - nick: spark-claude\n"
                "    directory: /tmp/a\n"
                "    channels:\n"
                "      - '#general'\n"
            )

        loaded = load_config(yaml_path)
        assert loaded.server.archived is False
        assert loaded.agents[0].archived is False
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# Config-level: archive roundtrip preserves other fields
# -----------------------------------------------------------------------


def test_archive_preserves_other_fields():
    """Archiving an agent preserves all other config fields."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        archive_agent,
        load_config,
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
                        agent="claude",
                        directory="/tmp/work",
                        channels=["#general", "#dev"],
                        model="claude-opus-4-6",
                        thinking="high",
                        system_prompt="Be helpful",
                        tags=["core", "main"],
                    ),
                ],
            ),
        )

        archive_agent(path, "spark-claude", reason="test")

        loaded = load_config(path)
        agent = loaded.agents[0]
        assert agent.archived is True
        # Original fields preserved
        assert agent.nick == "spark-claude"
        assert agent.agent == "claude"
        assert agent.directory == "/tmp/work"
        assert agent.channels == ["#general", "#dev"]
        assert agent.model == "claude-opus-4-6"
        assert agent.thinking == "high"
        assert agent.system_prompt == "Be helpful"
        assert agent.tags == ["core", "main"]
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# Config-level: remove_agent
# -----------------------------------------------------------------------


def test_remove_agent():
    """remove_agent removes the agent from config entirely."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        load_config,
        remove_agent,
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
                    AgentConfig(nick="spark-claude", directory="/tmp/a", channels=["#general"]),
                    AgentConfig(nick="spark-ori", directory="/tmp/b", channels=["#dev"]),
                ],
            ),
        )

        remove_agent(path, "spark-claude")

        loaded = load_config(path)
        assert len(loaded.agents) == 1
        assert loaded.agents[0].nick == "spark-ori"
    finally:
        shutil.rmtree(tmpdir)


def test_remove_agent_not_found():
    """remove_agent raises ValueError for unknown nick."""
    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        remove_agent,
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
            remove_agent(path, "spark-nonexistent")
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# Config-level: create overwrites archived agent
# -----------------------------------------------------------------------


def test_remove_then_add_replaces_archived_agent():
    """Removing an archived agent and re-adding produces a fresh entry."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        add_agent_to_config,
        archive_agent,
        load_config,
        remove_agent,
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
                        nick="spark-daria",
                        agent="claude",
                        directory="/tmp/daria",
                        channels=["#general"],
                    ),
                ],
            ),
        )

        # Archive, then remove and re-add with different backend
        archive_agent(path, "spark-daria", reason="switching backend")
        remove_agent(path, "spark-daria")

        new_agent = AgentConfig(
            nick="spark-daria",
            agent="acp",
            directory="/tmp/daria",
            channels=["#general"],
        )
        add_agent_to_config(path, new_agent, server_name="spark")

        loaded = load_config(path)
        assert len(loaded.agents) == 1
        agent = loaded.agents[0]
        assert agent.nick == "spark-daria"
        assert agent.agent == "acp"
        assert agent.archived is False
    finally:
        shutil.rmtree(tmpdir)


def test_create_blocked_by_active_agent():
    """add_agent_to_config raises ValueError when a non-archived agent exists."""
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        add_agent_to_config,
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
                        nick="spark-daria",
                        agent="claude",
                        directory="/tmp/daria",
                        channels=["#general"],
                    ),
                ],
            ),
        )

        new_agent = AgentConfig(
            nick="spark-daria",
            agent="acp",
            directory="/tmp/daria",
            channels=["#general"],
        )
        with pytest.raises(ValueError, match="already exists"):
            add_agent_to_config(path, new_agent, server_name="spark")
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# CLI-level: create overwrites archived, delete removes
# -----------------------------------------------------------------------


def test_cli_create_replaces_archived_agent(monkeypatch):
    """culture agent create replaces an archived agent via the CLI path."""
    from culture.cli import _build_parser
    from culture.cli.agent import _cmd_create
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        archive_agent,
        save_config,
    )
    from culture.config import load_config

    tmpdir = tempfile.mkdtemp()
    original_cwd = os.getcwd()
    try:
        # _create_acp_config / _create_default_config use os.getcwd() for the
        # new agent's `directory`, and _save_agent_to_directory then writes a
        # culture.yaml there. Without chdir, this test corrupts the real
        # culture.yaml at whatever directory pytest was invoked from.
        monkeypatch.chdir(tmpdir)
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(
                        nick="spark-daria",
                        agent="claude",
                        directory=os.path.join(tmpdir, "daria"),
                        channels=["#general"],
                    ),
                ],
            ),
        )
        archive_agent(path, "spark-daria", reason="switching backend")

        parser = _build_parser()
        args = parser.parse_args(
            [
                "agent",
                "create",
                "--server",
                "spark",
                "--nick",
                "daria",
                "--agent",
                "acp",
                "--config",
                path,
            ]
        )
        _cmd_create(args)

        loaded = load_config(path)
        assert len(loaded.agents) == 1
        agent = loaded.agents[0]
        assert agent.nick == "spark-daria"
        assert agent.backend == "acp"
        assert agent.archived is False
    finally:
        # Restore cwd before rmtree so we're not deleting the directory we're
        # standing in (Windows refuses, POSIX leaves a stale cwd).
        os.chdir(original_cwd)
        shutil.rmtree(tmpdir)


def test_cli_create_blocks_active_agent():
    """culture agent create exits non-zero for a non-archived agent."""
    from culture.cli import _build_parser
    from culture.cli.agent import _cmd_create
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
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
                        nick="spark-daria",
                        agent="claude",
                        directory=os.path.join(tmpdir, "daria"),
                        channels=["#general"],
                    ),
                ],
            ),
        )

        parser = _build_parser()
        args = parser.parse_args(
            [
                "agent",
                "create",
                "--server",
                "spark",
                "--nick",
                "daria",
                "--agent",
                "acp",
                "--config",
                path,
            ]
        )
        with pytest.raises(SystemExit) as exc_info:
            _cmd_create(args)
        assert exc_info.value.code == 1
    finally:
        shutil.rmtree(tmpdir)


def test_cli_delete_removes_agent():
    """culture agent delete removes the agent from config."""
    from culture.cli import _build_parser
    from culture.cli.agent import _cmd_delete
    from culture.clients.claude.config import (
        AgentConfig,
        DaemonConfig,
        ServerConnConfig,
        save_config,
    )
    from culture.config import load_config

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(
                server=ServerConnConfig(name="spark"),
                agents=[
                    AgentConfig(
                        nick="spark-daria",
                        agent="claude",
                        directory=os.path.join(tmpdir, "daria"),
                        channels=["#general"],
                    ),
                    AgentConfig(
                        nick="spark-ori",
                        agent="claude",
                        directory=os.path.join(tmpdir, "ori"),
                        channels=["#dev"],
                    ),
                ],
            ),
        )

        parser = _build_parser()
        args = parser.parse_args(
            [
                "agent",
                "delete",
                "spark-daria",
                "--config",
                path,
            ]
        )
        _cmd_delete(args)

        loaded = load_config(path)
        assert len(loaded.agents) == 1
        assert loaded.agents[0].nick == "spark-ori"
    finally:
        shutil.rmtree(tmpdir)


def test_cli_delete_not_found():
    """culture agent delete exits non-zero for unknown nick."""
    from culture.cli import _build_parser
    from culture.cli.agent import _cmd_delete
    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        save_config(
            path,
            DaemonConfig(server=ServerConnConfig(name="spark"), agents=[]),
        )

        parser = _build_parser()
        args = parser.parse_args(
            [
                "agent",
                "delete",
                "spark-nonexistent",
                "--config",
                path,
            ]
        )
        with pytest.raises(SystemExit) as exc_info:
            _cmd_delete(args)
        assert exc_info.value.code == 1
    finally:
        shutil.rmtree(tmpdir)


def test_remove_agent_preserves_other_agent_fields():
    """remove_agent with raw YAML preserves backend-specific fields on other agents."""
    import yaml as _yaml

    from culture.clients.claude.config import (
        DaemonConfig,
        ServerConnConfig,
        remove_agent,
        save_config,
    )

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        # Write a config with an ACP agent that has acp_command (unknown to claude schema)
        save_config(
            path,
            DaemonConfig(server=ServerConnConfig(name="spark"), agents=[]),
        )
        # Manually add agents with backend-specific fields
        with open(path) as f:
            raw = _yaml.safe_load(f)
        raw["agents"] = [
            {"nick": "spark-daria", "agent": "acp", "acp_command": ["opencode", "acp"]},
            {"nick": "spark-claude", "agent": "claude", "directory": "/tmp/c"},
        ]
        with open(path, "w") as f:
            _yaml.dump(raw, f, default_flow_style=False, sort_keys=False)

        remove_agent(path, "spark-claude")

        with open(path) as f:
            result = _yaml.safe_load(f)
        assert len(result["agents"]) == 1
        assert result["agents"][0]["nick"] == "spark-daria"
        assert result["agents"][0]["acp_command"] == ["opencode", "acp"]
    finally:
        shutil.rmtree(tmpdir)


# -----------------------------------------------------------------------
# Config mutations preserve backend-specific fields (#150)
# -----------------------------------------------------------------------


def _write_multi_backend_yaml(path):
    """Write a YAML file with agents from different backends."""
    import yaml as _yaml

    raw = {
        "server": {"name": "spark", "host": "127.0.0.1", "port": 6667},
        "agents": [
            {
                "nick": "spark-claude",
                "agent": "claude",
                "directory": "/tmp/c",
                "thinking": "medium",
            },
            {
                "nick": "spark-daria",
                "agent": "acp",
                "acp_command": ["opencode", "acp"],
                "directory": "/tmp/d",
            },
        ],
    }
    with open(path, "w") as f:
        _yaml.dump(raw, f, default_flow_style=False, sort_keys=False)


def test_archive_agent_preserves_backend_fields():
    """archive_agent preserves acp_command on sibling agents (#150)."""
    import yaml as _yaml

    from culture.clients.claude.config import archive_agent

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        _write_multi_backend_yaml(path)

        archive_agent(path, "spark-claude", reason="test")

        with open(path) as f:
            result = _yaml.safe_load(f)
        agents = {a["nick"]: a for a in result["agents"]}
        assert agents["spark-claude"]["archived"] is True
        assert agents["spark-daria"]["acp_command"] == ["opencode", "acp"]
    finally:
        shutil.rmtree(tmpdir)


def test_unarchive_agent_preserves_backend_fields():
    """unarchive_agent preserves acp_command on sibling agents (#150)."""
    import yaml as _yaml

    from culture.clients.claude.config import archive_agent, unarchive_agent

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        _write_multi_backend_yaml(path)

        archive_agent(path, "spark-claude", reason="test")
        unarchive_agent(path, "spark-claude")

        with open(path) as f:
            result = _yaml.safe_load(f)
        agents = {a["nick"]: a for a in result["agents"]}
        assert agents["spark-claude"]["archived"] is False
        assert agents["spark-daria"]["acp_command"] == ["opencode", "acp"]
    finally:
        shutil.rmtree(tmpdir)


def test_rename_agent_preserves_backend_fields():
    """rename_agent preserves acp_command on all agents (#150)."""
    import yaml as _yaml

    from culture.clients.claude.config import rename_agent

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        _write_multi_backend_yaml(path)

        rename_agent(path, "spark-claude", "spark-opus")

        with open(path) as f:
            result = _yaml.safe_load(f)
        agents = {a["nick"]: a for a in result["agents"]}
        assert "spark-opus" in agents
        assert "spark-claude" not in agents
        assert agents["spark-daria"]["acp_command"] == ["opencode", "acp"]
    finally:
        shutil.rmtree(tmpdir)


def test_rename_server_preserves_backend_fields():
    """rename_server preserves acp_command on all agents (#150)."""
    import yaml as _yaml

    from culture.clients.claude.config import rename_server

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        _write_multi_backend_yaml(path)

        rename_server(path, "thor")

        with open(path) as f:
            result = _yaml.safe_load(f)
        agents = {a["nick"]: a for a in result["agents"]}
        assert "thor-claude" in agents
        assert "thor-daria" in agents
        assert agents["thor-daria"]["acp_command"] == ["opencode", "acp"]
    finally:
        shutil.rmtree(tmpdir)


def test_archive_server_preserves_backend_fields():
    """archive_server preserves acp_command on all agents (#150)."""
    import yaml as _yaml

    from culture.clients.claude.config import archive_server

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        _write_multi_backend_yaml(path)

        archive_server(path, reason="maintenance")

        with open(path) as f:
            result = _yaml.safe_load(f)
        agents = {a["nick"]: a for a in result["agents"]}
        assert agents["spark-daria"]["acp_command"] == ["opencode", "acp"]
        assert agents["spark-daria"]["archived"] is True
        assert result["server"]["archived"] is True
    finally:
        shutil.rmtree(tmpdir)


def test_add_agent_preserves_backend_fields():
    """add_agent_to_config preserves acp_command on existing agents (#150)."""
    import yaml as _yaml

    from culture.clients.claude.config import AgentConfig, add_agent_to_config

    tmpdir = tempfile.mkdtemp()
    try:
        path = os.path.join(tmpdir, "agents.yaml")
        _write_multi_backend_yaml(path)

        new_agent = AgentConfig(nick="spark-new", agent="claude", directory="/tmp/n")
        add_agent_to_config(path, new_agent)

        with open(path) as f:
            result = _yaml.safe_load(f)
        agents = {a["nick"]: a for a in result["agents"]}
        assert "spark-new" in agents
        assert agents["spark-daria"]["acp_command"] == ["opencode", "acp"]
    finally:
        shutil.rmtree(tmpdir)
