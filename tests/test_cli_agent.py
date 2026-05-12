"""Tests for `culture.cli.agent` — `culture agent {create,join,start,stop,...}`.

`cli/agent.py` is the largest CLI module (1,123 LOC, 46 functions). Tests
focus on the dispatch + handler layer; integration-territory functions
(`_probe_server_connection`, `_start_foreground`, `_start_background`,
`_run_single_agent`, `_run_multi_agents`) are NOT exercised here —
`tests/test_integration_agent_runner.py` runs them against a real
`agentirc.ircd.IRCd`.

Mocking conventions (matches `tests/test_cli_server.py`):

- `culture.pidfile.{read_pid, write_pid, remove_pid, is_process_alive,
  rename_pid}` patched on the `culture.cli.agent` module (where they're
  imported at module top) or at source (`culture.pidfile.rename_pid` is
  lazy-imported inside `_cmd_rename` / `_cmd_assign`).
- `culture.config.*` patched on the `agent` module — these are the
  manifest/YAML accessors.
- `_send_ipc` patched directly when testing the IPC verb wrappers; the
  full `ipc_request` chain is patched only when testing `_send_ipc` itself.
- `_resolve_*` helpers patched when testing the orchestrators that
  compose them, so each layer is testable in isolation.
"""

from __future__ import annotations

import argparse

import pytest

from culture.cli import agent as agent_mod
from culture.config import AgentConfig, ServerConfig, ServerConnConfig

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"config": "~/.culture/server.yaml"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


def _make_agent(
    suffix: str = "ada",
    server: str = "spark",
    backend: str = "claude",
    archived: bool = False,
    directory: str = "/tmp/dir",
    **kwargs,
) -> AgentConfig:
    """Build an AgentConfig in the same shape `load_config` returns."""
    nick = f"{server}-{suffix}"
    return AgentConfig(
        suffix=suffix,
        backend=backend,
        nick=nick,
        directory=directory,
        channels=["#general"],
        archived=archived,
        **kwargs,
    )


def _make_config(*agents, server_name: str = "spark") -> ServerConfig:
    """Build a ServerConfig with the given agents pre-attached."""
    return ServerConfig(
        server=ServerConnConfig(name=server_name, host="127.0.0.1", port=6667),
        agents=list(agents),
    )


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_no_command_exits_with_usage(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod.dispatch(_args(agent_command=None))
        assert exc.value.code == 1
        assert "Usage: culture agent" in capsys.readouterr().err

    def test_unknown_command_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod.dispatch(_args(agent_command="frobnicate"))
        assert exc.value.code == 1
        assert "Unknown agent command" in capsys.readouterr().err

    def test_routes_to_handler(self, monkeypatch):
        called = []
        monkeypatch.setattr(agent_mod, "_cmd_status", called.append)
        agent_mod.dispatch(_args(agent_command="status"))
        assert len(called) == 1


# ---------------------------------------------------------------------------
# Backend config factories — parametrized
# ---------------------------------------------------------------------------


class TestBackendConfigFactories:
    def test_codex_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = agent_mod._create_codex_config("spark-ada")
        assert cfg.nick == "spark-ada"
        assert cfg.agent == "codex"
        assert cfg.directory == str(tmp_path)
        assert cfg.channels == ["#general"]

    def test_copilot_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = agent_mod._create_copilot_config("spark-ada")
        assert cfg.agent == "copilot"
        assert cfg.nick == "spark-ada"

    def test_acp_config_default_command(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(acp_command=None)
        cfg = agent_mod._create_acp_config("spark-ada", args)
        assert cfg.agent == "acp"
        assert cfg.acp_command == ["opencode", "acp"]

    def test_default_config_passes_backend_through(self):
        cfg = agent_mod._create_default_config("spark-ada", "claude")
        assert cfg.backend == "claude"
        assert cfg.nick == "spark-ada"

    @pytest.mark.parametrize(
        "backend,factory_name",
        [
            ("codex", "codex"),
            ("copilot", "copilot"),
            ("acp", "acp"),
            ("claude", "claude"),  # falls through to _create_default_config
        ],
    )
    def test_create_agent_config_dispatches_to_right_factory(
        self, backend, factory_name, tmp_path, monkeypatch
    ):
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(agent=backend, acp_command=None)
        cfg = agent_mod._create_agent_config(args, "spark-ada")
        # Each factory tags its output via `.agent` (back-compat alias for
        # .backend). For "claude" we get the default which uses backend.
        assert getattr(cfg, "agent", getattr(cfg, "backend", None)) == backend


class TestParseAcpCommand:
    def test_default_when_none(self):
        assert agent_mod._parse_acp_command(None) == ["opencode", "acp"]

    def test_json_list(self):
        assert agent_mod._parse_acp_command('["cline", "--acp"]') == ["cline", "--acp"]

    def test_falls_back_to_split_when_not_json(self):
        # Bare string → split on whitespace.
        assert agent_mod._parse_acp_command("cline --acp") == ["cline", "--acp"]

    def test_rejects_empty_list(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod._parse_acp_command("[]")
        assert exc.value.code == 1
        assert "must be a non-empty list" in capsys.readouterr().err

    def test_rejects_non_list_json(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod._parse_acp_command('"just a string"')
        assert exc.value.code == 1

    def test_rejects_mixed_types(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod._parse_acp_command('["cline", 42]')
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _check_existing_agent
# ---------------------------------------------------------------------------


class TestCheckExistingAgent:
    def test_passes_when_nick_unique(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        agent_mod._check_existing_agent(cfg, "spark-bob", "/tmp/cfg.yaml")  # no raise

    def test_removes_archived_duplicate(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada", archived=True))
        removed = []
        monkeypatch.setattr(
            agent_mod,
            "remove_manifest_agent",
            lambda path, nick: removed.append((path, nick)),
        )

        agent_mod._check_existing_agent(cfg, "spark-ada", "/tmp/cfg.yaml")

        assert removed == [("/tmp/cfg.yaml", "spark-ada")]
        assert "Replacing archived agent" in capsys.readouterr().out

    def test_exits_on_active_duplicate(self, capsys):
        cfg = _make_config(_make_agent(suffix="ada"))
        with pytest.raises(SystemExit) as exc:
            agent_mod._check_existing_agent(cfg, "spark-ada", "/tmp/cfg.yaml")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "already exists" in err
        assert "culture agent start spark-ada" in err


# ---------------------------------------------------------------------------
# _to_manifest_agent / _save_agent_to_directory
# ---------------------------------------------------------------------------


class TestToManifestAgent:
    def test_converts_codex_config(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        raw = agent_mod._create_codex_config("spark-ada")
        manifest = agent_mod._to_manifest_agent(raw, "ada")
        assert manifest.suffix == "ada"
        assert manifest.backend == "codex"

    def test_converts_acp_config_preserves_acp_command(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(acp_command='["my-acp", "--flag"]')
        raw = agent_mod._create_acp_config("spark-ada", args)
        manifest = agent_mod._to_manifest_agent(raw, "ada")
        assert manifest.extras["acp_command"] == ["my-acp", "--flag"]


class TestSaveAgentToDirectory:
    def test_creates_new_culture_yaml(self, tmp_path):
        directory = tmp_path / "proj"
        directory.mkdir()
        agent = AgentConfig(
            suffix="ada",
            backend="claude",
            directory=str(directory),
            channels=["#general"],
        )
        agent_mod._save_agent_to_directory(agent)
        assert (directory / "culture.yaml").exists()

    def test_merges_with_existing_culture_yaml(self, tmp_path):
        directory = tmp_path / "proj"
        directory.mkdir()
        # Pre-existing entry for `bob`
        (directory / "culture.yaml").write_text(
            "agents:\n" "  - suffix: bob\n" "    backend: claude\n"
        )
        agent = AgentConfig(
            suffix="ada",
            backend="codex",
            directory=str(directory),
            channels=["#general"],
        )

        agent_mod._save_agent_to_directory(agent)

        from culture.config import load_culture_yaml

        result = load_culture_yaml(str(directory))
        suffixes = sorted(a.suffix for a in result)
        assert suffixes == ["ada", "bob"]


# ---------------------------------------------------------------------------
# _cmd_create
# ---------------------------------------------------------------------------


class TestCmdCreate:
    def _create_args(self, **kwargs):
        defaults = dict(
            agent="claude",
            server=None,
            nick=None,
            acp_command=None,
            config="~/.culture/server.yaml",
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    @pytest.mark.parametrize("backend", ["claude", "codex", "copilot"])
    def test_create_each_backend(self, backend, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        added = []
        monkeypatch.setattr(
            agent_mod, "add_to_manifest", lambda path, suffix, dir: added.append((suffix, dir))
        )

        agent_mod._cmd_create(self._create_args(agent=backend, nick="ada"))

        assert (tmp_path / "culture.yaml").exists()
        assert added == [("ada", str(tmp_path))]
        out = capsys.readouterr().out
        assert "Agent created: spark-ada" in out

    def test_create_acp_backend(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "add_to_manifest", lambda *a, **kw: None)

        agent_mod._cmd_create(self._create_args(agent="acp", nick="ada", acp_command='["my-acp"]'))

        out = capsys.readouterr().out
        assert "Agent created: spark-ada" in out

    def test_uses_server_arg_when_provided(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        saved = []
        monkeypatch.setattr(
            agent_mod, "save_server_config", lambda path, c: saved.append((path, c.server.name))
        )
        monkeypatch.setattr(agent_mod, "add_to_manifest", lambda *a, **kw: None)

        agent_mod._cmd_create(self._create_args(server="thor", nick="ada"))

        # Server was renamed in-flight + persisted
        assert saved and saved[0][1] == "thor"

    def test_exits_when_agent_already_exists(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)

        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_create(self._create_args(nick="ada"))
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _cmd_join
# ---------------------------------------------------------------------------


class TestCmdJoin:
    def test_join_creates_then_starts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "add_to_manifest", lambda *a, **kw: None)
        started = []
        monkeypatch.setattr(agent_mod, "_cmd_start", started.append)

        args = argparse.Namespace(
            agent="claude",
            server=None,
            nick="ada",
            acp_command=None,
            config="~/.culture/server.yaml",
        )
        agent_mod._cmd_join(args)

        assert len(started) == 1
        # _cmd_join mutates args.nick to the full nick before invoking _cmd_start
        assert started[0].nick == "spark-ada"
        assert started[0].all is False


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


class TestResolutionHelpers:
    def test_get_active_agents_filters_archived(self):
        cfg = _make_config(
            _make_agent(suffix="ada"),
            _make_agent(suffix="dead", archived=True),
        )
        active = agent_mod._get_active_agents(cfg)
        assert [a.suffix for a in active] == ["ada"]

    def test_resolve_by_nick_returns_match(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        result = agent_mod._resolve_by_nick(cfg, "spark-ada")
        assert result.nick == "spark-ada"

    def test_resolve_by_nick_unknown_exits(self, capsys):
        cfg = _make_config()
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_by_nick(cfg, "spark-ghost")
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_resolve_by_nick_archived_exits(self, capsys):
        cfg = _make_config(_make_agent(suffix="dead", archived=True))
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_by_nick(cfg, "spark-dead")
        assert exc.value.code == 1
        assert "is archived" in capsys.readouterr().err

    def test_resolve_auto_single_agent(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        result = agent_mod._resolve_auto(cfg)
        assert [a.suffix for a in result] == ["ada"]

    def test_resolve_auto_no_agents(self, capsys):
        cfg = _make_config()
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_auto(cfg)
        assert exc.value.code == 1
        assert "No agents configured" in capsys.readouterr().err

    def test_resolve_auto_only_archived(self, capsys):
        cfg = _make_config(_make_agent(suffix="dead", archived=True))
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_auto(cfg)
        assert exc.value.code == 1
        assert "1 archived" in capsys.readouterr().err

    def test_resolve_auto_multiple_active_ambiguous(self, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), _make_agent(suffix="bob"))
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_auto(cfg)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "Multiple agents configured" in err
        assert "spark-ada" in err
        assert "spark-bob" in err


class TestResolveAgentsToStart:
    def test_all_flag(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        args = argparse.Namespace(all=True, nick=None)
        result = agent_mod._resolve_agents_to_start(cfg, args)
        assert [a.suffix for a in result] == ["ada"]

    def test_explicit_nick(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        args = argparse.Namespace(all=False, nick="spark-ada")
        result = agent_mod._resolve_agents_to_start(cfg, args)
        assert result[0].nick == "spark-ada"

    def test_auto(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        args = argparse.Namespace(all=False, nick=None)
        result = agent_mod._resolve_agents_to_start(cfg, args)
        assert len(result) == 1

    def test_empty_with_all_flag_exits(self, capsys):
        cfg = _make_config()
        args = argparse.Namespace(all=True, nick=None)
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_agents_to_start(cfg, args)
        assert exc.value.code == 1


class TestResolveAgentsToStop:
    def test_all_returns_everything_including_archived(self):
        cfg = _make_config(
            _make_agent(suffix="ada"),
            _make_agent(suffix="dead", archived=True),
        )
        args = argparse.Namespace(all=True, nick=None)
        result = agent_mod._resolve_agents_to_stop(cfg, args)
        assert len(result) == 2

    def test_explicit_nick(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        args = argparse.Namespace(all=False, nick="spark-ada")
        result = agent_mod._resolve_agents_to_stop(cfg, args)
        assert result[0].nick == "spark-ada"

    def test_unknown_nick_exits(self, capsys):
        cfg = _make_config()
        args = argparse.Namespace(all=False, nick="spark-ghost")
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_agents_to_stop(cfg, args)
        assert exc.value.code == 1

    def test_single_agent_auto(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        args = argparse.Namespace(all=False, nick=None)
        result = agent_mod._resolve_agents_to_stop(cfg, args)
        assert result[0].suffix == "ada"

    def test_no_agents_exits(self, capsys):
        cfg = _make_config()
        args = argparse.Namespace(all=False, nick=None)
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_agents_to_stop(cfg, args)
        assert exc.value.code == 1

    def test_multiple_agents_cwd_disambiguation(self, tmp_path, monkeypatch):
        proj_a = tmp_path / "proj_a"
        proj_b = tmp_path / "proj_b"
        proj_a.mkdir()
        proj_b.mkdir()
        cfg = _make_config(
            _make_agent(suffix="ada", directory=str(proj_a)),
            _make_agent(suffix="bob", directory=str(proj_b)),
        )
        monkeypatch.chdir(proj_a)
        args = argparse.Namespace(all=False, nick=None)
        result = agent_mod._resolve_agents_to_stop(cfg, args)
        assert [a.suffix for a in result] == ["ada"]

    def test_multiple_agents_no_cwd_match_exits(self, tmp_path, monkeypatch, capsys):
        cfg = _make_config(
            _make_agent(suffix="ada"),
            _make_agent(suffix="bob"),
        )
        monkeypatch.chdir(tmp_path)
        args = argparse.Namespace(all=False, nick=None)
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_agents_to_stop(cfg, args)
        assert exc.value.code == 1
        assert "Multiple agents configured" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _cmd_start dispatcher (skip body — covered by integration tests)
# ---------------------------------------------------------------------------


class TestCmdStartDispatcher:
    def test_routes_to_foreground(self, monkeypatch):
        cfg = _make_config(_make_agent(suffix="ada"))
        monkeypatch.setattr(agent_mod, "load_config", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "_probe_server_connection", lambda *a: None)
        called = []
        monkeypatch.setattr(agent_mod, "_start_foreground", lambda *a, **kw: called.append("fg"))
        monkeypatch.setattr(agent_mod, "_start_background", lambda *a, **kw: called.append("bg"))

        agent_mod._cmd_start(_args(all=False, nick="spark-ada", foreground=True))
        assert called == ["fg"]

    def test_routes_to_background_by_default(self, monkeypatch):
        cfg = _make_config(_make_agent(suffix="ada"))
        monkeypatch.setattr(agent_mod, "load_config", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "_probe_server_connection", lambda *a: None)
        called = []
        monkeypatch.setattr(agent_mod, "_start_foreground", lambda *a, **kw: called.append("fg"))
        monkeypatch.setattr(agent_mod, "_start_background", lambda *a, **kw: called.append("bg"))

        agent_mod._cmd_start(_args(all=False, nick="spark-ada", foreground=False))
        assert called == ["bg"]


# ---------------------------------------------------------------------------
# Backend daemon factories — parametrized
# ---------------------------------------------------------------------------


class TestBackendDaemonFactories:
    @pytest.mark.parametrize(
        "factory_attr,module_path,cls_name",
        [
            ("_create_codex_daemon", "cultureagent.clients.codex.daemon", "CodexDaemon"),
            ("_create_copilot_daemon", "cultureagent.clients.copilot.daemon", "CopilotDaemon"),
            ("_create_claude_daemon", "cultureagent.clients.claude.daemon", "AgentDaemon"),
        ],
    )
    def test_factory_invokes_daemon_class(self, factory_attr, module_path, cls_name, monkeypatch):
        cfg = _make_config(_make_agent(suffix="ada"))
        agent = cfg.agents[0]

        captured = {}

        class FakeDaemon:
            def __init__(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs

        monkeypatch.setitem(
            __import__("sys").modules, module_path, type(module_path, (), {cls_name: FakeDaemon})
        )
        # The factory function imports the class lazily — patch the import.
        import importlib

        mod = importlib.import_module(module_path)
        monkeypatch.setattr(mod, cls_name, FakeDaemon)

        factory = getattr(agent_mod, factory_attr)
        result = factory(cfg, agent)

        assert isinstance(result, FakeDaemon)

    def test_acp_daemon_coerces_non_acp_agent(self, monkeypatch):
        cfg = _make_config()
        agent = _make_agent(suffix="ada")  # claude agent

        captured = {}

        class FakeACPDaemon:
            def __init__(self, daemon_config, acp_agent):
                captured["agent"] = acp_agent

        import cultureagent.clients.acp.daemon as acp_daemon_mod

        monkeypatch.setattr(acp_daemon_mod, "ACPDaemon", FakeACPDaemon)

        agent_mod._create_acp_daemon(cfg, agent)

        # The coercion shim wraps the agent in an ACPAgentConfig
        coerced = captured["agent"]
        assert getattr(coerced, "agent", None) == "acp"

    def test_coerce_to_acp_agent_passthrough(self):
        from culture.clients.acp.config import AgentConfig as ACPAgentConfig

        acp = ACPAgentConfig(
            nick="spark-ada",
            agent="acp",
            acp_command=["x"],
            directory="/tmp",
            channels=["#general"],
        )
        result = agent_mod._coerce_to_acp_agent(acp)
        assert result is acp


class TestMakeBackendConfig:
    def test_copies_top_level_fields(self):
        cfg = _make_config()
        cfg.buffer_size = 999
        cfg.poll_interval = 7
        cfg.sleep_start = "01:00"
        cfg.sleep_end = "02:00"

        from culture.clients.codex.config import DaemonConfig as CodexDaemonConfig

        result = agent_mod._make_backend_config(cfg, CodexDaemonConfig)
        assert result.buffer_size == 999
        assert result.poll_interval == 7
        assert result.sleep_start == "01:00"
        assert result.sleep_end == "02:00"


# ---------------------------------------------------------------------------
# _cmd_stop
# ---------------------------------------------------------------------------


class TestCmdStop:
    def test_invokes_stop_agent_for_each_resolved(self, monkeypatch):
        cfg = _make_config(_make_agent(suffix="ada"), _make_agent(suffix="bob"))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        stopped = []
        monkeypatch.setattr(agent_mod, "stop_agent", stopped.append)

        agent_mod._cmd_stop(_args(all=True, nick=None))

        assert sorted(stopped) == ["spark-ada", "spark-bob"]


# ---------------------------------------------------------------------------
# _cmd_status
# ---------------------------------------------------------------------------


class TestCmdStatus:
    def test_no_agents_prints_message(self, monkeypatch, capsys):
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: _make_config())
        agent_mod._cmd_status(_args(nick=None, full=False, all=False))
        out = capsys.readouterr().out
        assert out  # NO_AGENTS_MSG was printed (string content varies)

    def test_specific_nick_calls_detail_printer(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        called = []
        monkeypatch.setattr(
            agent_mod, "print_agent_detail", lambda *a, **kw: called.append("detail")
        )

        agent_mod._cmd_status(_args(nick="spark-ada", full=False, all=False))
        assert called == ["detail"]

    def test_specific_nick_unknown_exits(self, monkeypatch, capsys):
        # Config has agents (so we don't short-circuit on "no agents") but
        # the requested nick isn't among them.
        cfg = _make_config(_make_agent(suffix="ada"))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_status(_args(nick="spark-ghost", full=False, all=False))
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err

    def test_all_flag_includes_archived(self, monkeypatch, capsys):
        cfg = _make_config(
            _make_agent(suffix="ada"),
            _make_agent(suffix="dead", archived=True),
        )
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        called_with = []
        monkeypatch.setattr(
            agent_mod,
            "print_agents_overview",
            lambda agents, full, show_archived_marker: called_with.append(len(agents)),
        )
        monkeypatch.setattr(agent_mod, "print_bot_listing", lambda **kw: None)

        agent_mod._cmd_status(_args(nick=None, full=False, all=True))

        assert called_with == [2]

    def test_no_active_agents_prints_archived_count(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="dead", archived=True))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)

        agent_mod._cmd_status(_args(nick=None, full=False, all=False))

        assert "1 archived" in capsys.readouterr().out


class TestPrintArchivedInfo:
    def test_active_agent_renders_nothing(self, capsys):
        agent_mod._print_archived_info(_make_agent(suffix="ada"))
        assert capsys.readouterr().out == ""

    def test_archived_agent_renders_block(self, capsys):
        agent = _make_agent(
            suffix="dead", archived=True, archived_at="2026-05-01", archived_reason="superseded"
        )
        agent_mod._print_archived_info(agent)
        out = capsys.readouterr().out
        assert "archived since 2026-05-01" in out
        assert "superseded" in out


# ---------------------------------------------------------------------------
# _cmd_rename / _cmd_assign
# ---------------------------------------------------------------------------


class TestCmdRename:
    def test_invalid_prefix_exits(self, monkeypatch, capsys):
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_rename(_args(nick="thor-ada", new_name="bob"))
        assert exc.value.code == 1
        assert "does not belong to server" in capsys.readouterr().err

    def test_invalid_new_name_exits(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(
            agent_mod,
            "sanitize_agent_name",
            lambda n: (_ for _ in ()).throw(ValueError("bad")),
        )

        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_rename(_args(nick="spark-ada", new_name="bad name!"))
        assert exc.value.code == 1

    def test_same_name_returns_silently(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "sanitize_agent_name", lambda n: n)

        agent_mod._cmd_rename(_args(nick="spark-ada", new_name="ada"))

        assert "already named" in capsys.readouterr().out

    def test_rename_manifest_error_exits(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "sanitize_agent_name", lambda n: n)
        monkeypatch.setattr(
            agent_mod,
            "rename_manifest_agent",
            lambda *a: (_ for _ in ()).throw(ValueError("collision")),
        )

        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_rename(_args(nick="spark-ada", new_name="bob"))
        assert exc.value.code == 1
        assert "collision" in capsys.readouterr().err

    def test_happy_path_renames_pidfile(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "sanitize_agent_name", lambda n: n)
        monkeypatch.setattr(agent_mod, "rename_manifest_agent", lambda *a: None)
        pid_renames = []
        monkeypatch.setattr(
            "culture.pidfile.rename_pid",
            lambda old, new: pid_renames.append((old, new)),
        )

        agent_mod._cmd_rename(_args(nick="spark-ada", new_name="bob"))

        assert pid_renames == [("agent-spark-ada", "agent-spark-bob")]
        assert "renamed: spark-ada → spark-bob" in capsys.readouterr().out


class TestCmdAssign:
    def test_invalid_prefix_exits(self, monkeypatch, capsys):
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_assign(_args(nick="thor-ada", server="bolt"))
        assert exc.value.code == 1

    def test_invalid_server_name_exits(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(
            agent_mod,
            "sanitize_agent_name",
            lambda n: (_ for _ in ()).throw(ValueError("bad")),
        )
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_assign(_args(nick="spark-ada", server="bad name!"))
        assert exc.value.code == 1

    def test_same_server_returns_silently(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "sanitize_agent_name", lambda n: n)

        agent_mod._cmd_assign(_args(nick="spark-ada", server="spark"))

        assert "already belongs to server" in capsys.readouterr().out

    def test_happy_path_reassigns(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"), server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "sanitize_agent_name", lambda n: n)
        monkeypatch.setattr(agent_mod, "rename_manifest_agent", lambda *a: None)
        pid_renames = []
        monkeypatch.setattr(
            "culture.pidfile.rename_pid",
            lambda old, new: pid_renames.append((old, new)),
        )

        agent_mod._cmd_assign(_args(nick="spark-ada", server="thor"))

        assert pid_renames == [("agent-spark-ada", "agent-thor-ada")]
        assert "reassigned" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# IPC dispatcher: _resolve_ipc_targets / _argparse_error / _send_ipc / _ipc_to_agents
# ---------------------------------------------------------------------------


class TestResolveIpcTargets:
    def test_rejects_both_nick_and_all(self, capsys):
        cfg = _make_config()
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_ipc_targets(
                cfg, argparse.Namespace(nick="spark-ada", all=True), "sleep"
            )
        assert exc.value.code == 2
        assert "cannot specify both" in capsys.readouterr().err

    def test_rejects_neither_nick_nor_all(self, capsys):
        cfg = _make_config()
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_ipc_targets(cfg, argparse.Namespace(nick=None, all=False), "sleep")
        assert exc.value.code == 2
        assert "required" in capsys.readouterr().err

    def test_all_returns_all_agents(self):
        cfg = _make_config(_make_agent(suffix="ada"), _make_agent(suffix="bob"))
        result = agent_mod._resolve_ipc_targets(
            cfg, argparse.Namespace(nick=None, all=True), "sleep"
        )
        assert len(result) == 2

    def test_nick_returns_single_agent(self):
        cfg = _make_config(_make_agent(suffix="ada"))
        result = agent_mod._resolve_ipc_targets(
            cfg, argparse.Namespace(nick="spark-ada", all=False), "sleep"
        )
        assert result[0].nick == "spark-ada"

    def test_unknown_nick_exits(self, capsys):
        cfg = _make_config()
        with pytest.raises(SystemExit) as exc:
            agent_mod._resolve_ipc_targets(
                cfg, argparse.Namespace(nick="spark-ghost", all=False), "sleep"
            )
        assert exc.value.code == 2


class TestArgparseError:
    def test_writes_prog_and_message_to_stderr(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod._argparse_error("culture agent sleep", "bad")
        assert exc.value.code == 2
        assert capsys.readouterr().err == "culture agent sleep: error: bad\n"


class TestSendIpc:
    def test_success_prints_action_verb(self, monkeypatch, capsys):
        async def _fake_ipc(sock, msg_type, **kw):
            return {"ok": True}

        monkeypatch.setattr(agent_mod, "ipc_request", _fake_ipc)
        monkeypatch.setattr(agent_mod, "agent_socket_path", lambda nick: f"/tmp/{nick}.sock")
        agent = _make_agent(suffix="ada")

        agent_mod._send_ipc(agent, "pause", "paused")

        assert "spark-ada: paused" in capsys.readouterr().out

    def test_failure_prints_to_stderr(self, monkeypatch, capsys):
        async def _fake_ipc(sock, msg_type, **kw):
            return None

        monkeypatch.setattr(agent_mod, "ipc_request", _fake_ipc)
        monkeypatch.setattr(agent_mod, "agent_socket_path", lambda nick: f"/tmp/{nick}.sock")
        agent = _make_agent(suffix="ada")

        agent_mod._send_ipc(agent, "pause", "paused")

        assert "failed" in capsys.readouterr().err


class TestIpcToAgents:
    def test_dispatches_per_target(self, monkeypatch):
        cfg = _make_config(_make_agent(suffix="ada"), _make_agent(suffix="bob"))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        sent = []
        monkeypatch.setattr(
            agent_mod,
            "_send_ipc",
            lambda agent, msg, verb: sent.append((agent.nick, msg, verb)),
        )

        agent_mod._ipc_to_agents(
            argparse.Namespace(all=True, nick=None, config="x"),
            "pause",
            "paused",
            "sleep",
        )

        assert sent == [
            ("spark-ada", "pause", "paused"),
            ("spark-bob", "pause", "paused"),
        ]


class TestIpcVerbs:
    @pytest.mark.parametrize(
        "verb_attr,msg_type,verb",
        [
            ("_cmd_sleep", "pause", "paused"),
            ("_cmd_wake", "resume", "resumed"),
        ],
    )
    def test_verb_wires_to_ipc_to_agents(self, monkeypatch, verb_attr, msg_type, verb):
        captured = {}

        def fake_ipc_to_agents(args, msg, action, name):
            captured.update(args=args, msg=msg, action=action, name=name)

        monkeypatch.setattr(agent_mod, "_ipc_to_agents", fake_ipc_to_agents)

        verb_fn = getattr(agent_mod, verb_attr)
        verb_fn(_args(all=True, nick=None))

        assert captured["msg"] == msg_type
        assert captured["action"] == verb


# ---------------------------------------------------------------------------
# _cmd_learn
# ---------------------------------------------------------------------------


class TestCmdLearn:
    def test_explicit_nick_unknown_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: _make_config())
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_learn(_args(nick="spark-ghost"))
        assert exc.value.code == 1

    def test_explicit_nick_existing_renders_prompt(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(
            "culture.learn_prompt.generate_learn_prompt",
            lambda **kw: f"PROMPT(nick={kw.get('nick')})",
        )
        agent_mod._cmd_learn(_args(nick="spark-ada"))
        assert "PROMPT(nick=spark-ada)" in capsys.readouterr().out

    def test_no_nick_falls_back_to_cwd_match(self, tmp_path, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada", directory=str(tmp_path)))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(
            "culture.learn_prompt.generate_learn_prompt",
            lambda **kw: f"NICK={kw.get('nick')}",
        )

        agent_mod._cmd_learn(_args(nick=None))

        assert "NICK=spark-ada" in capsys.readouterr().out

    def test_no_nick_no_cwd_match_uses_defaults(self, tmp_path, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada", directory="/other/path"))
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(
            "culture.learn_prompt.generate_learn_prompt",
            lambda **kw: f"NICK={kw.get('nick')}",
        )

        agent_mod._cmd_learn(_args(nick=None))

        # No nick in kwargs since no cwd match
        assert "NICK=None" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _cmd_message / _cmd_read
# ---------------------------------------------------------------------------


class TestCmdMessage:
    def test_empty_target_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_message(_args(target="  ", text="hi"))
        assert exc.value.code == 1
        assert "target nick cannot be empty" in capsys.readouterr().err

    def test_empty_text_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_message(_args(target="spark-bob", text="   "))
        assert exc.value.code == 1
        assert "message text cannot be empty" in capsys.readouterr().err

    def test_sends_via_observer(self, monkeypatch, capsys):
        sent = []

        class _Observer:
            async def send_message(self, target, text):
                sent.append((target, text))

        monkeypatch.setattr(agent_mod, "get_observer", lambda _cfg: _Observer())

        agent_mod._cmd_message(_args(target="spark-bob", text="hello"))

        assert sent == [("spark-bob", "hello")]
        assert "Sent to spark-bob" in capsys.readouterr().out


class TestCmdRead:
    def test_exits_with_not_implemented_message(self, capsys):
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_read(_args(target="spark-ada", limit=10))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "not yet implemented" in err
        assert "culture channel read" in err


# ---------------------------------------------------------------------------
# Archive lifecycle: _cmd_archive / _cmd_unarchive / _cmd_delete
# ---------------------------------------------------------------------------


class TestCmdArchive:
    def test_unknown_nick_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: _make_config())
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_archive(_args(nick="spark-ghost", reason=""))
        assert exc.value.code == 1

    def test_already_archived_returns(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="dead", archived=True))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)

        agent_mod._cmd_archive(_args(nick="spark-dead", reason=""))

        assert "already archived" in capsys.readouterr().out

    def test_archives_running_agent(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "read_pid", lambda name: 4242)
        monkeypatch.setattr(agent_mod, "is_process_alive", lambda pid: True)
        stopped = []
        monkeypatch.setattr(agent_mod, "stop_agent", stopped.append)
        archived = []
        monkeypatch.setattr(
            agent_mod,
            "archive_manifest_agent",
            lambda path, nick, reason="": archived.append((nick, reason)),
        )

        agent_mod._cmd_archive(_args(nick="spark-ada", reason="superseded"))

        assert stopped == ["spark-ada"]
        assert archived == [("spark-ada", "superseded")]
        out = capsys.readouterr().out
        assert "Agent archived: spark-ada" in out
        assert "Reason: superseded" in out


class TestCmdUnarchive:
    def test_happy_path(self, monkeypatch, capsys):
        unarchived = []
        monkeypatch.setattr(
            agent_mod,
            "unarchive_manifest_agent",
            lambda path, nick: unarchived.append(nick),
        )
        agent_mod._cmd_unarchive(_args(nick="spark-dead"))
        assert unarchived == ["spark-dead"]
        assert "Agent unarchived: spark-dead" in capsys.readouterr().out

    def test_unknown_or_not_archived_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(
            agent_mod,
            "unarchive_manifest_agent",
            lambda path, nick: (_ for _ in ()).throw(ValueError("not archived")),
        )
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_unarchive(_args(nick="spark-ghost"))
        assert exc.value.code == 1
        assert "not archived" in capsys.readouterr().err


class TestCmdDelete:
    def test_unknown_nick_exits(self, monkeypatch, capsys):
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: _make_config())
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_delete(_args(nick="spark-ghost"))
        assert exc.value.code == 1

    def test_stops_running_agent_then_deletes(self, monkeypatch, capsys):
        cfg = _make_config(_make_agent(suffix="ada"))
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(agent_mod, "read_pid", lambda name: 4242)
        monkeypatch.setattr(agent_mod, "is_process_alive", lambda pid: True)
        stopped = []
        monkeypatch.setattr(agent_mod, "stop_agent", stopped.append)
        removed = []
        monkeypatch.setattr(
            agent_mod,
            "remove_manifest_agent",
            lambda path, nick: removed.append(nick),
        )

        agent_mod._cmd_delete(_args(nick="spark-ada"))

        assert stopped == ["spark-ada"]
        assert removed == ["spark-ada"]
        assert "Agent deleted: spark-ada" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _cmd_unregister
# ---------------------------------------------------------------------------


class TestCmdUnregister:
    def test_full_nick_strips_prefix(self, monkeypatch, capsys):
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        removed = []
        monkeypatch.setattr(
            agent_mod,
            "remove_from_manifest",
            lambda path, suffix: removed.append(suffix),
        )

        agent_mod._cmd_unregister(_args(target="spark-ada"))

        assert removed == ["ada"]
        assert "Unregistered: spark-ada" in capsys.readouterr().out

    def test_bare_suffix(self, monkeypatch, capsys):
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        removed = []
        monkeypatch.setattr(
            agent_mod,
            "remove_from_manifest",
            lambda path, suffix: removed.append(suffix),
        )

        agent_mod._cmd_unregister(_args(target="ada"))

        assert removed == ["ada"]

    def test_error_exits(self, monkeypatch, capsys):
        cfg = _make_config(server_name="spark")
        monkeypatch.setattr(agent_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(
            agent_mod,
            "remove_from_manifest",
            lambda *a: (_ for _ in ()).throw(ValueError("not registered")),
        )
        with pytest.raises(SystemExit) as exc:
            agent_mod._cmd_unregister(_args(target="ada"))
        assert exc.value.code == 1
        assert "not registered" in capsys.readouterr().err
