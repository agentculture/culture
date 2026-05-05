"""Tests for `culture console` port-conflict UX and the `stop` verb.

Covers the pidfile lifecycle, same/different-target detection,
stale-pidfile cleanup, foreign-port fall-through, and ``culture
console stop`` happy path + idempotence.
"""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from unittest.mock import patch

import pytest

from culture import pidfile
from culture.cli import console


@pytest.fixture
def pid_dir(tmp_path):
    """Redirect ~/.culture/pids to a tmpdir and clear atexit between runs."""
    with patch("culture.pidfile.PID_DIR", str(tmp_path)):
        yield tmp_path


# --- _parse_serve_argv ----------------------------------------------------


class TestParseServeArgv:
    def test_full_argv_extracts_all_fields(self):
        argv = [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "6667",
            "--nick",
            "spark-ada",
            "--web-port",
            "9000",
        ]
        port, target = console._parse_serve_argv(argv)
        assert port == 9000
        assert target == {
            "server_name": "spark",
            "nick": "spark-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
            "web_port": 9000,
        }

    def test_default_web_port_when_unspecified(self):
        port, target = console._parse_serve_argv(
            ["serve", "--host", "127.0.0.1", "--port", "6667", "--nick", "spark-ada"]
        )
        assert port == 8765
        assert target["web_port"] == 8765

    def test_unknown_flags_are_ignored(self):
        port, target = console._parse_serve_argv(
            ["serve", "--nick", "spark-ada", "--icon", "🐢", "--open"]
        )
        assert port == 8765
        assert target["nick"] == "spark-ada"

    def test_nick_without_dash_yields_no_server_name(self):
        _port, target = console._parse_serve_argv(["serve", "--nick", "lonelyhost"])
        assert target["server_name"] is None
        assert target["nick"] == "lonelyhost"

    def test_invalid_web_port_falls_back_to_default(self):
        port, _target = console._parse_serve_argv(
            ["serve", "--nick", "spark-ada", "--web-port", "not-a-number"]
        )
        assert port == 8765


# --- pidfile lifecycle ----------------------------------------------------


class TestRegisterState:
    def test_writes_pid_port_and_sidecar(self, pid_dir):
        target = {
            "server_name": "spark",
            "nick": "spark-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
            "web_port": 8765,
        }
        console._register_state(8765, target)
        assert pidfile.read_pid("console") == os.getpid()
        assert pidfile.read_port("console") == 8765
        sidecar = json.loads((pid_dir / "console.json").read_text())
        assert sidecar["server_name"] == "spark"
        assert sidecar["nick"] == "spark-ada"
        assert sidecar["pid"] == os.getpid()

    def test_cleanup_state_removes_all_three(self, pid_dir):
        target = {
            "server_name": "spark",
            "nick": "spark-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
            "web_port": 8765,
        }
        console._register_state(8765, target)
        console._cleanup_state()
        assert pidfile.read_pid("console") is None
        assert pidfile.read_port("console") is None
        assert not (pid_dir / "console.json").exists()

    def test_cleanup_is_idempotent(self, pid_dir):
        # Nothing to remove — must not raise.
        console._cleanup_state()


# --- _check_port_conflict -------------------------------------------------


def _fake_culture_pid_alive(pid_dir, sidecar: dict, web_port: int = 8765) -> int:
    """Simulate a running culture-owned console by writing pid/port/sidecar."""
    pid = os.getpid()  # always alive, always the current "culture" process
    pidfile.write_pid("console", pid)
    pidfile.write_port("console", web_port)
    (pid_dir / "console.json").write_text(json.dumps({"pid": pid, **sidecar}))
    return pid


class TestCheckPortConflict:
    def test_no_pidfile_no_port_bound_returns(self, pid_dir):
        with (
            patch.object(console, "_port_in_use", return_value=False),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
            # Should not raise SystemExit.

    def test_same_target_exits_zero(self, pid_dir, capsys):
        sidecar = {"server_name": "spark", "nick": "spark-ada", "web_port": 8765}
        _fake_culture_pid_alive(pid_dir, sidecar)
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
        assert excinfo.value.code == 0
        err = capsys.readouterr().err
        assert "already running" in err
        assert "http://127.0.0.1:8765/" in err

    def test_different_target_exits_one_with_hint(self, pid_dir, capsys):
        sidecar = {"server_name": "thor", "nick": "thor-ada", "web_port": 8765}
        _fake_culture_pid_alive(pid_dir, sidecar)
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "'thor'" in err
        assert "thor-ada" in err
        assert "culture console stop" in err
        assert "--web-port 8766" in err
        assert "culture console spark" in err

    def test_stale_pidfile_dead_process_is_cleaned(self, pid_dir):
        # Use a PID that's almost certainly dead.
        dead_pid = 4_000_000
        pidfile.write_pid("console", dead_pid)
        pidfile.write_port("console", 8765)
        (pid_dir / "console.json").write_text(json.dumps({"server_name": "x", "nick": "x-y"}))
        with (
            patch.object(console, "_port_in_use", return_value=False),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
        assert pidfile.read_pid("console") is None
        assert not (pid_dir / "console.json").exists()

    def test_stale_pidfile_pid_belongs_to_non_culture_process(self, pid_dir):
        """Pidfile alive but not a culture process — treat as stale."""
        pidfile.write_pid("console", os.getpid())
        pidfile.write_port("console", 8765)
        (pid_dir / "console.json").write_text(json.dumps({"server_name": "x", "nick": "x-y"}))
        with (
            patch("culture.pidfile.is_culture_process", return_value=False),
            patch.object(console, "_port_in_use", return_value=False),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
        assert pidfile.read_pid("console") is None

    def test_foreign_port_owner_falls_through(self, pid_dir):
        """No pidfile, port bound, not irc-lens — let irc-lens emit its own error."""
        with (
            patch.object(console, "_port_in_use", return_value=True),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
            # No SystemExit — caller proceeds to irc-lens.

    def test_foreign_irc_lens_without_pidfile_exits_with_hint(self, pid_dir, capsys):
        with (
            patch.object(console, "_port_in_use", return_value=True),
            patch.object(console, "_looks_like_irc_lens", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "irc-lens" in err
        assert "wasn't started by `culture console`" in err
        assert "ss -tlnp" in err

    def test_pidfile_recorded_for_different_port_does_not_match(self, pid_dir):
        """Recorded port is 9000, requested is 8765 — pidfile doesn't apply."""
        pidfile.write_pid("console", os.getpid())
        pidfile.write_port("console", 9000)
        (pid_dir / "console.json").write_text(
            json.dumps({"server_name": "spark", "nick": "spark-ada"})
        )
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch.object(console, "_port_in_use", return_value=False),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            # Should not exit — recorded port mismatches requested port.
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})


# --- stop verb ------------------------------------------------------------


class TestCmdStop:
    def test_no_pidfile_returns_zero_with_message(self, pid_dir, capsys):
        rc = console._cmd_stop()
        assert rc == 0
        assert "no culture console running" in capsys.readouterr().err

    def test_dead_pid_cleans_up_returns_zero(self, pid_dir, capsys):
        pidfile.write_pid("console", 4_000_000)
        pidfile.write_port("console", 8765)
        (pid_dir / "console.json").write_text("{}")
        rc = console._cmd_stop()
        assert rc == 0
        assert pidfile.read_pid("console") is None
        assert "dead pid" in capsys.readouterr().err

    def test_non_culture_process_refused(self, pid_dir, capsys):
        pidfile.write_pid("console", os.getpid())
        pidfile.write_port("console", 8765)
        (pid_dir / "console.json").write_text("{}")
        with patch("culture.pidfile.is_culture_process", return_value=False):
            rc = console._cmd_stop()
        assert rc == 1
        err = capsys.readouterr().err
        assert "refusing to stop" in err
        # Pidfile is preserved — we don't trash state we didn't validate.
        assert pidfile.read_pid("console") == os.getpid()

    def test_graceful_stop_via_sigterm(self, pid_dir, capsys):
        # We pretend pid X is a culture process and dies after SIGTERM.
        target_pid = 12345
        pidfile.write_pid("console", target_pid)
        pidfile.write_port("console", 8765)
        (pid_dir / "console.json").write_text("{}")

        kill_calls: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        # is_process_alive: True before SIGTERM, False on next poll.
        alive_calls = iter([True, False])

        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch("culture.pidfile.is_process_alive", side_effect=lambda _: next(alive_calls)),
            patch("os.kill", side_effect=fake_kill),
        ):
            rc = console._cmd_stop()

        assert rc == 0
        assert (target_pid, signal.SIGTERM) in kill_calls
        assert (target_pid, signal.SIGKILL) not in kill_calls
        assert pidfile.read_pid("console") is None
        assert "stopped culture console" in capsys.readouterr().err

    def test_force_stop_escalates_to_sigkill(self, pid_dir, capsys):
        target_pid = 12345
        pidfile.write_pid("console", target_pid)
        pidfile.write_port("console", 8765)
        (pid_dir / "console.json").write_text("{}")

        kill_calls: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        # Always alive — forces escalation. Use a tiny grace period to keep tests fast.
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch("culture.pidfile.is_process_alive", return_value=True),
            patch("os.kill", side_effect=fake_kill),
            patch.object(console, "_STOP_GRACE_SECONDS", 0.05),
        ):
            rc = console._cmd_stop()

        assert rc == 0
        assert (target_pid, signal.SIGTERM) in kill_calls
        assert (target_pid, signal.SIGKILL) in kill_calls
        assert pidfile.read_pid("console") is None
        assert "force-stopped" in capsys.readouterr().err


# --- dispatch routing -----------------------------------------------------


class TestDispatchStopRouting:
    def test_stop_short_circuits_before_passthrough(self, pid_dir):
        import argparse as _ap

        # No pidfile → _cmd_stop returns 0 → dispatch sys.exits(0).
        ns = _ap.Namespace(console_args=["stop"])
        with pytest.raises(SystemExit) as excinfo:
            console.dispatch(ns)
        assert excinfo.value.code == 0

    def test_non_stop_verb_proceeds_to_passthrough(self, pid_dir):
        import argparse as _ap

        ns = _ap.Namespace(console_args=["explain"])
        # Patch _passthrough.run to capture the call instead of running irc-lens.
        with patch.object(console._passthrough, "run") as mock_run:
            console.dispatch(ns)
        mock_run.assert_called_once()
        # explain is passed through verbatim — no rewrite.
        forwarded_argv = mock_run.call_args[0][1]
        assert forwarded_argv == ["explain"]
