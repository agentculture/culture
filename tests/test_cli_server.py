"""Tests for `culture.cli.server` — `culture server {start,stop,status,default,rename,archive,unarchive}`.

The pid-file and signal layer is covered by `tests/test_cli_shared_process.py`
and `tests/test_pidfile.py`. These tests focus on dispatch, argument
resolution, and the archive cascade. Integration-territory functions
(`_run_server`, `_daemonize_server`, `_run_foreground`) are NOT exercised
— they're covered by `tests/test_integration_irc_transport.py`.

OS surface mocked at module boundary:

- `culture.pidfile.{read_pid, write_pid, remove_pid, is_process_alive,
  is_culture_process, read_default_server, write_default_server,
  rename_pid, list_servers}` — patched on the `culture.cli.server` module
  (where they're imported at module top).
- `os.kill`, `time.sleep`, `socket.create_connection`.
- `culture.config.{load_config_or_default, rename_manifest_server,
  archive_manifest_server, unarchive_manifest_server, sanitize_agent_name}`.
"""

from __future__ import annotations

import argparse
import signal
from dataclasses import dataclass, field

import pytest

from culture.cli import server as srv_mod

# ---------------------------------------------------------------------------
# Stub config classes
# ---------------------------------------------------------------------------


@dataclass
class _StubAgent:
    nick: str
    archived: bool = False


@dataclass
class _StubServerCfg:
    name: str = "spark"
    host: str = "127.0.0.1"
    port: int = 6667
    archived: bool = False


@dataclass
class _StubConfig:
    server: _StubServerCfg = field(default_factory=_StubServerCfg)
    agents: list = field(default_factory=list)


def _args(**kwargs) -> argparse.Namespace:
    defaults = {"config": "~/.culture/server.yaml"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# Shared monkeypatch fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(srv_mod.time, "sleep", lambda _s: None)


@pytest.fixture
def pids(monkeypatch):
    """Bookkeep pidfile + process-alive state. Defaults to "running culture process"."""
    table: dict[str, int] = {}
    alive: dict[int, list[bool]] = {}
    culture_set: set = set()
    removed: list[str] = []
    written: list[tuple[str, int]] = []
    default_server: dict[str, str | None] = {"current": None}

    def _pop_alive(pid):
        seq = alive.get(pid)
        if not seq:
            return True
        return seq.pop(0) if len(seq) > 1 else seq[0]

    # server.py imports read_pid, write_pid, remove_pid, is_process_alive,
    # is_culture_process, read_default_server at module top — patch on srv_mod.
    monkeypatch.setattr(srv_mod, "read_pid", lambda name: table.get(name))
    monkeypatch.setattr(srv_mod, "write_pid", lambda name, pid: written.append((name, pid)))
    monkeypatch.setattr(srv_mod, "remove_pid", lambda name: removed.append(name))
    monkeypatch.setattr(srv_mod, "is_process_alive", _pop_alive)
    monkeypatch.setattr(
        srv_mod, "is_culture_process", lambda pid: pid in culture_set or not culture_set
    )
    monkeypatch.setattr(srv_mod, "read_default_server", lambda: default_server["current"])
    # write_default_server and rename_pid are lazy-imported inside handlers —
    # patch them at the source so the lazy imports pick up the mock.
    monkeypatch.setattr(
        "culture.pidfile.write_default_server",
        lambda name: default_server.update(current=name),
    )

    class State:
        def __init__(self):
            self.table = table
            self.alive = alive
            self.culture_set = culture_set
            self.removed = removed
            self.written = written
            self.default_server = default_server

        def set_pid(self, name, pid, *, alive_seq=None, is_culture=True):
            self.table[name] = pid
            if alive_seq is not None:
                self.alive[pid] = list(alive_seq)
            if is_culture:
                self.culture_set.add(pid)

    return State()


@pytest.fixture
def fake_os_kill(monkeypatch):
    calls = []
    raises = {}

    def _fake(pid, sig):
        calls.append((pid, sig))
        if (exc := raises.get(len(calls))) is not None:
            raise exc()

    monkeypatch.setattr(srv_mod.os, "kill", _fake)

    class State:
        def __init__(self):
            self.calls = calls
            self.raises = raises

        def raise_on_call(self, n, exc):
            self.raises[n] = exc

    return State()


# ---------------------------------------------------------------------------
# _resolve_server_name
# ---------------------------------------------------------------------------


class TestResolveServerName:
    def test_explicit_name_wins(self, pids):
        result = srv_mod._resolve_server_name(_args(name="thor"))
        assert result == "thor"

    def test_falls_back_to_default_server(self, pids):
        pids.default_server["current"] = "thor"
        assert srv_mod._resolve_server_name(_args(name=None)) == "thor"

    def test_falls_back_to_hardcoded_default(self, pids):
        # No default set
        assert srv_mod._resolve_server_name(_args(name=None)) == "culture"


# ---------------------------------------------------------------------------
# _cmd_default
# ---------------------------------------------------------------------------


class TestCmdDefault:
    def test_accepts_running_server(self, monkeypatch, pids, capsys, tmp_path):
        monkeypatch.setattr("culture.pidfile.list_servers", lambda: [{"name": "spark"}])
        monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path / "no-pids"))
        # Configured server is also accepted; stub it.
        monkeypatch.setattr(
            "culture.config.load_config_or_default",
            lambda _p: _StubConfig(),
        )
        # Patch write_default_server inside _cmd_default's import (lazy).
        wrote = []
        monkeypatch.setattr("culture.pidfile.write_default_server", lambda name: wrote.append(name))

        srv_mod._cmd_default(argparse.Namespace(name="spark"))

        assert wrote == ["spark"]
        assert "Default server set to 'spark'" in capsys.readouterr().out

    def test_rejects_unknown_server(self, monkeypatch, capsys, tmp_path):
        monkeypatch.setattr("culture.pidfile.list_servers", lambda: [])
        monkeypatch.setattr("culture.pidfile.PID_DIR", str(tmp_path))
        # No configured server name
        monkeypatch.setattr(
            "culture.config.load_config_or_default",
            lambda _p: (_ for _ in ()).throw(OSError()),
        )

        with pytest.raises(SystemExit) as exc:
            srv_mod._cmd_default(argparse.Namespace(name="ghost"))
        assert exc.value.code == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_no_verb_exits(self, capsys):
        with pytest.raises(SystemExit) as exc:
            srv_mod.dispatch(_args(server_command=None))
        assert exc.value.code == 1
        assert "Usage: culture server" in capsys.readouterr().err

    def test_unknown_verb_exits(self, monkeypatch, capsys, pids):
        with pytest.raises(SystemExit) as exc:
            srv_mod.dispatch(_args(server_command="frobnicate", name="spark"))
        assert exc.value.code == 1
        assert "Unknown server command" in capsys.readouterr().err

    def test_forwarded_verb_calls_agentirc(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            "agentirc.cli.dispatch",
            lambda argv: calls.append(argv) or 0,
        )

        with pytest.raises(SystemExit) as exc:
            srv_mod.dispatch(_args(server_command="logs", argv=["--tail", "10"]))

        assert exc.value.code == 0
        assert calls == [["logs", "--tail", "10"]]

    def test_routes_to_status_handler(self, monkeypatch, pids):
        called = []
        monkeypatch.setattr(srv_mod, "_server_status", called.append)
        srv_mod.dispatch(_args(server_command="status", name="spark"))
        assert len(called) == 1

    def test_default_verb_skips_name_resolution(self, monkeypatch):
        """`default` verb has a positional `name`, not `--name` — skip _resolve_server_name."""
        called = []
        monkeypatch.setattr(srv_mod, "_cmd_default", called.append)
        srv_mod.dispatch(argparse.Namespace(server_command="default", name="thor"))
        assert called and called[0].name == "thor"


# ---------------------------------------------------------------------------
# _wait_for_port
# ---------------------------------------------------------------------------


class TestWaitForPort:
    def test_returns_true_on_first_connect(self, monkeypatch, pids):
        # Process is alive
        pids.alive[1234] = [True]

        class _Sock:
            def close(self):
                pass

        monkeypatch.setattr(srv_mod.socket, "create_connection", lambda *a, **kw: _Sock())

        ok, msg = srv_mod._wait_for_port("127.0.0.1", 6667, pid=1234, timeout=1)
        assert ok is True
        assert msg == ""

    def test_returns_false_when_pid_dies(self, monkeypatch, pids):
        pids.alive[1234] = [False]
        # Patch is_process_alive on the module so the loop sees the pid as dead.
        ok, msg = srv_mod._wait_for_port("127.0.0.1", 6667, pid=1234, timeout=1)
        assert ok is False
        assert "failed to start" in msg

    def test_returns_false_on_timeout(self, monkeypatch, pids):
        pids.alive[1234] = [True]

        def _refuse(*a, **kw):
            raise OSError("refused")

        monkeypatch.setattr(srv_mod.socket, "create_connection", _refuse)
        # Push time forward fast: monotonic returns increasing values past the deadline.
        times = iter([0, 0.5, 1, 1.5, 2, 2.5])
        monkeypatch.setattr(srv_mod.time, "monotonic", lambda: next(times, 5))

        ok, msg = srv_mod._wait_for_port("127.0.0.1", 6667, pid=1234, timeout=1)
        assert ok is False


# ---------------------------------------------------------------------------
# _maybe_set_default_server
# ---------------------------------------------------------------------------


class TestMaybeSetDefaultServer:
    def test_writes_when_no_default(self, monkeypatch):
        wrote = []
        monkeypatch.setattr("culture.pidfile.read_default_server", lambda: None)
        monkeypatch.setattr("culture.pidfile.write_default_server", lambda name: wrote.append(name))

        srv_mod._maybe_set_default_server("spark")
        assert wrote == ["spark"]

    def test_does_not_overwrite_existing(self, monkeypatch):
        wrote = []
        monkeypatch.setattr("culture.pidfile.read_default_server", lambda: "thor")
        monkeypatch.setattr("culture.pidfile.write_default_server", lambda name: wrote.append(name))

        srv_mod._maybe_set_default_server("spark")
        assert wrote == []


# ---------------------------------------------------------------------------
# _check_server_archived
# ---------------------------------------------------------------------------


class TestCheckServerArchived:
    def test_exits_when_archived(self, monkeypatch, capsys):
        cfg = _StubConfig(server=_StubServerCfg(name="spark", archived=True))
        monkeypatch.setattr("culture.config.load_config_or_default", lambda _p: cfg)

        with pytest.raises(SystemExit) as exc:
            srv_mod._check_server_archived(_args(name="spark"))
        assert exc.value.code == 1
        assert "is archived" in capsys.readouterr().err

    def test_passes_when_not_archived(self, monkeypatch):
        cfg = _StubConfig(server=_StubServerCfg(name="spark", archived=False))
        monkeypatch.setattr("culture.config.load_config_or_default", lambda _p: cfg)
        srv_mod._check_server_archived(_args(name="spark"))  # no raise

    def test_passes_when_name_mismatches(self, monkeypatch):
        """Mismatched name → check doesn't apply, just returns."""
        cfg = _StubConfig(server=_StubServerCfg(name="other", archived=True))
        monkeypatch.setattr("culture.config.load_config_or_default", lambda _p: cfg)
        srv_mod._check_server_archived(_args(name="spark"))  # no raise


# ---------------------------------------------------------------------------
# _check_already_running
# ---------------------------------------------------------------------------


class TestCheckAlreadyRunning:
    def test_exits_when_running(self, pids, capsys):
        pids.set_pid("server-spark", 4242, alive_seq=[True])
        with pytest.raises(SystemExit) as exc:
            srv_mod._check_already_running("server-spark", "spark")
        assert exc.value.code == 1
        assert "already running" in capsys.readouterr().out

    def test_passes_when_pid_missing(self, pids):
        srv_mod._check_already_running("server-spark", "spark")  # no raise

    def test_passes_when_pid_stale(self, pids):
        pids.set_pid("server-spark", 4242, alive_seq=[False])
        srv_mod._check_already_running("server-spark", "spark")  # no raise


# ---------------------------------------------------------------------------
# _resolve_server_links
# ---------------------------------------------------------------------------


class TestResolveServerLinks:
    def test_uses_cli_links_by_default(self):
        link = object()
        result = srv_mod._resolve_server_links(_args(link=[link], mesh_config=None))
        assert result == [link]

    def test_resolves_from_mesh_config_when_given(self, monkeypatch):
        resolved = [object(), object()]
        monkeypatch.setattr(srv_mod, "resolve_links_from_mesh", lambda path: resolved)
        result = srv_mod._resolve_server_links(_args(link=[], mesh_config="/tmp/mesh.yaml"))
        assert result == resolved


# ---------------------------------------------------------------------------
# _wait_for_graceful_stop
# ---------------------------------------------------------------------------


class TestWaitForGracefulStop:
    def test_returns_true_when_process_exits(self, pids):
        pids.alive[1234] = [True, True, False]
        assert srv_mod._wait_for_graceful_stop(1234, timeout_ticks=5) is True

    def test_returns_false_when_polls_exhausted(self, pids):
        pids.alive[1234] = [True]  # always alive
        assert srv_mod._wait_for_graceful_stop(1234, timeout_ticks=3) is False


# ---------------------------------------------------------------------------
# _force_kill
# ---------------------------------------------------------------------------


class TestForceKill:
    def test_posix_sends_sigkill(self, monkeypatch, fake_os_kill, capsys):
        monkeypatch.setattr(srv_mod.sys, "platform", "linux")
        srv_mod._force_kill(1234, "spark")
        assert fake_os_kill.calls == [(1234, signal.SIGKILL)]
        assert "sending SIGKILL" in capsys.readouterr().out

    def test_win32_sends_sigterm(self, monkeypatch, fake_os_kill, capsys):
        monkeypatch.setattr(srv_mod.sys, "platform", "win32")
        srv_mod._force_kill(1234, "spark")
        assert fake_os_kill.calls == [(1234, signal.SIGTERM)]
        assert "terminating" in capsys.readouterr().out

    def test_swallows_process_lookup_error(self, monkeypatch, fake_os_kill):
        monkeypatch.setattr(srv_mod.sys, "platform", "linux")
        fake_os_kill.raise_on_call(1, ProcessLookupError)
        # No exception escapes
        srv_mod._force_kill(1234, "spark")


# ---------------------------------------------------------------------------
# _server_stop
# ---------------------------------------------------------------------------


class TestServerStop:
    def test_exits_when_no_pidfile(self, pids, capsys):
        with pytest.raises(SystemExit) as exc:
            srv_mod._server_stop(_args(name="spark"))
        assert exc.value.code == 1
        assert "No PID file" in capsys.readouterr().out

    def test_removes_stale_pid(self, pids, capsys):
        pids.set_pid("server-spark", 4242, alive_seq=[False])
        srv_mod._server_stop(_args(name="spark"))
        assert "server-spark" in pids.removed
        assert "stale PID" in capsys.readouterr().out

    def test_removes_non_culture_pid(self, pids, capsys):
        # alive but not in culture_set → fallback predicate is_culture_process
        # returns True when set is empty; force the empty-set guard off by
        # adding a dummy other pid.
        pids.culture_set.add(9999)
        pids.set_pid("server-spark", 4242, alive_seq=[True], is_culture=False)
        srv_mod._server_stop(_args(name="spark"))
        assert "server-spark" in pids.removed
        assert "not a culture process" in capsys.readouterr().out

    def test_sigterm_success(self, monkeypatch, pids, fake_os_kill, capsys):
        pids.set_pid("server-spark", 4242, alive_seq=[True, False])
        monkeypatch.setattr(srv_mod.sys, "platform", "linux")

        srv_mod._server_stop(_args(name="spark"))

        assert fake_os_kill.calls == [(4242, signal.SIGTERM)]
        assert "server-spark" in pids.removed
        assert "stopped" in capsys.readouterr().out

    def test_sigkill_escalation(self, monkeypatch, pids, fake_os_kill, capsys):
        pids.set_pid("server-spark", 4242, alive_seq=[True])  # never dies
        monkeypatch.setattr(srv_mod.sys, "platform", "linux")

        srv_mod._server_stop(_args(name="spark"))

        assert fake_os_kill.calls == [
            (4242, signal.SIGTERM),
            (4242, signal.SIGKILL),
        ]
        assert "killed" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# _server_status
# ---------------------------------------------------------------------------


class TestServerStatus:
    def test_no_pidfile(self, pids, capsys):
        srv_mod._server_status(_args(name="spark"))
        assert "not running (no PID file)" in capsys.readouterr().out

    def test_running(self, pids, capsys):
        pids.set_pid("server-spark", 4242, alive_seq=[True])
        srv_mod._server_status(_args(name="spark"))
        assert "running (PID 4242)" in capsys.readouterr().out

    def test_stale_pid_removed(self, pids, capsys):
        pids.set_pid("server-spark", 4242, alive_seq=[False])
        srv_mod._server_status(_args(name="spark"))
        assert "stale PID" in capsys.readouterr().out
        assert "server-spark" in pids.removed


# ---------------------------------------------------------------------------
# _validate_config_name
# ---------------------------------------------------------------------------


class TestValidateConfigName:
    def test_match_returns_name(self):
        cfg = _StubConfig(server=_StubServerCfg(name="spark"))
        assert srv_mod._validate_config_name(cfg, "spark") == "spark"

    def test_mismatch_exits(self, capsys):
        cfg = _StubConfig(server=_StubServerCfg(name="other"))
        with pytest.raises(SystemExit) as exc:
            srv_mod._validate_config_name(cfg, "spark")
        assert exc.value.code == 1
        assert "name mismatch" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _update_single_bot_archive
# ---------------------------------------------------------------------------


class TestUpdateSingleBotArchive:
    def _bot(self, **overrides):
        from culture.bots.config import BotConfig

        return BotConfig(**overrides)

    def test_archives_active_bot(self, monkeypatch):
        saved = []
        monkeypatch.setattr(
            "culture.bots.config.save_bot_config",
            lambda path, cfg: saved.append((path, cfg.archived, cfg.archived_reason)),
        )
        bot = self._bot(name="b", archived=False)

        result = srv_mod._update_single_bot_archive(
            "/tmp/bot.yaml", bot, archive=True, reason="superseded", today="2026-05-13"
        )

        assert result == "b"
        assert saved == [("/tmp/bot.yaml", True, "superseded")]

    def test_unarchives_archived_bot(self, monkeypatch):
        monkeypatch.setattr("culture.bots.config.save_bot_config", lambda *a: None)
        bot = self._bot(name="b", archived=True, archived_reason="old")

        result = srv_mod._update_single_bot_archive(
            "/tmp/bot.yaml", bot, archive=False, reason="", today="2026-05-13"
        )

        assert result == "b"
        assert bot.archived is False
        assert bot.archived_reason == ""

    def test_noop_when_already_in_target_state(self, monkeypatch):
        monkeypatch.setattr(
            "culture.bots.config.save_bot_config",
            lambda *a: pytest.fail("should not save"),
        )
        bot = self._bot(name="b", archived=True)

        # Already archived; archive=True is a no-op.
        assert (
            srv_mod._update_single_bot_archive(
                "/tmp/bot.yaml", bot, archive=True, reason="", today="2026-05-13"
            )
            is None
        )


# ---------------------------------------------------------------------------
# _set_bots_archive_state
# ---------------------------------------------------------------------------


class TestSetBotsArchiveState:
    def test_returns_empty_when_bots_dir_missing(self, monkeypatch, tmp_path):
        ghost = tmp_path / "no-such"
        monkeypatch.setattr("culture.bots.config.BOTS_DIR", ghost)
        assert srv_mod._set_bots_archive_state({"spark-ada"}, archive=True) == []

    def test_cascades_to_matching_bots(self, monkeypatch, tmp_path):
        from culture.bots.config import BotConfig, save_bot_config

        bots = tmp_path / "bots"
        bots.mkdir()
        # Owned by spark-ada → should be archived
        cfg_ada = BotConfig(name="spark-ada-watch", owner="spark-ada")
        (bots / "spark-ada-watch").mkdir()
        save_bot_config(bots / "spark-ada-watch" / "bot.yaml", cfg_ada)
        # Owned by someone else → should be skipped
        cfg_other = BotConfig(name="spark-bob-watch", owner="spark-bob")
        (bots / "spark-bob-watch").mkdir()
        save_bot_config(bots / "spark-bob-watch" / "bot.yaml", cfg_other)

        monkeypatch.setattr("culture.bots.config.BOTS_DIR", bots)

        changed = srv_mod._set_bots_archive_state({"spark-ada"}, archive=True, reason="cleanup")

        assert changed == ["spark-ada-watch"]


# ---------------------------------------------------------------------------
# _server_archive / _server_unarchive (orchestrators)
# ---------------------------------------------------------------------------


class TestServerArchive:
    def test_already_archived_returns_silently(self, monkeypatch, capsys):
        cfg = _StubConfig(server=_StubServerCfg(name="spark", archived=True))
        monkeypatch.setattr("culture.config.load_config_or_default", lambda _p: cfg)
        srv_mod._server_archive(_args(name="spark", reason=""))
        assert "already archived" in capsys.readouterr().out

    def test_archives_server_and_cascades(self, monkeypatch, pids, capsys):
        cfg = _StubConfig(
            server=_StubServerCfg(name="spark", archived=False),
            agents=[_StubAgent(nick="spark-ada"), _StubAgent(nick="spark-bob")],
        )
        monkeypatch.setattr("culture.config.load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr(
            "culture.config.archive_manifest_server",
            lambda path, reason="": ["spark-ada", "spark-bob"],
        )
        # No running pid for the server
        monkeypatch.setattr(srv_mod, "_set_bots_archive_state", lambda *a, **kw: ["bot1"])
        # stop_agent shim — agent.archive cascade
        stopped = []
        monkeypatch.setattr("culture.cli.shared.process.stop_agent", stopped.append)

        srv_mod._server_archive(_args(name="spark", reason="cleanup"))

        out = capsys.readouterr().out
        assert "Server archived: spark" in out
        assert "Agents: spark-ada, spark-bob" in out
        assert "Bots:   bot1" in out
        assert "cleanup" in out


class TestServerUnarchive:
    def test_not_archived_exits(self, monkeypatch, capsys):
        cfg = _StubConfig(server=_StubServerCfg(name="spark", archived=False))
        monkeypatch.setattr("culture.config.load_config_or_default", lambda _p: cfg)
        with pytest.raises(SystemExit) as exc:
            srv_mod._server_unarchive(_args(name="spark"))
        assert exc.value.code == 1
        assert "is not archived" in capsys.readouterr().err

    def test_unarchives_server_and_cascades(self, monkeypatch, capsys):
        cfg = _StubConfig(
            server=_StubServerCfg(name="spark", archived=True),
            agents=[_StubAgent(nick="spark-ada")],
        )
        monkeypatch.setattr("culture.config.load_config_or_default", lambda _p: cfg)
        monkeypatch.setattr("culture.config.unarchive_manifest_server", lambda path: ["spark-ada"])
        monkeypatch.setattr(srv_mod, "_set_bots_archive_state", lambda *a, **kw: ["bot1"])

        srv_mod._server_unarchive(_args(name="spark"))

        out = capsys.readouterr().out
        assert "Server unarchived: spark" in out
        assert "Agents: spark-ada" in out
        assert "Bots:   bot1" in out


# ---------------------------------------------------------------------------
# _server_rename
# ---------------------------------------------------------------------------


class TestServerRename:
    def _setup(self, monkeypatch, *, sanitize_raises=False, rename_raises=None, rename_result=None):
        if sanitize_raises:
            monkeypatch.setattr(
                "culture.config.sanitize_agent_name",
                lambda n: (_ for _ in ()).throw(ValueError("bad name")),
            )
        else:
            monkeypatch.setattr("culture.config.sanitize_agent_name", lambda n: n)
        if rename_raises is not None:
            monkeypatch.setattr(
                "culture.config.rename_manifest_server",
                lambda *a, **kw: (_ for _ in ()).throw(rename_raises),
            )
        else:
            monkeypatch.setattr(
                "culture.config.rename_manifest_server",
                lambda *a, **kw: rename_result or ("old", []),
            )

    def test_invalid_name_exits(self, monkeypatch, capsys):
        self._setup(monkeypatch, sanitize_raises=True)
        with pytest.raises(SystemExit) as exc:
            srv_mod._server_rename(_args(new_name="bad name!", config="~/.culture/server.yaml"))
        assert exc.value.code == 1
        assert "Invalid server name" in capsys.readouterr().err

    def test_rename_manifest_error_exits(self, monkeypatch, capsys):
        self._setup(monkeypatch, rename_raises=ValueError("server not found"))
        with pytest.raises(SystemExit) as exc:
            srv_mod._server_rename(_args(new_name="thor", config="~/.culture/server.yaml"))
        assert exc.value.code == 1
        assert "server not found" in capsys.readouterr().err

    def test_same_name_returns_silently(self, monkeypatch, capsys):
        self._setup(monkeypatch, rename_result=("thor", []))
        srv_mod._server_rename(_args(new_name="thor", config="~/.culture/server.yaml"))
        assert "already named 'thor'" in capsys.readouterr().out

    def test_renames_pid_and_agents(self, monkeypatch, capsys):
        self._setup(
            monkeypatch,
            rename_result=("spark", [("spark-ada", "thor-ada")]),
        )
        pid_renames = []
        monkeypatch.setattr(
            "culture.pidfile.rename_pid", lambda old, new: pid_renames.append((old, new))
        )
        monkeypatch.setattr("culture.pidfile.read_default_server", lambda: "spark")
        wrote_default = []
        monkeypatch.setattr("culture.pidfile.write_default_server", wrote_default.append)
        monkeypatch.setattr("culture.pidfile.read_pid", lambda _n: None)
        monkeypatch.setattr("culture.pidfile.is_process_alive", lambda _p: False)

        srv_mod._server_rename(_args(new_name="thor", config="~/.culture/server.yaml"))

        assert ("server-spark", "server-thor") in pid_renames
        assert ("agent-spark-ada", "agent-thor-ada") in pid_renames
        assert wrote_default == ["thor"]
        out = capsys.readouterr().out
        assert "Server renamed: spark → thor" in out

    def test_warns_when_server_still_running(self, monkeypatch, capsys):
        self._setup(monkeypatch, rename_result=("spark", []))
        monkeypatch.setattr("culture.pidfile.rename_pid", lambda old, new: None)
        monkeypatch.setattr("culture.pidfile.read_default_server", lambda: None)
        monkeypatch.setattr("culture.pidfile.read_pid", lambda _n: 4242)
        monkeypatch.setattr("culture.pidfile.is_process_alive", lambda _p: True)

        srv_mod._server_rename(_args(new_name="thor", config="~/.culture/server.yaml"))

        out = capsys.readouterr().out
        assert "still running under the old name" in out


# ---------------------------------------------------------------------------
# _server_start (skip _run_foreground / _daemonize_server)
# ---------------------------------------------------------------------------


class TestServerStart:
    def test_routes_to_foreground_branch(self, monkeypatch, pids):
        monkeypatch.setattr(srv_mod, "_check_server_archived", lambda _a: None)
        monkeypatch.setattr(srv_mod, "_resolve_server_links", lambda _a: [])
        called = []
        monkeypatch.setattr(srv_mod, "_run_foreground", lambda *a, **kw: called.append("fg"))
        monkeypatch.setattr(srv_mod, "_daemonize_server", lambda *a, **kw: called.append("daemon"))

        srv_mod._server_start(_args(name="spark", foreground=True))
        assert called == ["fg"]

    def test_routes_to_daemonize_when_not_foreground(self, monkeypatch, pids):
        monkeypatch.setattr(srv_mod, "_check_server_archived", lambda _a: None)
        monkeypatch.setattr(srv_mod, "_resolve_server_links", lambda _a: [])
        called = []
        monkeypatch.setattr(srv_mod, "_run_foreground", lambda *a, **kw: called.append("fg"))
        monkeypatch.setattr(srv_mod, "_daemonize_server", lambda *a, **kw: called.append("daemon"))

        srv_mod._server_start(_args(name="spark", foreground=False))
        assert called == ["daemon"]
