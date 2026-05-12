"""Tests for `culture.cli.mesh` — `culture mesh {overview,setup,update,console}`.

Each handler is invoked directly with an `argparse.Namespace`. The OS
surface (subprocess, sockets, systemd) is monkeypatched at the module
boundary so the suite is hermetic. `_install_mesh_services` is excluded
from coverage by `# pragma: no cover` in production code (systemd is
Linux + root only), and `os.execvp` is excluded the same way.

The tests do NOT exercise:
- the `--serve` web dashboard path (long-running aiohttp server)
- `_install_mesh_services` (excluded)
- the `os.execvp` re-exec inside `_upgrade_culture_package` (excluded)
"""

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass, field

import pytest

from culture.cli import mesh as mesh_mod

# ---------------------------------------------------------------------------
# Stub data classes
# ---------------------------------------------------------------------------


@dataclass
class _StubLink:
    name: str
    host: str = "peer.example.com"
    port: int = 6667
    trust: str = "full"


@dataclass
class _StubAgent:
    nick: str


@dataclass
class _StubServerCfg:
    name: str = "spark"
    host: str = "127.0.0.1"
    port: int = 6667
    links: list = field(default_factory=list)


@dataclass
class _StubMesh:
    server: _StubServerCfg
    agents: list = field(default_factory=list)


@dataclass
class _StubFullConfig:
    """Stand-in for culture.config.ServerConfig returned by load_config_or_default."""

    server: _StubServerCfg = field(default_factory=_StubServerCfg)
    agents: list = field(default_factory=list)


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"config": "~/.culture/mesh.yaml"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_dispatch_no_command_exits_with_usage(self, capsys):
        with pytest.raises(SystemExit) as exc:
            mesh_mod.dispatch(_args(mesh_command=None))
        assert exc.value.code == 1
        assert "Usage: culture mesh" in capsys.readouterr().err

    def test_dispatch_unknown_command_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            mesh_mod.dispatch(_args(mesh_command="frobnicate"))
        assert exc.value.code == 1
        assert "Unknown mesh command" in capsys.readouterr().err

    def test_dispatch_routes_to_handler(self, monkeypatch):
        called = []
        monkeypatch.setattr(mesh_mod, "_cmd_overview", lambda a: called.append("overview"))
        mesh_mod.dispatch(_args(mesh_command="overview"))
        assert called == ["overview"]


# ---------------------------------------------------------------------------
# _collect_mesh_data
# ---------------------------------------------------------------------------


class TestCollectMeshData:
    def _patch_collector(self, monkeypatch, *, side_effect=None, returns=None):
        """Patch `culture.overview.collector.collect_mesh_state` to a fake async."""

        async def _fake(*, host, port, server_name, message_limit, manifest_agents=None):
            if side_effect is not None:
                raise side_effect
            return returns or {"host": host, "port": port}

        monkeypatch.setattr("culture.overview.collector.collect_mesh_state", _fake)

    def test_happy_path_returns_mesh(self, monkeypatch):
        self._patch_collector(monkeypatch, returns={"server": "spark"})
        result = mesh_mod._collect_mesh_data("127.0.0.1", 6667, "spark", 4)
        assert result == {"server": "spark"}

    def test_connection_refused_exits_with_hint(self, monkeypatch, capsys):
        self._patch_collector(monkeypatch, side_effect=ConnectionRefusedError())
        with pytest.raises(SystemExit) as exc:
            mesh_mod._collect_mesh_data("127.0.0.1", 6667, "spark", 4)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "could not connect" in err

    def test_timeout_exits_with_hint(self, monkeypatch, capsys):
        self._patch_collector(monkeypatch, side_effect=TimeoutError())
        with pytest.raises(SystemExit) as exc:
            mesh_mod._collect_mesh_data("127.0.0.1", 6667, "spark", 4)
        assert exc.value.code == 1
        assert "not responding" in capsys.readouterr().err

    def test_other_oserror_exits_with_message(self, monkeypatch, capsys):
        self._patch_collector(monkeypatch, side_effect=OSError("eperm"))
        with pytest.raises(SystemExit) as exc:
            mesh_mod._collect_mesh_data("127.0.0.1", 6667, "spark", 4)
        assert exc.value.code == 1
        assert "eperm" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _cmd_overview (non-serve path)
# ---------------------------------------------------------------------------


class TestCmdOverview:
    def test_non_serve_path_renders_text(self, monkeypatch, capsys):
        cfg = _StubFullConfig()
        monkeypatch.setattr(mesh_mod, "load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(mesh_mod, "_collect_mesh_data", lambda *a, **kw: {"x": 1})
        monkeypatch.setattr(
            "culture.overview.renderer_text.render_text",
            lambda mesh, **kw: f"RENDERED({mesh})",
        )

        mesh_mod._cmd_overview(
            _args(
                mesh_command="overview",
                room=None,
                agent=None,
                messages=4,
                refresh=5,
                serve=False,
            )
        )

        assert "RENDERED" in capsys.readouterr().out

    def test_messages_clamped(self, monkeypatch):
        captured = {}

        def fake_collect(*args, **kwargs):
            captured["msgs"] = args[3]
            return {}

        monkeypatch.setattr(mesh_mod, "load_config_or_default", lambda _p: _StubFullConfig())
        monkeypatch.setattr(mesh_mod, "_collect_mesh_data", fake_collect)
        monkeypatch.setattr("culture.overview.renderer_text.render_text", lambda mesh, **kw: "")

        mesh_mod._cmd_overview(
            _args(
                mesh_command="overview",
                room=None,
                agent=None,
                messages=50,  # > max 20
                refresh=5,
                serve=False,
            )
        )
        assert captured["msgs"] == 20

    def test_serve_path_invokes_web(self, monkeypatch):
        called = []
        monkeypatch.setattr(mesh_mod, "load_config_or_default", lambda _p: _StubFullConfig())
        monkeypatch.setattr(
            "culture.overview.renderer_web.serve_web",
            lambda **kw: called.append(kw),
        )

        mesh_mod._cmd_overview(
            _args(
                mesh_command="overview",
                room=None,
                agent=None,
                messages=4,
                refresh=5,
                serve=True,
            )
        )

        assert called and called[0]["server_name"] == "spark"


# ---------------------------------------------------------------------------
# _cmd_console (deprecation passthrough)
# ---------------------------------------------------------------------------


class TestCmdConsole:
    def test_emits_deprecation_warning_and_forwards(self, monkeypatch, capsys):
        forwarded = []
        monkeypatch.setattr(mesh_mod, "console_dispatch", lambda name: forwarded.append(name))

        mesh_mod._cmd_console(_args(mesh_command="console", server_name="spark"))

        assert "deprecated" in capsys.readouterr().err
        assert forwarded == ["spark"]


# ---------------------------------------------------------------------------
# _store_mesh_credentials
# ---------------------------------------------------------------------------


class TestStoreMeshCredentials:
    def test_skips_when_credential_already_in_keyring(self, monkeypatch, capsys):
        mesh = _StubMesh(server=_StubServerCfg(links=[_StubLink(name="thor")]))
        monkeypatch.setattr("culture.credentials.lookup_credential", lambda n: "existing")
        # If getpass is called, the test fails noisily.
        monkeypatch.setattr("getpass.getpass", lambda _p: pytest.fail("should not prompt"))

        mesh_mod._store_mesh_credentials(mesh)
        assert "already in keyring" in capsys.readouterr().out

    def test_prompts_and_stores_when_missing(self, monkeypatch, capsys):
        mesh = _StubMesh(server=_StubServerCfg(links=[_StubLink(name="thor")]))
        monkeypatch.setattr("culture.credentials.lookup_credential", lambda n: None)
        monkeypatch.setattr("getpass.getpass", lambda _p: "secret")
        stored = []
        monkeypatch.setattr(
            "culture.credentials.store_credential",
            lambda name, pw: stored.append((name, pw)) or True,
        )

        mesh_mod._store_mesh_credentials(mesh)

        assert stored == [("thor", "secret")]
        assert "Stored credential" in capsys.readouterr().out

    def test_warns_when_store_fails(self, monkeypatch, capsys):
        mesh = _StubMesh(server=_StubServerCfg(links=[_StubLink(name="thor")]))
        monkeypatch.setattr("culture.credentials.lookup_credential", lambda n: None)
        monkeypatch.setattr("getpass.getpass", lambda _p: "secret")
        monkeypatch.setattr("culture.credentials.store_credential", lambda *a: False)

        mesh_mod._store_mesh_credentials(mesh)
        err = capsys.readouterr().err
        assert "failed to store credential" in err
        assert "secret-tool" in err


# ---------------------------------------------------------------------------
# _cmd_setup
# ---------------------------------------------------------------------------


class TestCmdSetup:
    def _setup(self, monkeypatch, *, mesh_load=None, generate=None):
        if mesh_load is not None:
            monkeypatch.setattr("culture.mesh_config.load_mesh_config", mesh_load)
        if generate is not None:
            monkeypatch.setattr(mesh_mod, "generate_mesh_from_agents", generate)

    def test_uninstall_path(self, monkeypatch, capsys):
        mesh = _StubMesh(
            server=_StubServerCfg(name="spark"),
            agents=[_StubAgent(nick="ada")],
        )
        self._setup(monkeypatch, mesh_load=lambda _p: mesh)
        monkeypatch.setattr(
            "culture.persistence.list_services",
            lambda: ["culture-server-spark", "culture-agent-spark-ada", "unrelated"],
        )
        removed = []
        monkeypatch.setattr("culture.persistence.uninstall_service", removed.append)
        stopped_servers = []
        stopped_agents = []
        monkeypatch.setattr(mesh_mod, "server_stop_by_name", stopped_servers.append)
        monkeypatch.setattr(mesh_mod, "stop_agent", stopped_agents.append)

        mesh_mod._cmd_setup(_args(mesh_command="setup", uninstall=True))

        assert sorted(removed) == ["culture-agent-spark-ada", "culture-server-spark"]
        assert stopped_servers == ["spark"]
        assert stopped_agents == ["spark-ada"]
        assert "Uninstalling" in capsys.readouterr().out

    def test_install_path_invokes_credential_and_service_install(self, monkeypatch, capsys):
        mesh = _StubMesh(
            server=_StubServerCfg(name="spark", links=[_StubLink(name="thor")]),
            agents=[_StubAgent(nick="ada")],
        )
        self._setup(monkeypatch, mesh_load=lambda _p: mesh)
        store_calls = []
        install_calls = []
        monkeypatch.setattr(mesh_mod, "_store_mesh_credentials", lambda m: store_calls.append(m))
        monkeypatch.setattr(
            mesh_mod,
            "_install_mesh_services",
            lambda *a, **kw: install_calls.append((a, kw)),
        )
        monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/culture")

        mesh_mod._cmd_setup(_args(mesh_command="setup", uninstall=False))

        assert store_calls == [mesh]
        assert len(install_calls) == 1
        assert "Setup complete" in capsys.readouterr().out

    def test_install_falls_back_to_generated_mesh(self, monkeypatch, capsys):
        # load_mesh_config raises → generate_mesh_from_agents returns a mesh
        def _missing(_p):
            raise FileNotFoundError

        generated = _StubMesh(server=_StubServerCfg(name="spark"))
        self._setup(monkeypatch, mesh_load=_missing, generate=lambda _p: generated)
        monkeypatch.setattr(mesh_mod, "_store_mesh_credentials", lambda m: None)
        monkeypatch.setattr(mesh_mod, "_install_mesh_services", lambda *a, **k: None)
        monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/culture")

        mesh_mod._cmd_setup(_args(mesh_command="setup", uninstall=False))

        assert "Setup complete" in capsys.readouterr().out

    def test_install_exits_when_no_mesh_and_generation_fails(self, monkeypatch, capsys):
        def _missing(_p):
            raise FileNotFoundError

        self._setup(monkeypatch, mesh_load=_missing, generate=lambda _p: None)

        with pytest.raises(SystemExit) as exc:
            mesh_mod._cmd_setup(_args(mesh_command="setup", uninstall=False))
        assert exc.value.code == 1


# ---------------------------------------------------------------------------
# _find_upgrade_tool / _upgrade_timeout_hint
# ---------------------------------------------------------------------------


class TestFindUpgradeTool:
    def test_prefers_uv(self, monkeypatch):
        monkeypatch.setattr(
            "shutil.which",
            lambda name: "/usr/bin/uv" if name == "uv" else None,
        )
        result = mesh_mod._find_upgrade_tool()
        assert result is not None
        tool_name, cmd = result
        assert tool_name == "uv"
        assert cmd == ["/usr/bin/uv", "tool", "upgrade", "culture"]

    def test_falls_back_to_pip(self, monkeypatch):
        def _which(name):
            return "/usr/bin/pip" if name == "pip" else None

        monkeypatch.setattr("shutil.which", _which)
        result = mesh_mod._find_upgrade_tool()
        assert result is not None
        tool_name, cmd = result
        assert tool_name == "pip"
        assert "install" in cmd and "--upgrade" in cmd

    def test_returns_none_when_neither_present(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda _n: None)
        assert mesh_mod._find_upgrade_tool() is None


class TestUpgradeTimeoutHint:
    @pytest.mark.parametrize(
        "tool,direct_cmd",
        [
            ("uv", "uv tool upgrade culture"),
            ("pip", "pip install --upgrade culture"),
        ],
    )
    def test_includes_direct_cmd_for_tool(self, tool, direct_cmd):
        hint = mesh_mod._upgrade_timeout_hint(tool, 600)
        assert direct_cmd in hint
        assert "600s" in hint
        assert "--skip-upgrade" in hint


# ---------------------------------------------------------------------------
# _run_upgrade
# ---------------------------------------------------------------------------


class TestRunUpgrade:
    def test_success_returns_silently(self, monkeypatch, capsys):
        fake_result = subprocess.CompletedProcess(args=[], returncode=0)
        monkeypatch.setattr(mesh_mod.subprocess, "run", lambda *a, **kw: fake_result)

        mesh_mod._run_upgrade("uv", ["uv", "tool", "upgrade", "culture"], 600)

        assert "Upgrading via uv" in capsys.readouterr().out

    def test_timeout_exits_with_hint(self, monkeypatch, capsys):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="uv", timeout=600)

        monkeypatch.setattr(mesh_mod.subprocess, "run", _raise)
        with pytest.raises(SystemExit) as exc:
            mesh_mod._run_upgrade("uv", ["uv"], 600)
        assert exc.value.code == 1
        assert "timed out" in capsys.readouterr().err

    def test_non_zero_exit_exits_with_error(self, monkeypatch, capsys):
        fake_result = subprocess.CompletedProcess(args=[], returncode=2)
        monkeypatch.setattr(mesh_mod.subprocess, "run", lambda *a, **kw: fake_result)
        with pytest.raises(SystemExit) as exc:
            mesh_mod._run_upgrade("pip", ["pip", "install"], 600)
        assert exc.value.code == 1
        assert "upgrade failed" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _upgrade_culture_package
# ---------------------------------------------------------------------------


class TestUpgradeCulturePackage:
    def _args_update(self, **kwargs):
        defaults = dict(
            skip_upgrade=False,
            dry_run=False,
            upgrade_timeout=600,
            config="~/.culture/mesh.yaml",
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_skip_upgrade_returns_true(self):
        assert mesh_mod._upgrade_culture_package(self._args_update(skip_upgrade=True)) is True

    def test_dry_run_returns_false_after_announcing(self, capsys):
        result = mesh_mod._upgrade_culture_package(self._args_update(dry_run=True))
        assert result is False
        out = capsys.readouterr().out
        assert "[dry-run]" in out

    def test_exits_when_no_upgrade_tool(self, monkeypatch, capsys):
        monkeypatch.setattr(mesh_mod, "_find_upgrade_tool", lambda: None)
        with pytest.raises(SystemExit) as exc:
            mesh_mod._upgrade_culture_package(self._args_update())
        assert exc.value.code == 1
        assert "Neither uv nor pip" in capsys.readouterr().err

    def test_runs_upgrade_then_reexecs(self, monkeypatch):
        """`_run_upgrade` returns, then `os.execvp` would normally fire.

        We patch `os.execvp` to raise so the function does not actually
        exec the real CLI in the test process. The branch is excluded
        from coverage in production (it's an end-of-life re-exec), so we
        only assert that the function reaches that point.
        """
        monkeypatch.setattr(
            mesh_mod, "_find_upgrade_tool", lambda: ("uv", ["uv", "tool", "upgrade", "culture"])
        )
        monkeypatch.setattr(mesh_mod, "_run_upgrade", lambda *a, **kw: None)
        monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/culture")
        execvp_calls: list = []

        def _fake_execvp(prog, argv):
            execvp_calls.append((prog, argv))
            raise SystemExit(0)

        monkeypatch.setattr(mesh_mod.os, "execvp", _fake_execvp)

        with pytest.raises(SystemExit):
            mesh_mod._upgrade_culture_package(self._args_update())

        assert execvp_calls and execvp_calls[0][0] == "/usr/bin/culture"
        assert "--skip-upgrade" in execvp_calls[0][1]


# ---------------------------------------------------------------------------
# _wait_for_server_port
# ---------------------------------------------------------------------------


class TestWaitForServerPort:
    @pytest.fixture(autouse=True)
    def _no_sleep(self, monkeypatch):
        monkeypatch.setattr(mesh_mod.time, "sleep", lambda _s: None)

    def test_returns_true_when_port_accepts(self, monkeypatch):
        import contextlib

        @contextlib.contextmanager
        def _fake_socket(*args, **kwargs):
            class _S:
                def close(self):
                    pass

            yield _S()

        monkeypatch.setattr("socket.create_connection", _fake_socket)
        assert mesh_mod._wait_for_server_port("127.0.0.1", 6667, retries=2) is True

    def test_returns_false_when_port_never_opens(self, monkeypatch):
        def _refuse(*a, **kw):
            raise OSError("refused")

        monkeypatch.setattr("socket.create_connection", _refuse)
        assert mesh_mod._wait_for_server_port("127.0.0.1", 6667, retries=3) is False

    def test_returns_false_when_pid_not_culture(self, monkeypatch):
        import contextlib

        @contextlib.contextmanager
        def _fake_socket(*args, **kwargs):
            class _S:
                def close(self):
                    pass

            yield _S()

        monkeypatch.setattr("socket.create_connection", _fake_socket)
        monkeypatch.setattr("culture.pidfile.read_pid", lambda _n: 4242)
        monkeypatch.setattr("culture.pidfile.is_culture_process", lambda _pid: False)

        result = mesh_mod._wait_for_server_port("0.0.0.0", 6667, retries=2, server_name="spark")
        assert result is False

    def test_zero_pid_does_not_disqualify(self, monkeypatch):
        """read_pid returning None means no pidfile — connection is enough."""
        import contextlib

        @contextlib.contextmanager
        def _fake_socket(*args, **kwargs):
            class _S:
                def close(self):
                    pass

            yield _S()

        monkeypatch.setattr("socket.create_connection", _fake_socket)
        monkeypatch.setattr("culture.pidfile.read_pid", lambda _n: None)
        monkeypatch.setattr("culture.pidfile.is_culture_process", lambda _pid: True)

        assert (
            mesh_mod._wait_for_server_port("0.0.0.0", 6667, retries=1, server_name="spark") is True
        )


# ---------------------------------------------------------------------------
# _dry_run_restart
# ---------------------------------------------------------------------------


class TestDryRunRestart:
    def test_lists_every_step(self, capsys):
        mesh = _StubMesh(
            server=_StubServerCfg(name="spark"),
            agents=[_StubAgent(nick="ada"), _StubAgent(nick="bob")],
        )
        mesh_mod._dry_run_restart(mesh, "spark")
        out = capsys.readouterr().out
        for fragment in [
            "[dry-run] Would stop agent spark-ada",
            "[dry-run] Would stop agent spark-bob",
            "[dry-run] Would stop server spark",
            "[dry-run] Would start server spark",
            "[dry-run] Would start agent spark-ada",
            "[dry-run] Would start agent spark-bob",
        ]:
            assert fragment in out


# ---------------------------------------------------------------------------
# _restart_single_service
# ---------------------------------------------------------------------------


class TestRestartSingleService:
    def test_uses_service_when_available(self, monkeypatch, capsys):
        called = []
        mesh_mod._restart_single_service(
            "culture-server-spark",
            ["culture", "server", "start"],
            lambda svc: called.append(svc) or True,
        )
        assert called == ["culture-server-spark"]
        # Subprocess was not called — only service restart fired
        assert "Restarting culture-server-spark" in capsys.readouterr().out

    def test_falls_back_to_subprocess(self, monkeypatch, capsys):
        runs = []

        def _fake_run(cmd, **kw):
            runs.append(cmd)
            return subprocess.CompletedProcess(args=cmd, returncode=0)

        monkeypatch.setattr(mesh_mod.subprocess, "run", _fake_run)

        mesh_mod._restart_single_service(
            "culture-server-spark", ["culture", "server", "start"], lambda _svc: False
        )
        assert runs == [["culture", "server", "start"]]
        assert "starting via CLI" in capsys.readouterr().out

    def test_fallback_timeout_is_warned_but_not_raised(self, monkeypatch, capsys):
        def _timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kw.get("timeout", 30))

        monkeypatch.setattr(mesh_mod.subprocess, "run", _timeout)

        mesh_mod._restart_single_service(
            "culture-server-spark", ["culture", "server", "start"], lambda _svc: False
        )
        assert "timed out" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _restart_mesh_services
# ---------------------------------------------------------------------------


class TestRestartMeshServices:
    def test_dry_run_returns_true_and_prints_plan(self, monkeypatch, capsys):
        mesh = _StubMesh(server=_StubServerCfg(name="spark"))
        result = mesh_mod._restart_mesh_services(
            mesh, "spark", "/usr/bin/culture", "/tmp/mesh.yaml", dry_run=True
        )
        assert result is True
        assert "[dry-run]" in capsys.readouterr().out

    def test_returns_false_when_server_does_not_come_up(self, monkeypatch, capsys):
        mesh = _StubMesh(server=_StubServerCfg(name="spark"))
        monkeypatch.setattr(mesh_mod, "stop_agent", lambda _n: None)
        monkeypatch.setattr(mesh_mod, "server_stop_by_name", lambda _n: None)
        monkeypatch.setattr("culture.persistence.install_service", lambda *a, **kw: None)
        monkeypatch.setattr("culture.persistence.restart_service", lambda _svc: True)
        monkeypatch.setattr(mesh_mod, "_wait_for_server_port", lambda *a, **kw: False)

        result = mesh_mod._restart_mesh_services(
            mesh, "spark", "/usr/bin/culture", "/tmp/mesh.yaml", dry_run=False
        )
        assert result is False
        assert "did not start" in capsys.readouterr().err

    def test_happy_path_returns_true(self, monkeypatch, capsys):
        mesh = _StubMesh(
            server=_StubServerCfg(name="spark"),
            agents=[_StubAgent(nick="ada")],
        )
        monkeypatch.setattr(mesh_mod, "stop_agent", lambda _n: None)
        monkeypatch.setattr(mesh_mod, "server_stop_by_name", lambda _n: None)
        monkeypatch.setattr("culture.persistence.install_service", lambda *a, **kw: None)
        monkeypatch.setattr("culture.persistence.restart_service", lambda _svc: True)
        monkeypatch.setattr(mesh_mod, "_wait_for_server_port", lambda *a, **kw: True)

        result = mesh_mod._restart_mesh_services(
            mesh, "spark", "/usr/bin/culture", "/tmp/mesh.yaml", dry_run=False
        )
        assert result is True
        # `_restart_single_service` produces a "Restarting ..." line per service.
        out = capsys.readouterr().out
        assert "Restarting culture-server-spark" in out
        assert "Restarting culture-agent-spark-ada" in out


# ---------------------------------------------------------------------------
# _resolve_mesh_for_server
# ---------------------------------------------------------------------------


class TestResolveMeshForServer:
    def test_returns_loaded_mesh_when_server_matches(self, monkeypatch):
        mesh = _StubMesh(server=_StubServerCfg(name="spark"))
        monkeypatch.setattr("culture.mesh_config.load_mesh_config", lambda _p: mesh)
        result = mesh_mod._resolve_mesh_for_server("spark", "/tmp/mesh.yaml")
        assert result is mesh

    def test_returns_none_when_neither_path_matches(self, monkeypatch):
        def _missing(_p):
            raise FileNotFoundError

        monkeypatch.setattr("culture.mesh_config.load_mesh_config", _missing)
        # No DEFAULT_CONFIG file
        monkeypatch.setattr(mesh_mod.os.path, "isfile", lambda _p: False)

        assert mesh_mod._resolve_mesh_for_server("spark", "/tmp/mesh.yaml") is None

    def test_rebuilds_from_default_config(self, monkeypatch):
        def _missing(_p):
            raise FileNotFoundError

        monkeypatch.setattr("culture.mesh_config.load_mesh_config", _missing)
        monkeypatch.setattr(mesh_mod.os.path, "isfile", lambda _p: True)
        monkeypatch.setattr(
            mesh_mod, "load_config", lambda _p: _StubFullConfig(server=_StubServerCfg(name="spark"))
        )
        rebuilt = _StubMesh(server=_StubServerCfg(name="spark"))
        monkeypatch.setattr("culture.mesh_config.from_daemon_config", lambda dc: rebuilt)
        monkeypatch.setattr("culture.mesh_config.merge_links", lambda *a, **kw: None)
        saved = []
        monkeypatch.setattr(
            "culture.mesh_config.save_mesh_config",
            lambda mesh, path: saved.append((mesh, path)),
        )

        result = mesh_mod._resolve_mesh_for_server("spark", "/tmp/mesh.yaml")

        assert result is rebuilt
        assert saved == [(rebuilt, "/tmp/mesh.yaml")]


# ---------------------------------------------------------------------------
# _restart_running_servers / _restart_from_config
# ---------------------------------------------------------------------------


class TestRestartRunningServers:
    def test_warns_when_no_config_for_server(self, monkeypatch, capsys):
        monkeypatch.setattr(mesh_mod, "_resolve_mesh_for_server", lambda *a: None)

        result = mesh_mod._restart_running_servers(
            running=[{"name": "ghost"}],
            args=argparse.Namespace(config="/tmp/mesh.yaml", dry_run=True),
            culture_bin="culture",
        )

        assert result == []
        assert "no config found" in capsys.readouterr().err

    def test_collects_failed_restarts(self, monkeypatch):
        mesh = _StubMesh(server=_StubServerCfg(name="spark"))
        monkeypatch.setattr(mesh_mod, "_resolve_mesh_for_server", lambda *a: mesh)
        monkeypatch.setattr(mesh_mod, "_restart_mesh_services", lambda *a, **kw: False)

        failed = mesh_mod._restart_running_servers(
            running=[{"name": "spark"}],
            args=argparse.Namespace(config="/tmp/mesh.yaml", dry_run=False),
            culture_bin="culture",
        )
        assert failed == ["spark"]


class TestRestartFromConfig:
    def test_uses_loaded_mesh(self, monkeypatch):
        mesh = _StubMesh(server=_StubServerCfg(name="spark"))
        monkeypatch.setattr("culture.mesh_config.load_mesh_config", lambda _p: mesh)
        called = []
        monkeypatch.setattr(
            mesh_mod, "_restart_mesh_services", lambda *a, **kw: called.append(a) or True
        )

        result = mesh_mod._restart_from_config(
            argparse.Namespace(config="/tmp/mesh.yaml", dry_run=False), "culture"
        )
        assert result == []
        assert called and called[0][1] == "spark"  # server_name in args

    def test_falls_back_to_generated_mesh(self, monkeypatch):
        def _missing(_p):
            raise FileNotFoundError

        monkeypatch.setattr("culture.mesh_config.load_mesh_config", _missing)
        generated = _StubMesh(server=_StubServerCfg(name="spark"))
        monkeypatch.setattr(mesh_mod, "generate_mesh_from_agents", lambda _p: generated)
        monkeypatch.setattr(mesh_mod, "_restart_mesh_services", lambda *a, **kw: True)

        result = mesh_mod._restart_from_config(
            argparse.Namespace(config="/tmp/mesh.yaml", dry_run=False), "culture"
        )
        assert result == []

    def test_exits_when_no_mesh_can_be_built(self, monkeypatch):
        def _missing(_p):
            raise FileNotFoundError

        monkeypatch.setattr("culture.mesh_config.load_mesh_config", _missing)
        monkeypatch.setattr(mesh_mod, "generate_mesh_from_agents", lambda _p: None)

        with pytest.raises(SystemExit):
            mesh_mod._restart_from_config(
                argparse.Namespace(config="/tmp/mesh.yaml", dry_run=False), "culture"
            )


# ---------------------------------------------------------------------------
# _cmd_update — orchestration
# ---------------------------------------------------------------------------


class TestCmdUpdate:
    def _args_update(self, **kwargs):
        defaults = dict(
            mesh_command="update",
            skip_upgrade=True,  # Skip the upgrade path by default — tested separately.
            dry_run=False,
            upgrade_timeout=600,
            config="~/.culture/mesh.yaml",
        )
        defaults.update(kwargs)
        return argparse.Namespace(**defaults)

    def test_short_circuits_when_upgrade_returns_false(self, monkeypatch):
        """Dry-run path: _upgrade_culture_package returns False → function returns."""
        monkeypatch.setattr(mesh_mod, "_upgrade_culture_package", lambda _a: False)
        # No further calls should happen — if they do, this would explode.
        monkeypatch.setattr(
            "culture.pidfile.list_servers",
            lambda: pytest.fail("should not be called"),
        )

        mesh_mod._cmd_update(self._args_update())

    def test_restarts_running_servers_when_present(self, monkeypatch, capsys):
        monkeypatch.setattr(mesh_mod, "_upgrade_culture_package", lambda _a: True)
        monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/culture")
        monkeypatch.setattr("culture.pidfile.list_servers", lambda: [{"name": "spark"}])
        called = []
        monkeypatch.setattr(
            mesh_mod,
            "_restart_running_servers",
            lambda running, args, culture_bin: called.append("running") or [],
        )
        monkeypatch.setattr(
            mesh_mod,
            "_restart_from_config",
            lambda args, culture_bin: pytest.fail("should not be called"),
        )

        mesh_mod._cmd_update(self._args_update())

        assert called == ["running"]
        assert "Update complete" in capsys.readouterr().out

    def test_falls_back_to_config_when_no_running_servers(self, monkeypatch):
        monkeypatch.setattr(mesh_mod, "_upgrade_culture_package", lambda _a: True)
        monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/culture")
        monkeypatch.setattr("culture.pidfile.list_servers", lambda: [])
        called = []
        monkeypatch.setattr(
            mesh_mod,
            "_restart_from_config",
            lambda args, culture_bin: called.append("config") or [],
        )

        mesh_mod._cmd_update(self._args_update())
        assert called == ["config"]

    def test_exits_with_error_on_failed_restart(self, monkeypatch, capsys):
        monkeypatch.setattr(mesh_mod, "_upgrade_culture_package", lambda _a: True)
        monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/culture")
        monkeypatch.setattr("culture.pidfile.list_servers", lambda: [])
        monkeypatch.setattr(mesh_mod, "_restart_from_config", lambda args, cb: ["spark"])

        with pytest.raises(SystemExit) as exc:
            mesh_mod._cmd_update(self._args_update())
        assert exc.value.code == 1
        assert "Failed to restart" in capsys.readouterr().err

    def test_dry_run_announces_no_services_restarted(self, monkeypatch, capsys):
        monkeypatch.setattr(mesh_mod, "_upgrade_culture_package", lambda _a: True)
        monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/culture")
        monkeypatch.setattr("culture.pidfile.list_servers", lambda: [])
        monkeypatch.setattr(mesh_mod, "_restart_from_config", lambda *a: [])

        mesh_mod._cmd_update(self._args_update(dry_run=True))
        assert "Dry run complete" in capsys.readouterr().out
