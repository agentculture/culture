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
    """Qodo PR #51 #1: nicks MUST be ``<server>-<agent>``."""

    @pytest.mark.parametrize(
        "nick",
        [
            "local-boss",
            "fork-cc",
            "thor-claude",
            "spark-st4ck-boss",  # agent half may contain hyphens
            "local-plenty-staging-boss",
            "a-b",  # minimal valid
            "local-st4ck-boss",
        ],
    )
    def test_valid_nicks_accepted(self, nick: str) -> None:
        # ``_validate_nick`` ``sys.exit``s on rejection; running cleanly
        # is the positive signal.
        bridge._validate_nick(nick)

    @pytest.mark.parametrize(
        "nick",
        [
            "",
            "ABC",  # no hyphen
            "x",  # no hyphen
            "single",  # no hyphen
            "-leading-hyphen",  # empty server
            "trailing-",  # empty agent (split would leave "" as agent)
            "has space",
            "has/slash",
            "has\\backslash",
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
        # Pretend the recorded PID is still a live bridge.
        monkeypatch.setattr(bridge, "_is_our_bridge", lambda _pid: True)
        # Second start refuses with exit 1.
        with pytest.raises(SystemExit) as exc:
            bridge._cmd_start(_start_args("local-fork"))
        assert exc.value.code == 1

    def test_start_proceeds_when_live_pid_is_not_a_culture_process(
        self, fake_home, fake_popen, monkeypatch
    ) -> None:
        """Qodo PR #51 #3: a PID that's alive but NOT a culture process
        means the OS recycled the original bridge's PID for someone
        else. The PID file is stale; start should proceed."""
        bridge._cmd_start(_start_args("local-fork"))

        # PID is alive but not ours — simulate OS reusing the PID.
        monkeypatch.setattr(bridge, "_is_our_bridge", lambda _pid: False)

        class _NewProc:
            pid = 9999

        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: _NewProc())
        bridge._cmd_start(_start_args("local-fork"))
        with open(bridge._pid_path("local-fork")) as fh:
            assert fh.read().strip() == "9999"

    def test_start_cleans_stale_pid_and_proceeds(self, fake_home, fake_popen, monkeypatch) -> None:
        bridge._cmd_start(_start_args("local-fork"))
        # First Popen returned pid=4242; pretend that process died.
        monkeypatch.setattr(bridge, "_is_our_bridge", lambda _pid: False)
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
        # First call (pre-signal liveness check): alive. After SIGTERM
        # we want the wait loop to see it as dead on the very next
        # probe so the test finishes quickly.
        alive_state = {"alive": True}

        def _alive(_pid):
            v = alive_state["alive"]
            alive_state["alive"] = False  # flip after first read
            return v

        monkeypatch.setattr(bridge, "is_process_alive", _alive)
        monkeypatch.setattr(bridge, "is_culture_process", lambda _pid: True)
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))
        monkeypatch.setattr(bridge.time, "sleep", lambda _: None)

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
        monkeypatch.setattr(bridge, "is_process_alive", lambda _pid: False)
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

        bridge._cmd_stop(argparse.Namespace(nick="local-fork"))
        assert signalled == []  # never sent a signal — pid was already dead
        assert not os.path.exists(pid_path)
        out = capsys.readouterr().out
        assert "stale" in out or "dead" in out

    def test_stop_reused_pid_refuses_to_signal(self, fake_home, monkeypatch, capsys) -> None:
        """Qodo PR #51 #3: PID is alive but NOT a culture process →
        OS recycled the PID for someone else. NEVER SIGTERM."""
        os.makedirs(bridge._run_dir(), exist_ok=True)
        pid_path = bridge._pid_path("local-fork")
        with open(pid_path, "w") as fh:
            fh.write("12345")

        signalled: list[tuple[int, int]] = []
        monkeypatch.setattr(bridge, "is_process_alive", lambda _pid: True)
        monkeypatch.setattr(bridge, "is_culture_process", lambda _pid: False)
        monkeypatch.setattr(os, "kill", lambda pid, sig: signalled.append((pid, sig)))

        bridge._cmd_stop(argparse.Namespace(nick="local-fork"))
        assert signalled == []  # MUST NOT SIGTERM an unrelated process
        assert not os.path.exists(pid_path)
        out = capsys.readouterr().out
        assert "not a culture process" in out

    def test_stop_waits_for_process_exit_before_removing_pid_file(
        self, fake_home, monkeypatch, capsys
    ) -> None:
        """Qodo PR #51 #4: the PID file must NOT be removed until the
        bridge process actually exits."""
        os.makedirs(bridge._run_dir(), exist_ok=True)
        pid_path = bridge._pid_path("local-fork")
        with open(pid_path, "w") as fh:
            fh.write("12345")

        # Alive for the first 3 wait-loop probes, dead from the 4th.
        # Record whether the PID file is still on disk DURING the wait.
        probe_count = {"n": 0}
        pid_file_seen_after_signal: list[bool] = []

        def _alive(_pid):
            probe_count["n"] += 1
            # Probe #1 is the pre-signal liveness gate.
            if probe_count["n"] > 1:
                pid_file_seen_after_signal.append(os.path.exists(pid_path))
            return probe_count["n"] <= 3

        monkeypatch.setattr(bridge, "is_process_alive", _alive)
        monkeypatch.setattr(bridge, "is_culture_process", lambda _pid: True)
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(bridge.time, "sleep", lambda _: None)

        bridge._cmd_stop(argparse.Namespace(nick="local-fork"))
        assert any(
            pid_file_seen_after_signal
        ), "PID file should remain on disk while process is alive"
        assert not os.path.exists(pid_path)
        out = capsys.readouterr().out
        assert "stopped" in out

    def test_stop_keeps_pid_file_if_process_will_not_exit(
        self, fake_home, monkeypatch, capsys
    ) -> None:
        """Qodo PR #51 #4: if the process is still alive after the
        wait window, the PID file MUST be preserved and the command
        MUST exit non-zero."""
        os.makedirs(bridge._run_dir(), exist_ok=True)
        pid_path = bridge._pid_path("local-fork")
        with open(pid_path, "w") as fh:
            fh.write("12345")

        monkeypatch.setattr(bridge, "is_process_alive", lambda _pid: True)
        monkeypatch.setattr(bridge, "is_culture_process", lambda _pid: True)
        monkeypatch.setattr(os, "kill", lambda pid, sig: None)
        monkeypatch.setattr(bridge.time, "sleep", lambda _: None)

        with pytest.raises(SystemExit) as exc:
            bridge._cmd_stop(argparse.Namespace(nick="local-fork"))
        assert exc.value.code == 1
        assert os.path.exists(pid_path)
        err = capsys.readouterr().err
        assert "did not exit" in err


class TestRunDirPermissions:
    """Qodo PR #51 #5: ``_run_dir`` must always end up at 0o700, even
    when the directory pre-exists with broader permissions."""

    def test_new_dir_is_0o700(self, fake_home) -> None:
        path = bridge._run_dir()
        mode = os.stat(path).st_mode & 0o777
        assert mode == 0o700

    def test_preexisting_lax_dir_is_chmodded_to_0o700(self, fake_home) -> None:
        """A pre-existing dir at 0o755 (umask leak / earlier deploy)
        is tightened on next ``_run_dir`` call."""
        path = os.path.expanduser("~/.culture/run")
        os.makedirs(path, mode=0o755, exist_ok=True)
        os.chmod(path, 0o755)  # in case the makedirs mode was masked
        assert os.stat(path).st_mode & 0o777 == 0o755

        bridge._run_dir()
        assert os.stat(path).st_mode & 0o777 == 0o700


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

    def test_status_labels_live_stale_broken_reused(self, fake_home, monkeypatch, capsys) -> None:
        """Status enumerates every PID file with one of four labels:
        running / stale / broken / reused (Qodo PR #51 #3)."""
        os.makedirs(bridge._run_dir(), exist_ok=True)
        with open(bridge._pid_path("alpha"), "w") as fh:
            fh.write("100")
        with open(bridge._pid_path("beta"), "w") as fh:
            fh.write("200")
        with open(bridge._pid_path("gamma"), "w") as fh:
            fh.write("not-a-number")
        with open(bridge._pid_path("delta"), "w") as fh:
            fh.write("400")

        # alpha=running (alive + culture), beta=stale (dead),
        # gamma=broken (unparseable), delta=reused (alive + non-culture).
        def _alive(pid):
            return pid in (100, 400)

        def _culture(pid):
            return pid == 100  # only alpha is a real bridge

        monkeypatch.setattr(bridge, "is_process_alive", _alive)
        monkeypatch.setattr(bridge, "is_culture_process", _culture)

        bridge._cmd_status(argparse.Namespace())
        out = capsys.readouterr().out
        assert "alpha" in out and "running" in out and "100" in out
        assert "beta" in out and "stale" in out and "200" in out
        assert "gamma" in out and "broken" in out
        assert "delta" in out and "reused" in out and "400" in out


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
