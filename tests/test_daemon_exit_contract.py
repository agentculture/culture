"""Tests for the daemon exit-code contract (#15).

Covers the transient-vs-permanent classifier in ``culture_core.cli._errors``,
the zombie-aware reap-and-detect verification helpers in
``culture_core.cli.server``, and the fork paths of both daemon children
(server + agents) via REAL ``os.fork()`` children — no process mocks.

The contract: permanent config/user errors exit ``EXIT_DAEMON_PERMANENT``
(sysexits EX_CONFIG) so service managers park the unit; transient crashes
keep ``EXIT_DAEMON_TRANSIENT`` so they self-heal via restart; clean
shutdown still exits 0.
"""

from __future__ import annotations

import argparse
import asyncio
import errno
import os
import re
import signal
import socket
import subprocess
import sys
import time

import pytest

from culture_core.cli import agents as agents_mod
from culture_core.cli import main as cli_main
from culture_core.cli import server as srv_mod
from culture_core.cli._errors import (
    EXIT_DAEMON_PERMANENT,
    EXIT_DAEMON_TRANSIENT,
    EXIT_ENV_ERROR,
    EXIT_USER_ERROR,
    CultureError,
    classify_daemon_exit,
)
from culture_core.pidfile import is_process_alive, write_pid

# ---------------------------------------------------------------------------
# classify_daemon_exit
# ---------------------------------------------------------------------------


class TestClassifyDaemonExit:
    @pytest.mark.parametrize(
        "exc",
        [
            ValueError("malformed config"),
            KeyError("missing config key"),
            TypeError("bad config type"),
            PermissionError(errno.EACCES, "permission denied"),
            FileNotFoundError(errno.ENOENT, "no such file"),
            CultureError(EXIT_USER_ERROR, "unknown agent backend 'bogus'"),
            CultureError(EXIT_ENV_ERROR, "missing credentials"),
        ],
        ids=[
            "ValueError",
            "KeyError",
            "TypeError",
            "PermissionError",
            "FileNotFoundError",
            "CultureError-user",
            "CultureError-env",
        ],
    )
    def test_permanent_errors_exit_78(self, exc):
        assert classify_daemon_exit(exc) == EXIT_DAEMON_PERMANENT

    @pytest.mark.parametrize(
        "exc",
        [
            OSError(errno.EADDRINUSE, "address already in use"),
            ConnectionRefusedError(errno.ECONNREFUSED, "peer down"),
            RuntimeError("something odd"),
            Exception("anything unknown"),
        ],
        ids=["OSError-EADDRINUSE", "ConnectionRefusedError", "RuntimeError", "Exception"],
    )
    def test_transient_and_unknown_errors_exit_1(self, exc):
        """OSError (port taken, network) and unknown crashes stay restartable."""
        assert classify_daemon_exit(exc) == EXIT_DAEMON_TRANSIENT

    def test_contract_codes(self):
        """78 is sysexits EX_CONFIG; transient reuses the generic error code."""
        assert EXIT_DAEMON_PERMANENT == 78
        assert EXIT_DAEMON_TRANSIENT == 1


# ---------------------------------------------------------------------------
# _resolve_server_links — mesh-config resolution boundary (2026-07-03 outage)
#
# ``--mesh-config`` resolution runs in the CLI process BEFORE any fork, for
# both ``--foreground`` (what systemd's ``Type=simple`` ExecStart actually
# runs) and the background/daemonize path (the fork happens after this
# call). Before this fix, a missing/malformed mesh config raised a bare
# FileNotFoundError/yaml error that fell through ``main()``'s generic
# ``except Exception`` and exited 1 — indistinguishable from a transient
# crash, so ``RestartPreventExitStatus=EXIT_DAEMON_PERMANENT`` never
# engaged and systemd crash-looped the unit 11,235 times.
# ---------------------------------------------------------------------------


def _mesh_args(mesh_config: str) -> argparse.Namespace:
    return argparse.Namespace(mesh_config=mesh_config, link=[])


class TestResolveServerLinksConfigExitContract:
    def test_missing_mesh_config_raises_permanent(self, tmp_path):
        missing = tmp_path / "nope.yaml"
        with pytest.raises(CultureError) as exc:
            srv_mod._resolve_server_links(_mesh_args(str(missing)))
        assert exc.value.code == EXIT_DAEMON_PERMANENT
        assert str(missing) in exc.value.message
        assert "\n" not in exc.value.message  # one-line, actionable
        assert exc.value.remediation

    def test_malformed_yaml_raises_permanent(self, tmp_path):
        bad = tmp_path / "mesh.yaml"
        bad.write_text("server: {name: foo\n  bad indent: [1,2\n")
        with pytest.raises(CultureError) as exc:
            srv_mod._resolve_server_links(_mesh_args(str(bad)))
        assert exc.value.code == EXIT_DAEMON_PERMANENT
        assert "\n" not in exc.value.message

    def test_invalid_schema_raises_permanent(self, tmp_path):
        """A link entry with an unknown field is a schema error (surfaces
        as TypeError from MeshLinkConfig(**lc)), not a transient one."""
        bad = tmp_path / "mesh.yaml"
        bad.write_text(
            "server:\n"
            "  name: spark\n"
            "  links:\n"
            "    - name: thor\n"
            "      host: thor.example.com\n"
            "      port: 6667\n"
            "      bogus_key: yes\n"
        )
        with pytest.raises(CultureError) as exc:
            srv_mod._resolve_server_links(_mesh_args(str(bad)))
        assert exc.value.code == EXIT_DAEMON_PERMANENT
        assert "\n" not in exc.value.message

    def test_no_mesh_config_bypasses_the_boundary(self):
        """No ``--mesh-config``: CLI ``--link`` args pass straight through,
        never touching the try/except that classifies config errors."""
        link = object()
        result = srv_mod._resolve_server_links(argparse.Namespace(mesh_config=None, link=[link]))
        assert result == [link]


# ---------------------------------------------------------------------------
# `culture server start` end-to-end through ``culture_core.cli.main`` — the
# actual command systemd's ExecStart runs. Regression coverage for the
# 2026-07-03 outage at the level an operator/service-manager observes it.
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_cli_state(monkeypatch, tmp_path):
    """Keep these CLI-level tests off the real ``~/.culture`` tree."""
    monkeypatch.setattr(srv_mod, "DEFAULT_CONFIG", str(tmp_path / "no-server.yaml"))
    monkeypatch.setattr("culture_core.pidfile.PID_DIR", str(tmp_path / "pids"))
    return tmp_path


class TestServerStartCliExitsOnBadMeshConfig:
    def test_missing_mesh_config_exits_78_foreground(self, isolated_cli_state, monkeypatch, capsys):
        missing = isolated_cli_state / "nope.yaml"
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "culture",
                "server",
                "start",
                "--name",
                "t2clifg",
                "--mesh-config",
                str(missing),
                "--foreground",
            ],
        )
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == EXIT_DAEMON_PERMANENT
        err = capsys.readouterr().err
        assert "error:" in err
        assert str(missing) in err

    def test_missing_mesh_config_exits_78_background(self, isolated_cli_state, monkeypatch, capsys):
        """Background (fork) mode: resolution happens in the parent before
        ``os.fork()``, so the CLI process itself exits 78 — no daemon
        child, no pidfile, nothing left running."""
        missing = isolated_cli_state / "nope.yaml"
        monkeypatch.setattr(
            sys,
            "argv",
            ["culture", "server", "start", "--name", "t2clibg", "--mesh-config", str(missing)],
        )
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == EXIT_DAEMON_PERMANENT


class TestServerStartAlreadyRunningStaysTransient:
    """Boundary check (requirement 3): an already-running server is a
    transient/user condition that resolves itself once the running
    instance stops — it must NOT exit 78, or the unit would park
    permanently on a condition restart (of the CLI invocation) fixes."""

    def test_already_running_exits_1_not_78(self, isolated_cli_state, monkeypatch, capsys):
        name = "t2clirunning"
        write_pid(f"server-{name}", os.getpid())  # our own test process: genuinely alive
        monkeypatch.setattr(sys, "argv", ["culture", "server", "start", "--name", name])
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == EXIT_USER_ERROR
        assert exc.value.code != EXIT_DAEMON_PERMANENT
        assert "already running" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Reap-and-detect helpers (parent-side verification)
# ---------------------------------------------------------------------------


def _fork_exit(code: int) -> int:
    """Fork a real child that exits immediately with *code*; return its pid."""
    pid = os.fork()
    if pid == 0:
        os._exit(code)  # child: bypass pytest entirely
    return pid


def _poll(probe, timeout: float = 5.0):
    """Poll *probe* until it returns non-None (or *timeout* expires)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = probe()
        if result is not None:
            return result
        time.sleep(0.01)
    return None


class TestDaemonChildExitCode:
    def test_zombie_child_is_seen_dead_with_status(self):
        """A child that died before being reaped is a zombie: os.kill(pid, 0)
        still succeeds, so is_process_alive reports it alive on every Unix —
        waitpid(WNOHANG) must be the probe that wins."""
        pid = _fork_exit(7)
        # Running or zombie, kill(pid, 0) succeeds either way — the old
        # verification could report this dead child as started.
        assert is_process_alive(pid) is True
        assert _poll(lambda: srv_mod._daemon_child_exit_code(pid)) == 7

    def test_permanent_code_round_trips(self):
        pid = _fork_exit(EXIT_DAEMON_PERMANENT)
        assert _poll(lambda: srv_mod._daemon_child_exit_code(pid)) == EXIT_DAEMON_PERMANENT

    def test_running_child_returns_none(self):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(write_fd)
            os.read(read_fd, 1)  # block until the parent closes its end
            os._exit(0)
        os.close(read_fd)
        try:
            assert srv_mod._daemon_child_exit_code(pid) is None
        finally:
            os.close(write_fd)  # EOF unblocks the child
            os.waitpid(pid, 0)

    def test_non_child_pid_returns_none(self):
        """PID 1 is never our child — ChildProcessError maps to None so the
        caller can fall back to is_process_alive."""
        assert srv_mod._daemon_child_exit_code(1) is None

    def test_signaled_child_reports_128_plus_signum(self):
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(write_fd)
            os.read(read_fd, 1)
            os._exit(0)
        os.close(read_fd)
        try:
            os.kill(pid, signal.SIGKILL)
            assert _poll(lambda: srv_mod._daemon_child_exit_code(pid)) == 128 + signal.SIGKILL
        finally:
            os.close(write_fd)


class TestProbeDaemonFailure:
    def test_dead_child_permanent_message(self):
        pid = _fork_exit(EXIT_DAEMON_PERMANENT)
        msg = _poll(lambda: srv_mod._probe_daemon_failure(pid))
        assert msg == "exited with code 78 (permanent error — will not be restarted)"

    def test_dead_child_transient_message(self):
        pid = _fork_exit(EXIT_DAEMON_TRANSIENT)
        msg = _poll(lambda: srv_mod._probe_daemon_failure(pid))
        assert msg == "exited with code 1"

    def test_alive_process_returns_none(self):
        assert srv_mod._probe_daemon_failure(os.getpid()) is None

    def test_reaped_non_child_falls_back_to_is_process_alive(self):
        """A pid we never forked (already reaped elsewhere) keeps the old
        'failed to start' path via is_process_alive."""
        proc = subprocess.Popen(["true"])
        proc.wait()  # Popen reaps — waitpid raises ChildProcessError for us
        assert srv_mod._probe_daemon_failure(proc.pid) == "failed to start"


class TestWaitForPortDeadChild:
    def test_detects_child_that_died_before_port_opened(self):
        """A child that dies before ever listening must fail verification
        fast — with its exit status — instead of burning the full timeout
        polling a zombie the old is_process_alive probe saw as alive."""
        tmp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tmp.bind(("127.0.0.1", 0))
        port = tmp.getsockname()[1]
        tmp.close()

        pid = _fork_exit(EXIT_DAEMON_PERMANENT)
        start = time.monotonic()
        ok, err = srv_mod._wait_for_port("127.0.0.1", port, pid, timeout=30)
        elapsed = time.monotonic() - start

        assert ok is False
        assert "exited with code 78" in err
        assert "permanent error — will not be restarted" in err
        assert elapsed < 10  # detected the death; did not wait out the timeout


# ---------------------------------------------------------------------------
# _daemonize_server — end-to-end over a real fork
# ---------------------------------------------------------------------------


@pytest.fixture
def daemon_dirs(monkeypatch, tmp_path):
    """Isolate the fork tests from the real ~/.culture tree."""
    monkeypatch.setattr(srv_mod, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(agents_mod, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr("culture_core.pidfile.PID_DIR", str(tmp_path / "pids"))
    return tmp_path


def _free_port() -> int:
    """Bind-then-close to get a port nothing listens on."""
    tmp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tmp.bind(("127.0.0.1", 0))
    port = tmp.getsockname()[1]
    tmp.close()
    return port


def _server_args(name: str) -> argparse.Namespace:
    return argparse.Namespace(
        name=name,
        host="127.0.0.1",
        port=_free_port(),
        webhook_port=0,
        data_dir="",
    )


class TestDaemonizeServerExitContract:
    def test_permanent_crash_reported_as_parked(self, daemon_dirs, monkeypatch, capsys):
        """A config-shaped crash in the daemon child exits 78, and the parent
        verification reports it as a permanent, non-restartable failure."""

        async def _boom(*_a, **_kw):
            raise ValueError("malformed config")

        monkeypatch.setattr(srv_mod, "_run_server", _boom)
        with pytest.raises(SystemExit) as e:
            srv_mod._daemonize_server(_server_args("exitperm"), "server-exitperm", [])
        assert e.value.code == 1
        err = capsys.readouterr().err
        assert "exited with code 78" in err
        assert "permanent error — will not be restarted" in err

    def test_transient_crash_reported_with_code_1(self, daemon_dirs, monkeypatch, capsys):
        async def _flaky(*_a, **_kw):
            raise OSError(errno.EADDRINUSE, "address already in use")

        monkeypatch.setattr(srv_mod, "_run_server", _flaky)
        with pytest.raises(SystemExit) as e:
            srv_mod._daemonize_server(_server_args("exittrans"), "server-exittrans", [])
        assert e.value.code == 1
        err = capsys.readouterr().err
        assert "exited with code 1" in err
        assert "permanent" not in err

    def test_healthy_child_still_verifies_started(self, daemon_dirs, monkeypatch, capsys):
        """The reap-and-detect probe must not false-positive on a healthy
        child that simply hasn't opened its port state yet."""

        async def _serve_forever(name, host, port, *_a, **_kw):
            server = await asyncio.start_server(lambda r, w: None, host, port)
            async with server:
                await asyncio.sleep(60)

        monkeypatch.setattr(srv_mod, "_run_server", _serve_forever)
        srv_mod._daemonize_server(_server_args("exitok"), "server-exitok", [])
        out = capsys.readouterr().out
        match = re.search(r"started \(PID (\d+)\)", out)
        assert match, out
        pid = int(match.group(1))
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)


# ---------------------------------------------------------------------------
# _run_multi_agents — the agents fork path follows the same contract
# ---------------------------------------------------------------------------


class TestRunMultiAgentsExitContract:
    def _fork_and_reap(self, monkeypatch, capsys, behavior) -> int:
        """Fork one agent whose run is *behavior*; return the child's exit code."""

        async def _fake_run_single_agent(_config, _agent):
            behavior()

        monkeypatch.setattr(agents_mod, "_run_single_agent", _fake_run_single_agent)
        agents_mod._run_multi_agents(None, [argparse.Namespace(nick="spark-exitc")])

        out = capsys.readouterr().out
        match = re.search(r"PID (\d+)", out)
        assert match, out
        _, status = os.waitpid(int(match.group(1)), 0)
        assert os.WIFEXITED(status)
        return os.WEXITSTATUS(status)

    def test_permanent_crash_exits_78(self, daemon_dirs, monkeypatch, capsys):
        def _raise():
            raise ValueError("malformed config")

        assert self._fork_and_reap(monkeypatch, capsys, _raise) == EXIT_DAEMON_PERMANENT

    def test_transient_crash_exits_1(self, daemon_dirs, monkeypatch, capsys):
        """The old `finally: os._exit(0)` masked every agent crash as success."""

        def _raise():
            raise OSError(errno.EADDRINUSE, "address already in use")

        assert self._fork_and_reap(monkeypatch, capsys, _raise) == EXIT_DAEMON_TRANSIENT

    def test_clean_shutdown_exits_0(self, daemon_dirs, monkeypatch, capsys):
        assert self._fork_and_reap(monkeypatch, capsys, lambda: None) == 0

    def test_keyboard_interrupt_exits_0(self, daemon_dirs, monkeypatch, capsys):
        def _interrupt():
            raise KeyboardInterrupt

        assert self._fork_and_reap(monkeypatch, capsys, _interrupt) == 0

    def test_sys_exit_code_propagates(self, daemon_dirs, monkeypatch, capsys):
        """sys.exit(N) inside the child must reach the OS — not fall through
        to os._exit(0) because SystemExit isn't an Exception."""

        def _exit_5():
            sys.exit(5)

        assert self._fork_and_reap(monkeypatch, capsys, _exit_5) == 5

    def test_sys_exit_message_exits_1(self, daemon_dirs, monkeypatch, capsys):
        def _exit_msg():
            sys.exit("fatal: something")

        assert self._fork_and_reap(monkeypatch, capsys, _exit_msg) == 1

    def test_sys_exit_none_exits_0(self, daemon_dirs, monkeypatch, capsys):
        def _exit_none():
            sys.exit(None)

        assert self._fork_and_reap(monkeypatch, capsys, _exit_none) == 0


# ---------------------------------------------------------------------------
# _daemonize_server — SystemExit follows the same policy
# ---------------------------------------------------------------------------


class TestDaemonizeServerSystemExit:
    def test_sys_exit_nonzero_not_masked_as_success(self, daemon_dirs, monkeypatch, capsys):
        async def _exiting(*_a, **_kw):
            sys.exit(7)

        monkeypatch.setattr(srv_mod, "_run_server", _exiting)
        with pytest.raises(SystemExit) as e:
            srv_mod._daemonize_server(_server_args("exitsys"), "server-exitsys", [])
        assert e.value.code == 1
        assert "exited with code 7" in capsys.readouterr().err
