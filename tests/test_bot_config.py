"""Tests for bot configuration loading and saving."""

import pytest

from agentirc.bots.config import BotConfig, load_bot_config, save_bot_config


@pytest.fixture
def bot_yaml(tmp_path):
    """Write a sample bot.yaml and return its path."""
    content = """\
bot:
  name: spark-ori-ghci
  owner: spark-ori
  description: GitHub CI notifier
  created: "2026-04-03"

trigger:
  type: webhook

output:
  channels:
    - "#builds"
  dm_owner: true
  mention: spark-claude
  template: |
    CI {body.action} for {body.repo}
  fallback: json
"""
    p = tmp_path / "bot.yaml"
    p.write_text(content)
    return p


def test_load_bot_config(bot_yaml):
    config = load_bot_config(bot_yaml)
    assert config.name == "spark-ori-ghci"
    assert config.owner == "spark-ori"
    assert config.description == "GitHub CI notifier"
    assert config.created == "2026-04-03"
    assert config.trigger_type == "webhook"
    assert config.channels == ["#builds"]
    assert config.dm_owner is True
    assert config.mention == "spark-claude"
    assert "CI {body.action}" in config.template
    assert config.fallback == "json"


def test_load_bot_config_defaults(tmp_path):
    p = tmp_path / "bot.yaml"
    p.write_text("bot:\n  name: minimal\n")
    config = load_bot_config(p)
    assert config.name == "minimal"
    assert config.owner == ""
    assert config.trigger_type == "webhook"
    assert config.channels == []
    assert config.dm_owner is False
    assert config.mention is None
    assert config.template is None
    assert config.fallback == "json"


def test_save_and_reload(tmp_path):
    config = BotConfig(
        name="spark-ori-test",
        owner="spark-ori",
        description="Test bot",
        created="2026-04-03",
        trigger_type="webhook",
        channels=["#test", "#builds"],
        dm_owner=True,
        mention="spark-claude",
        template="Job {body.status}",
        fallback="json",
    )
    p = tmp_path / "bot.yaml"
    save_bot_config(p, config)
    loaded = load_bot_config(p)
    assert loaded.name == config.name
    assert loaded.owner == config.owner
    assert loaded.channels == config.channels
    assert loaded.dm_owner == config.dm_owner
    assert loaded.mention == config.mention
    assert loaded.template == config.template


def test_save_creates_parent_dirs(tmp_path):
    config = BotConfig(name="nested-bot")
    p = tmp_path / "deep" / "path" / "bot.yaml"
    save_bot_config(p, config)
    assert p.exists()
    loaded = load_bot_config(p)
    assert loaded.name == "nested-bot"


def test_has_handler_false(tmp_path):
    config = BotConfig(name="no-handler")
    assert config.has_handler is False
