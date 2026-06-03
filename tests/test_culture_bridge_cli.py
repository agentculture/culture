"""Tests for ``culture bridge`` — the per-nick IRC-bridge CLI verb.

The CLI is a thin wrapper around ``python -m culture.clients.bridge``.
These tests cover the wrapper's concerns:

  * PID-file lifecycle (write on start, remove on stop, leave-alone on
    foreground, clean-stale on second start).
  * Liveness probe (signal-0).
  * Nick validation at the CLI boundary (so a typo cannot end up as
    part of a filesystem path).
  * ``status`` enumeration with live / stale / broken labels.

The actual daemon process is mocked at ``subprocess.Popen`` — we
don't want pytest spawning real IRC clients.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from culture.cli import bridge

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Pin ~ to tmp_path so ``~/.culture/run/`` lives in the test dir."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # On some platforms os.path.expanduser caches; force a fresh resolve
    # by also patching the function for determinism.
    monkeypatch.setattr(
        os.path,
        "expanduser",
        lambda p: p.replace("~", str(tmp_path), 1) if p.startswith("~") else p,
    )
    return tmp_path


@pytest.fixture
def fake_popen(monkeypatch):
    """Replace ``subprocess.Popen`` with a stub that records args + returns a
    handle with a stable .pid."""
    calls: list[list[str]] = []

    class _Proc:
        pid = 4242

        def terminate(self):  # pragma: no cover — tests should not fall back here
            pass

    def _popen(cmd, **kwargs):
        calls.append(list(cmd))
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", _popen)
    return calls


# ----------------------------------------------------------------------
# Nick validation
# ----------------------------------------------------------------------


class TestNickValidation:
    @pytest.mark.parametrize(
        "nick",
        ["local-boss", "fork-cc", "ABC", "x", "a-b_c-1"],
    )
    def test_valid_nicks_accepted(self, nick: str) -> None:
        # ``_validate_nick`` ``sys.exit``s on rejection; running cleanly
        # is the positive signal.
        bridge._validate_nick(nick)

    @pytest.mark.parametrize(
        "nick",
        [
            "",
            "1-leading-digit",
            "-leading-hyphen",
            "has space",
            "has/slash",
            "has..dotdot",
            "x" * 65,  # too long
            "nick;rm -rf /",
        ],
    )
    def test_invalid_nicks_rejected(self, nick: str) -> None:
        with pytest.raises(SystemExit) as exc:
            bridge._validate_nick(nick)
        assert exc.value.code == 1


# ----------------------------------------------------------------------
# Start
# ----------------------------------------------------------------------


def _start_args(nick: str, **overrides) -> argparse.Namespace:
    defaults = {
        "nick": nick,
        "config": "/tmp/fake-server.yaml",
        "channels": None,
        "tags": None,
        "foreground": False,
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestCmdStart:
    def test_start_writes_pid_file_and_returns(self, fake_home, fake_popen, capsys) -> None:
        bridge._cmd_start(_start_args("local-fork"))
        pid_path = bridge._pid_path("local-fork")
        assert os.path.exists(pid_path)
        with open(pid_path) as fh:
            assert fh.read().strip() == "4242"
        # PID-file mode must be same-user-only — leaking the pid path
        # to other users would let them ``kill -TERM`` someone else's
        # bridge.
        mode = os.stat(pid_path).st_mode & 0o777
        assert mode == 0o600
        # User-facing message names the PID.
        captured = capsys.readouterr()
        assert "started" in captured.out
        assert "4242" in captured.out

    def test_start_passes_channels_to_subprocess(self, fake_home, fake_popen) -> None:
        bridge._cmd_start(_start_args("local-fork", channels=["#a", "#b"]))
        assert len(fake_popen) == 1
        cmd = fake_popen[0]
        assert "--channels" in cmd
        idx = cmd.index("--channels")
        assert cmd[idx + 1] == "#a"
        assert cmd[idx + 2] == "#b"

    def test_start_passes_tags_to_subprocess(self, fake_home, fake_popen) -> None:
        bridge._cmd_start(_start_args("local-fork", tags=["custom", "extra"]))
        cmd = fake_popen[0]
        # --tag repeats; both should appear.
        idxs = [i for i, t in enumerate(cmd) if t == "--tag"]
        assert len(idxs) == 2
        assert cmd[idxs[0] + 1] == "custom"
        assert cmd[idxs[1] + 1] == "extra"

    def test_start_refuses_when_live_pid_exists(self, fake_home, fake_popen, monkeypatch) -> None:
        # First start writes the PID file.
        bridge._cmd_start(_start_args("local-fork"))
        # Pretend the recorded PID is still alive.
        monkeypatch.setattr(bridge, "_is_alive", lambda _pid: True)
        # Second start refuses with exit 1.
        with pytest.raises(SystemExit) as exc:
            bridge._cmd_start(_start_args("local-fork"))
        assert exc.value.code == 1

    def test_start_cleans_stale_pid_and_proceeds(self, fake_home, fake_popen, monkeypatch) -> None:
        bridge._cmd_start(_start_args("local-fork"))
        # First Popen returned pid=4242; pretend that process died.
        monkeypatch.setattr(bridge, "_is_alive", lambda _pid: False)
        # Second start should succeed (stale cleanup), updating the PID file.

        class _NewProc:
            pid = 9999

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _NewProc())
        bridge._cmd_start(_start_args("local-fork"))
        with open(bridge._pid_path("local-fork")) as fh:
            assert fh.read().strip() == "9999"

    def test_start_command_includes_nick_and_config(self, fake_home, fake_popen) -> None:
        bridge._cmd_start(_start_args("local-fork", config="/custom/path.yaml"))
        cmd = fake_popen[0]
        # Always shape: <python> -m culture.clients.bridge start <nick> --config <path>
        assert cmd[0] == sys.executable
        assert cmd[1:5] == ["-m", "culture.clients.bridge", "start", "local-fork"]
        assert "--config" in cmd
        assert cmd[cmd.index("--config") + 1] == "/custom/path.yaml"

    def test_start_invalid_nick_exits_before_subprocess(self, fake_home, fake_popen) -> None:
        with pytest.raises(SystemExit):
            bridge._cmd_start(_start_args("../etc/passwd"))
        # No subprocess fired.
        assert fake_popen == []


class TestCmdStartForeground:
    def test_foreground_runs_subprocess_run_not_popen(self, fake_home, monkeypatch) -> None:
        ran: list[list[str]] = []

        class _Result:
            returncode = 0

        def _run(cmd, **kw):
            ran.append(list(cmd))
            return _Result()

        monkeypatch.setattr(subprocess, "run", _run)
        # Popen MUST NOT be called in foreground mode.
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **k: pytest.fail("Popen used in foreground mode"),
        )
        with pytest.raises(SystemExit) as exc:
            bridge._cmd_start(_start_args("local-fork", foreground=True))
        assert exc.value.code == 0
        assert ran[0][:5] == [
            sys.executable,
            "-m",
            "culture.clients.bridge",
            "start",
            "local-fork",
        ]
        # No PID file in foreground mode — the shell is the lifecycle root.
        assert not os.path.exists(bridge._pid_path("local-fork"))


# ----------------------------------------------------------------------
# Stop
# ----------------------------------------------------------------------


class TestCmdStop:
    def test_stop_signals_recorded_pid_and_removes_file(
        self, fake_home, monkeypatch, capsys
    ) -> None:
        os.makedirs(bridge._run_dir(), exist_ok=True)
        pid_path = bridge._pid_path("local-fork")
        with open(pid_path, "w") as fh:
            fh.write("7777")

        signalled: list[tuple[int, int]] = []
        monkeypatch.setattr(bridge, "_is_alive", lambda _pid: True)
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

        bridge._cmd_stop(argparse.Namespace(nick="local-fork"))
        assert signalled == [(7777, signal.SIGTERM)]
        assert not os.path.exists(pid_path)
        out = capsys.readouterr().out
        assert "stopped" in out and "7777" in out

    def test_stop_no_pid_file_reports_and_returns_clean(self, fake_home, capsys) -> None:
        bridge._cmd_stop(argparse.Namespace(nick="local-fork"))
        out = capsys.readouterr().out
        assert "no PID file" in out

    def test_stop_stale_pid_cleans_up_without_signalling(
        self, fake_home, monkeypatch, capsys
    ) -> None:
        os.makedirs(bridge._run_dir(), exist_ok=True)
        pid_path = bridge._pid_path("local-fork")
        with open(pid_path, "w") as fh:
            fh.write("12345")

        signalled: list[tuple[int, int]] = []
        monkeypatch.setattr(bridge, "_is_alive", lambda _pid: False)
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

        bridge._cmd_stop(argparse.Namespace(nick="local-fork"))
        assert signalled == []  # never sent a signal — pid was already dead
        assert not os.path.exists(pid_path)
        out = capsys.readouterr().out
        assert "stale" in out or "dead" in out


# ----------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------


class TestCmdStatus:
    def test_status_no_run_dir_prints_no_bridges(self, fake_home, capsys) -> None:
        bridge._cmd_status(argparse.Namespace())
        assert "no bridges running" in capsys.readouterr().out

    def test_status_empty_run_dir_prints_no_bridges(self, fake_home, capsys) -> None:
        os.makedirs(bridge._run_dir(), exist_ok=True)
        bridge._cmd_status(argparse.Namespace())
        assert "no bridges running" in capsys.readouterr().out

    def test_status_labels_live_and_stale(self, fake_home, monkeypatch, capsys) -> None:
        os.makedirs(bridge._run_dir(), exist_ok=True)
        with open(bridge._pid_path("alpha"), "w") as fh:
            fh.write("100")
        with open(bridge._pid_path("beta"), "w") as fh:
            fh.write("200")
        with open(bridge._pid_path("gamma"), "w") as fh:
            fh.write("not-a-number")

        # alpha=alive, beta=dead.
        monkeypatch.setattr(bridge, "_is_alive", lambda pid: pid == 100)

        bridge._cmd_status(argparse.Namespace())
        out = capsys.readouterr().out
        assert "alpha" in out and "running" in out and "100" in out
        assert "beta" in out and "stale" in out and "200" in out
        assert "gamma" in out and "broken" in out


# ----------------------------------------------------------------------
# Dispatch surface
# ----------------------------------------------------------------------


class TestDispatch:
    def test_dispatch_routes_start(self, monkeypatch) -> None:
        called = {}

        def _start(args):
            called["start"] = args.nick

        monkeypatch.setattr(bridge, "_cmd_start", _start)
        bridge.dispatch(
            argparse.Namespace(
                bridge_command="start",
                nick="x",
                config="c",
                channels=None,
                tags=None,
                foreground=False,
            )
        )
        assert called == {"start": "x"}

    def test_dispatch_no_subcommand_exits(self, capsys) -> None:
        with pytest.raises(SystemExit) as exc:
            bridge.dispatch(argparse.Namespace(bridge_command=None))
        assert exc.value.code == 1
        assert "Usage" in capsys.readouterr().err


# ----------------------------------------------------------------------
# CLI registration
# ----------------------------------------------------------------------


class TestRegistration:
    def test_bridge_group_is_in_top_level_groups(self) -> None:
        """Ensure ``culture/cli/__init__.py`` registers the bridge group
        so ``culture bridge ...`` works at the top level."""
        from culture.cli import GROUPS

        assert (
            bridge in GROUPS
        ), "culture.cli.bridge must be added to GROUPS in culture/cli/__init__.py"

    def test_register_adds_start_stop_status(self) -> None:
        """Subparser registration adds all three verbs (start/stop/status)."""
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        bridge.register(sub)
        # ``parse_args(["bridge","--help"])`` would exit, so we parse a real
        # invocation instead.
        args = parser.parse_args(["bridge", "start", "local-fork"])
        assert args.command == "bridge"
        assert args.bridge_command == "start"
        assert args.nick == "local-fork"
