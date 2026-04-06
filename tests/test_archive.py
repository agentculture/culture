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
