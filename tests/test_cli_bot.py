"""Tests for `culture.cli.bot` — culture bot {create,start,stop,list,inspect,archive,unarchive}.

Each handler is invoked directly with an `argparse.Namespace`. The bot
filesystem lives at `culture.bots.config.BOTS_DIR` (a `~/.culture/bots`
path by default) — every test redirects it via `monkeypatch` to a
tmp_path so we never touch the user's real config.

`load_config_or_default` and `save_bot_config` are patched at the
import boundary (the handlers re-import them lazily inside each function)
so we don't have to write real YAML to test them.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from culture.cli import bot as bot_mod
from culture.cli.shared.constants import BOT_CONFIG_FILE

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bots_dir(monkeypatch, tmp_path):
    """Redirect culture.bots.config.BOTS_DIR to a tmp_path."""
    d = tmp_path / "bots"
    d.mkdir()
    monkeypatch.setattr("culture.bots.config.BOTS_DIR", d)
    return d


@dataclass
class _StubServerCfg:
    name: str = "spark"


@dataclass
class _StubConfig:
    server: _StubServerCfg = field(default_factory=_StubServerCfg)


@pytest.fixture
def stub_config(monkeypatch):
    """Patch `culture.config.load_config_or_default` to return a deterministic stub."""
    cfg = _StubConfig()
    # bot.py imports load_config_or_default at module top — patch on the module.
    monkeypatch.setattr(bot_mod, "load_config_or_default", lambda _p: cfg)
    return cfg


def _args(**kwargs) -> argparse.Namespace:
    """Build a minimal Namespace for any bot subcommand."""
    defaults = {"config": "~/.culture/server.yaml"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_bot_yaml(bots_dir: Path, name: str, **fields) -> Path:
    """Persist a minimal BotConfig YAML so `load_bot_config` can read it back."""
    from culture.bots.config import BotConfig, save_bot_config

    bot_dir = bots_dir / name
    bot_dir.mkdir(parents=True, exist_ok=True)
    cfg = BotConfig(name=name, owner=fields.pop("owner", "spark-ori"), **fields)
    yaml_path = bot_dir / BOT_CONFIG_FILE
    save_bot_config(yaml_path, cfg)
    return yaml_path


# ---------------------------------------------------------------------------
# dispatch (no subcommand)
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_dispatch_with_no_command_exits_with_usage(self, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod.dispatch(_args(bot_command=None))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Usage: culture bot" in err

    def test_dispatch_unknown_command(self, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod.dispatch(_args(bot_command="frobnicate"))
        assert exc.value.code == 1
        assert "Unknown bot command" in capsys.readouterr().err

    def test_dispatch_routes_to_list_handler(self, bots_dir, capsys):
        bot_mod.dispatch(_args(bot_command="list", owner=None, all=False))
        # bots_dir is empty → "No bots configured." path
        assert "No bots configured" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _should_include_bot
# ---------------------------------------------------------------------------


class TestShouldIncludeBot:
    def _make(self, **overrides):
        from culture.bots.config import BotConfig

        return BotConfig(**overrides)

    def test_owner_match_passes(self):
        cfg = self._make(owner="spark-ori")
        assert bot_mod._should_include_bot(cfg, "spark-ori", show_archived=False) is True

    def test_owner_mismatch_filters_out(self):
        cfg = self._make(owner="spark-ori")
        assert bot_mod._should_include_bot(cfg, "spark-claude", show_archived=False) is False

    def test_archived_filtered_out_by_default(self):
        cfg = self._make(owner="spark-ori", archived=True)
        assert bot_mod._should_include_bot(cfg, None, show_archived=False) is False

    def test_archived_included_when_show_archived(self):
        cfg = self._make(owner="spark-ori", archived=True)
        assert bot_mod._should_include_bot(cfg, None, show_archived=True) is True


# ---------------------------------------------------------------------------
# _load_and_filter_bots
# ---------------------------------------------------------------------------


class TestLoadAndFilterBots:
    def test_empty_when_bots_dir_missing(self, monkeypatch, tmp_path):
        ghost = tmp_path / "no-such-dir"
        monkeypatch.setattr("culture.bots.config.BOTS_DIR", ghost)
        assert bot_mod._load_and_filter_bots(_args(owner=None, all=False)) == []

    def test_returns_all_when_unfiltered(self, bots_dir):
        _make_bot_yaml(bots_dir, "spark-ori-alpha")
        _make_bot_yaml(bots_dir, "spark-ori-beta")
        bots = bot_mod._load_and_filter_bots(_args(owner=None, all=False))
        names = sorted(b.name for b in bots)
        assert names == ["spark-ori-alpha", "spark-ori-beta"]

    def test_respects_owner_filter(self, bots_dir):
        _make_bot_yaml(bots_dir, "spark-ori-alpha", owner="spark-ori")
        _make_bot_yaml(bots_dir, "spark-claude-beta", owner="spark-claude")
        bots = bot_mod._load_and_filter_bots(_args(owner="spark-ori", all=False))
        assert [b.name for b in bots] == ["spark-ori-alpha"]

    def test_archived_filtered_unless_all_flag_set(self, bots_dir):
        _make_bot_yaml(bots_dir, "spark-ori-active")
        _make_bot_yaml(bots_dir, "spark-ori-dead", archived=True)

        active_only = bot_mod._load_and_filter_bots(_args(owner=None, all=False))
        assert [b.name for b in active_only] == ["spark-ori-active"]

        with_archived = bot_mod._load_and_filter_bots(_args(owner=None, all=True))
        assert sorted(b.name for b in with_archived) == [
            "spark-ori-active",
            "spark-ori-dead",
        ]

    def test_skips_directories_without_yaml(self, bots_dir):
        _make_bot_yaml(bots_dir, "spark-ori-real")
        (bots_dir / "spark-ori-empty").mkdir()  # no bot.yaml inside
        bots = bot_mod._load_and_filter_bots(_args(owner=None, all=False))
        assert [b.name for b in bots] == ["spark-ori-real"]


# ---------------------------------------------------------------------------
# _bot_create
# ---------------------------------------------------------------------------


class TestBotCreate:
    def _create_args(self, name="ghci", **overrides):
        defaults = dict(
            name=name,
            owner="spark-ori",
            channels=["#ops"],
            trigger="webhook",
            mention=None,
            template=None,
            dm_owner=False,
            description="a bot",
            config="~/.culture/server.yaml",
        )
        defaults.update(overrides)
        return argparse.Namespace(**defaults)

    def test_rejects_empty_name(self, bots_dir, stub_config, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_create(self._create_args(name="   "))
        assert exc.value.code == 1
        assert "bot name cannot be empty" in capsys.readouterr().err

    def test_writes_yaml_with_server_owner_prefix(self, bots_dir, stub_config, capsys):
        bot_mod._bot_create(self._create_args(name="ghci", owner="spark-ori"))

        # Name is rewritten to <server>-<owner_suffix>-<name>
        bot_dir = bots_dir / "spark-ori-ghci"
        assert (bot_dir / BOT_CONFIG_FILE).is_file()
        out = capsys.readouterr().out
        assert "Bot 'spark-ori-ghci' created" in out
        assert "Owner:    spark-ori" in out

    def test_strips_server_prefix_from_owner_when_building_name(
        self, bots_dir, stub_config, capsys
    ):
        bot_mod._bot_create(self._create_args(name="ghci", owner="bare-owner"))

        assert (bots_dir / "spark-bare-owner-ghci" / BOT_CONFIG_FILE).is_file()

    def test_rejects_duplicate(self, bots_dir, stub_config, capsys):
        bot_mod._bot_create(self._create_args(name="ghci"))
        capsys.readouterr()  # drain
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_create(self._create_args(name="spark-ori-ghci"))
        assert exc.value.code == 1
        assert "already exists" in capsys.readouterr().err

    def test_renders_channels_and_mention_in_summary(self, bots_dir, stub_config, capsys):
        bot_mod._bot_create(
            self._create_args(
                name="ghci",
                channels=["#ops", "#general"],
                mention="@spark-claude",
            )
        )
        out = capsys.readouterr().out
        assert "Channels: #ops, #general" in out
        assert "Mentions: @spark-claude" in out


# ---------------------------------------------------------------------------
# _bot_start / _bot_stop
# ---------------------------------------------------------------------------


class TestBotStartStop:
    def test_start_unknown_bot_exits(self, bots_dir, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_start(_args(name="missing"))
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_start_existing_bot_prints_reload_notice(self, bots_dir, capsys):
        _make_bot_yaml(bots_dir, "spark-ori-ghci")
        bot_mod._bot_start(_args(name="spark-ori-ghci"))
        out = capsys.readouterr().out
        assert "spark-ori-ghci" in out
        assert "next server restart" in out

    def test_stop_unknown_bot_exits(self, bots_dir, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_stop(_args(name="missing"))
        assert exc.value.code == 1

    def test_stop_existing_bot_prints_unload_notice(self, bots_dir, capsys):
        _make_bot_yaml(bots_dir, "spark-ori-ghci")
        bot_mod._bot_stop(_args(name="spark-ori-ghci"))
        out = capsys.readouterr().out
        assert "unloaded on next server restart" in out


# ---------------------------------------------------------------------------
# _bot_list
# ---------------------------------------------------------------------------


class TestBotList:
    def test_empty_when_no_bots_dir(self, monkeypatch, tmp_path, capsys):
        ghost = tmp_path / "no-such-dir"
        monkeypatch.setattr("culture.bots.config.BOTS_DIR", ghost)
        bot_mod._bot_list(_args(owner=None, all=False))
        assert "No bots configured" in capsys.readouterr().out

    def test_empty_with_owner_filter_says_no_bots_for_owner(self, bots_dir, capsys):
        bot_mod._bot_list(_args(owner="spark-claude", all=False))
        assert "No bots found for owner 'spark-claude'" in capsys.readouterr().out

    def test_renders_table_with_each_bot(self, bots_dir, capsys):
        _make_bot_yaml(bots_dir, "spark-ori-alpha", channels=["#ops"])
        _make_bot_yaml(bots_dir, "spark-ori-beta", channels=[])

        bot_mod._bot_list(_args(owner=None, all=False))

        out = capsys.readouterr().out
        assert "NAME" in out and "TRIGGER" in out and "OWNER" in out
        assert "spark-ori-alpha" in out
        assert "spark-ori-beta" in out

    def test_archived_marker_appears_in_show_all_mode(self, bots_dir, capsys):
        _make_bot_yaml(bots_dir, "spark-ori-dead", archived=True)
        bot_mod._bot_list(_args(owner=None, all=True))
        assert "[archived]" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _bot_inspect
# ---------------------------------------------------------------------------


class TestBotInspect:
    def test_unknown_bot_exits(self, bots_dir, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_inspect(_args(name="missing"))
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_renders_full_bot_metadata(self, bots_dir, capsys):
        _make_bot_yaml(
            bots_dir,
            "spark-ori-ghci",
            description="GitHub CI watcher",
            channels=["#ops", "#general"],
            mention="@spark-claude",
            template="Hi {agent} — build {status}",
            dm_owner=True,
        )

        bot_mod._bot_inspect(_args(name="spark-ori-ghci"))

        out = capsys.readouterr().out
        assert "Bot:" in out and "spark-ori-ghci" in out
        assert "Owner:" in out and "spark-ori" in out
        assert "GitHub CI watcher" in out
        assert "Webhook URL:" in out and "spark-ori-ghci" in out
        assert "#ops" in out and "#general" in out
        assert "yes" in out  # DM Owner: yes
        assert "@spark-claude" in out
        assert "Template:" in out

    def test_truncates_long_template_first_line(self, bots_dir, capsys):
        long = "X" * 200
        _make_bot_yaml(bots_dir, "spark-ori-ghci", template=long)
        bot_mod._bot_inspect(_args(name="spark-ori-ghci"))
        out = capsys.readouterr().out
        assert "..." in out  # truncation marker

    def test_renders_archive_block_when_archived(self, bots_dir, capsys):
        _make_bot_yaml(
            bots_dir,
            "spark-ori-dead",
            archived=True,
            archived_at="2026-05-01",
            archived_reason="superseded",
        )
        bot_mod._bot_inspect(_args(name="spark-ori-dead"))
        out = capsys.readouterr().out
        assert "Archived:    yes" in out
        assert "2026-05-01" in out
        assert "superseded" in out


# ---------------------------------------------------------------------------
# _bot_archive / _bot_unarchive
# ---------------------------------------------------------------------------


class TestBotArchive:
    def test_unknown_bot_exits(self, bots_dir, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_archive(_args(name="missing", reason=""))
        assert exc.value.code == 1

    def test_marks_bot_archived_and_writes_back(self, bots_dir, capsys):
        from culture.bots.config import load_bot_config

        yaml_path = _make_bot_yaml(bots_dir, "spark-ori-ghci")
        bot_mod._bot_archive(_args(name="spark-ori-ghci", reason="superseded"))

        reloaded = load_bot_config(yaml_path)
        assert reloaded.archived is True
        assert reloaded.archived_reason == "superseded"
        assert reloaded.archived_at  # non-empty date string
        out = capsys.readouterr().out
        assert "Bot archived: spark-ori-ghci" in out
        assert "Reason: superseded" in out

    def test_already_archived_is_idempotent(self, bots_dir, capsys):
        _make_bot_yaml(bots_dir, "spark-ori-dead", archived=True)
        bot_mod._bot_archive(_args(name="spark-ori-dead", reason=""))
        out = capsys.readouterr().out
        assert "already archived" in out


class TestBotUnarchive:
    def test_unknown_bot_exits(self, bots_dir, capsys):
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_unarchive(_args(name="missing"))
        assert exc.value.code == 1

    def test_not_archived_exits(self, bots_dir, capsys):
        _make_bot_yaml(bots_dir, "spark-ori-active")
        with pytest.raises(SystemExit) as exc:
            bot_mod._bot_unarchive(_args(name="spark-ori-active"))
        assert exc.value.code == 1
        assert "not archived" in capsys.readouterr().err

    def test_restores_archived_bot(self, bots_dir, capsys):
        from culture.bots.config import load_bot_config

        yaml_path = _make_bot_yaml(
            bots_dir, "spark-ori-dead", archived=True, archived_at="2026-05-01"
        )
        bot_mod._bot_unarchive(_args(name="spark-ori-dead"))

        reloaded = load_bot_config(yaml_path)
        assert reloaded.archived is False
        assert reloaded.archived_at == ""
        assert reloaded.archived_reason == ""
        assert "Bot unarchived: spark-ori-dead" in capsys.readouterr().out
