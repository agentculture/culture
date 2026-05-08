"""Tests for `culture console` port-conflict UX and the `stop` verb.

Covers per-port pidfile slots, side-by-side independence, the
--flag=value normaliser, port-bound precondition, fail-closed SIGKILL
re-validation, and the `stop` verb's `--web-port` flag.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
from unittest.mock import patch

import pytest

from culture import pidfile
from culture.cli import console


@pytest.fixture
def pid_dir(tmp_path):
    """Redirect ~/.culture/pids to a tmpdir for each test."""
    with patch("culture.pidfile.PID_DIR", str(tmp_path)):
        yield tmp_path


def _slot(port: int) -> str:
    return console._pid_slot(port)


# --- argv parsing ---------------------------------------------------------


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
        # server_name is no longer derived from nick — caller passes it
        # explicitly. The parser leaves it None.
        assert target == {
            "server_name": None,
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

    def test_invalid_web_port_falls_back_to_default(self):
        port, _target = console._parse_serve_argv(
            ["serve", "--nick", "spark-ada", "--web-port", "not-a-number"]
        )
        assert port == 8765

    def test_equals_form_normalised(self):
        """`--web-port=9000` is irc-lens-legal and must be parsed by the shim."""
        port, target = console._parse_serve_argv(
            ["serve", "--nick=spark-ada", "--web-port=9000", "--host=127.0.0.1"]
        )
        assert port == 9000
        assert target["nick"] == "spark-ada"
        assert target["host"] == "127.0.0.1"

    def test_mixed_forms(self):
        """Mix of two-token and equals form in one argv."""
        port, target = console._parse_serve_argv(
            ["serve", "--nick", "spark-ada", "--web-port=9000"]
        )
        assert port == 9000
        assert target["nick"] == "spark-ada"


class TestNormaliseArgv:
    def test_double_dash_alone_is_preserved(self):
        # `--` is a positional separator, not a flag — leave it alone.
        assert console._normalise_argv(["--", "x"]) == ["--", "x"]

    def test_short_flag_with_equals_is_preserved(self):
        # We only split long flags. `-x=y` is unusual and risky to split.
        assert console._normalise_argv(["-x=y"]) == ["-x=y"]


# --- pidfile lifecycle ----------------------------------------------------


class TestRegisterState:
    def test_writes_per_port_pid_port_and_sidecar(self, pid_dir):
        target = {
            "server_name": "spark",
            "nick": "spark-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
            "web_port": 8765,
        }
        console._register_state(8765, target)
        assert pidfile.read_pid("console-8765") == os.getpid()
        assert pidfile.read_port("console-8765") == 8765
        sidecar = json.loads((pid_dir / "console-8765.json").read_text())
        assert sidecar["server_name"] == "spark"
        assert sidecar["pid"] == os.getpid()
        # No global slot is touched — old code wrote `console.{pid,port,json}`.
        assert pidfile.read_pid("console") is None
        assert not (pid_dir / "console.json").exists()

    def test_side_by_side_does_not_clobber(self, pid_dir):
        """Two consoles on different ports own independent slots."""
        t1 = {
            "server_name": "spark",
            "nick": "spark-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
            "web_port": 8765,
        }
        t2 = {
            "server_name": "thor",
            "nick": "thor-bob",
            "host": "127.0.0.1",
            "irc_port": 6668,
            "web_port": 8766,
        }
        console._register_state(8765, t1)
        console._register_state(8766, t2)
        # Both slots present, both with the current process pid.
        assert pidfile.read_pid("console-8765") == os.getpid()
        assert pidfile.read_pid("console-8766") == os.getpid()
        # Cleanup of one does not touch the other.
        console._cleanup_state(8765)
        assert pidfile.read_pid("console-8765") is None
        assert pidfile.read_pid("console-8766") == os.getpid()
        assert (pid_dir / "console-8766.json").exists()

    def test_cleanup_state_removes_all_three_for_port(self, pid_dir):
        target = {
            "server_name": "spark",
            "nick": "spark-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
            "web_port": 8765,
        }
        console._register_state(8765, target)
        console._cleanup_state(8765)
        assert pidfile.read_pid("console-8765") is None
        assert pidfile.read_port("console-8765") is None
        assert not (pid_dir / "console-8765.json").exists()

    def test_cleanup_is_idempotent(self, pid_dir):
        # Nothing to remove — must not raise.
        console._cleanup_state(8765)


# --- _check_port_conflict -------------------------------------------------


def _seed_culture_console(pid_dir, web_port: int, sidecar: dict) -> int:
    """Pretend a culture-owned console is running on ``web_port``."""
    pid = os.getpid()
    pidfile.write_pid(_slot(web_port), pid)
    pidfile.write_port(_slot(web_port), web_port)
    (pid_dir / f"console-{web_port}.json").write_text(json.dumps({"pid": pid, **sidecar}))
    return pid


class TestCheckPortConflict:
    def test_port_free_short_circuits(self, pid_dir):
        """Port not bound -> return without exit, regardless of pidfile state."""
        with (
            patch.object(console, "_port_in_use", return_value=False),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})

    def test_port_free_with_stale_pidfile_cleans_up(self, pid_dir):
        """Port not bound + stale pidfile -> cleaned up."""
        pidfile.write_pid(_slot(8765), 4_000_000)
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text(json.dumps({"server_name": "x"}))
        with (
            patch.object(console, "_port_in_use", return_value=False),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(8765, {"server_name": "spark", "nick": "spark-ada"})
        assert pidfile.read_pid(_slot(8765)) is None
        assert not (pid_dir / "console-8765.json").exists()

    def test_same_target_exits_zero(self, pid_dir, capsys):
        sidecar = {
            "server_name": "spark",
            "nick": "spark-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
        }
        _seed_culture_console(pid_dir, 8765, sidecar)
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch.object(console, "_port_in_use", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(
                8765,
                {
                    "server_name": "spark",
                    "nick": "spark-ada",
                    "host": "127.0.0.1",
                    "irc_port": 6667,
                },
            )
        assert excinfo.value.code == 0
        err = capsys.readouterr().err
        assert "already running" in err
        assert "http://127.0.0.1:8765/" in err

    def test_same_target_with_no_server_name_still_matches(self, pid_dir, capsys):
        """Pure-passthrough re-runs (no resolved server_name) should still hit the
        exit-0 path when nick/host/irc_port match."""
        sidecar = {
            "server_name": None,
            "nick": "lens",
            "host": "127.0.0.1",
            "irc_port": 6667,
        }
        _seed_culture_console(pid_dir, 8765, sidecar)
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch.object(console, "_port_in_use", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(
                8765,
                {
                    "server_name": None,
                    "nick": "lens",
                    "host": "127.0.0.1",
                    "irc_port": 6667,
                },
            )
        assert excinfo.value.code == 0

    def test_same_nick_different_host_treated_as_different_target(self, pid_dir, capsys):
        sidecar = {
            "server_name": None,
            "nick": "lens",
            "host": "127.0.0.1",
            "irc_port": 6667,
        }
        _seed_culture_console(pid_dir, 8765, sidecar)
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch.object(console, "_port_in_use", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(
                8765,
                {
                    "server_name": None,
                    "nick": "lens",
                    "host": "remote.invalid",  # different host (RFC 2606 reserved name)
                    "irc_port": 6667,
                },
            )
        assert excinfo.value.code == 1

    def test_different_target_exits_one_with_hint(self, pid_dir, capsys):
        sidecar = {
            "server_name": "thor",
            "nick": "thor-ada",
            "host": "127.0.0.1",
            "irc_port": 6667,
        }
        _seed_culture_console(pid_dir, 8765, sidecar)
        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch.object(console, "_port_in_use", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(
                8765,
                {
                    "server_name": "spark",
                    "nick": "spark-ada",
                    "host": "127.0.0.1",
                    "irc_port": 6667,
                },
            )
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "'thor'" in err
        assert "thor-ada" in err
        assert "culture console stop --web-port 8765" in err
        assert "--web-port 8766" in err
        assert "culture console spark" in err

    def test_stale_pidfile_dead_process_is_cleaned(self, pid_dir):
        """Port bound, but our pidfile points at a dead PID — clean up + report on owner."""
        pidfile.write_pid(_slot(8765), 4_000_000)
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text(json.dumps({"server_name": "x"}))
        with (
            patch.object(console, "_port_in_use", return_value=True),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            # Foreign owner, not irc-lens shape -> falls through (no exit).
            console._check_port_conflict(
                8765,
                {
                    "server_name": "spark",
                    "nick": "spark-ada",
                    "host": "127.0.0.1",
                    "irc_port": 6667,
                },
            )
        # Stale slot got cleaned up before falling through.
        assert pidfile.read_pid(_slot(8765)) is None

    def test_stale_pidfile_pid_belongs_to_non_culture_process(self, pid_dir):
        pidfile.write_pid(_slot(8765), os.getpid())
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text(json.dumps({"server_name": "x"}))
        with (
            patch("culture.pidfile.is_culture_process", return_value=False),
            patch.object(console, "_port_in_use", return_value=True),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(
                8765,
                {
                    "server_name": "spark",
                    "nick": "spark-ada",
                    "host": "127.0.0.1",
                    "irc_port": 6667,
                },
            )
        assert pidfile.read_pid(_slot(8765)) is None

    def test_foreign_port_owner_falls_through(self, pid_dir):
        """No pidfile, port bound, not irc-lens — let irc-lens emit its own error."""
        with (
            patch.object(console, "_port_in_use", return_value=True),
            patch.object(console, "_looks_like_irc_lens", return_value=False),
        ):
            console._check_port_conflict(
                8765,
                {
                    "server_name": "spark",
                    "nick": "spark-ada",
                    "host": "127.0.0.1",
                    "irc_port": 6667,
                },
            )

    def test_foreign_irc_lens_without_pidfile_exits_with_hint(self, pid_dir, capsys):
        with (
            patch.object(console, "_port_in_use", return_value=True),
            patch.object(console, "_looks_like_irc_lens", return_value=True),
            pytest.raises(SystemExit) as excinfo,
        ):
            console._check_port_conflict(
                8765,
                {
                    "server_name": "spark",
                    "nick": "spark-ada",
                    "host": "127.0.0.1",
                    "irc_port": 6667,
                },
            )
        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        assert "irc-lens" in err
        assert "wasn't started by `culture console`" in err
        assert "ss -tlnp" in err


# --- stop verb ------------------------------------------------------------


def _stop_args(argv: list[str]) -> argparse.Namespace:
    return argparse.Namespace(console_args=argv)


class TestCmdStop:
    def test_no_pidfile_returns_zero_with_message(self, pid_dir, capsys):
        rc = console._cmd_stop(_stop_args(["stop"]))
        assert rc == 0
        assert "no culture console running on port 8765" in capsys.readouterr().err

    def test_dead_pid_cleans_up_returns_zero(self, pid_dir, capsys):
        pidfile.write_pid(_slot(8765), 4_000_000)
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text("{}")
        rc = console._cmd_stop(_stop_args(["stop"]))
        assert rc == 0
        assert pidfile.read_pid(_slot(8765)) is None
        assert "dead pid" in capsys.readouterr().err

    def test_non_culture_process_refused_preserves_state(self, pid_dir, capsys):
        pidfile.write_pid(_slot(8765), os.getpid())
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text("{}")
        with patch("culture.pidfile.is_culture_process", return_value=False):
            rc = console._cmd_stop(_stop_args(["stop"]))
        assert rc == 1
        err = capsys.readouterr().err
        assert "refusing to stop" in err
        # Pidfile is preserved — we don't trash state we couldn't validate.
        assert pidfile.read_pid(_slot(8765)) == os.getpid()
        assert (pid_dir / "console-8765.json").exists()

    def test_graceful_stop_via_sigterm(self, pid_dir, capsys):
        target_pid = 12345
        pidfile.write_pid(_slot(8765), target_pid)
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text("{}")

        kill_calls: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        # Alive at the initial check, dead by the next poll.
        alive_calls = iter([True, False])

        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch("culture.pidfile.is_process_alive", side_effect=lambda _: next(alive_calls)),
            patch("os.kill", side_effect=fake_kill),
        ):
            rc = console._cmd_stop(_stop_args(["stop"]))

        assert rc == 0
        assert (target_pid, signal.SIGTERM) in kill_calls
        assert (target_pid, signal.SIGKILL) not in kill_calls
        assert pidfile.read_pid(_slot(8765)) is None
        assert "stopped culture console" in capsys.readouterr().err

    def test_force_stop_escalates_to_sigkill(self, pid_dir, capsys):
        target_pid = 12345
        pidfile.write_pid(_slot(8765), target_pid)
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text("{}")

        kill_calls: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        with (
            patch("culture.pidfile.is_culture_process", return_value=True),
            patch("culture.pidfile.is_process_alive", return_value=True),
            patch("os.kill", side_effect=fake_kill),
            patch.object(console, "_STOP_GRACE_SECONDS", 0.05),
        ):
            rc = console._cmd_stop(_stop_args(["stop"]))

        assert rc == 0
        assert (target_pid, signal.SIGTERM) in kill_calls
        assert (target_pid, signal.SIGKILL) in kill_calls
        assert pidfile.read_pid(_slot(8765)) is None
        assert "force-stopped" in capsys.readouterr().err

    def test_sigkill_skipped_if_pid_recycled_to_non_culture(self, pid_dir, capsys):
        """Fail-closed escalation: re-validate is_culture_process before SIGKILL.

        Simulates PID reuse during the grace window — the original
        culture process exited (so SIGTERM didn't kill it because it was
        already gone), but the kernel recycled the PID to an unrelated
        process by the time we'd escalate. We must not fire SIGKILL at it.
        """
        target_pid = 12345
        pidfile.write_pid(_slot(8765), target_pid)
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text("{}")

        kill_calls: list[tuple[int, int]] = []

        def fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        # First call (pre-SIGTERM): True (it's our culture console).
        # All subsequent calls (escalation re-validation): False (recycled).
        culture_calls = iter([True, False, False])

        with (
            patch("culture.pidfile.is_culture_process", side_effect=lambda _: next(culture_calls)),
            patch("culture.pidfile.is_process_alive", return_value=True),
            patch("os.kill", side_effect=fake_kill),
            patch.object(console, "_STOP_GRACE_SECONDS", 0.05),
        ):
            rc = console._cmd_stop(_stop_args(["stop"]))

        assert rc == 0
        assert (target_pid, signal.SIGTERM) in kill_calls
        # SIGKILL withheld — recycled PID protection.
        assert (target_pid, signal.SIGKILL) not in kill_calls
        assert pidfile.read_pid(_slot(8765)) is None

    def test_stop_with_explicit_web_port(self, pid_dir, capsys):
        """`culture console stop --web-port 9000` only touches the 9000 slot."""
        # Two side-by-side consoles.
        pidfile.write_pid(_slot(8765), 4_000_001)
        pidfile.write_port(_slot(8765), 8765)
        (pid_dir / "console-8765.json").write_text("{}")
        pidfile.write_pid(_slot(9000), 4_000_002)
        pidfile.write_port(_slot(9000), 9000)
        (pid_dir / "console-9000.json").write_text("{}")

        rc = console._cmd_stop(_stop_args(["stop", "--web-port", "9000"]))
        assert rc == 0
        # 9000 cleaned (dead pid path).
        assert pidfile.read_pid(_slot(9000)) is None
        # 8765 untouched.
        assert pidfile.read_pid(_slot(8765)) == 4_000_001

    def test_stop_with_equals_form_web_port(self, pid_dir):
        pidfile.write_pid(_slot(9000), 4_000_002)
        pidfile.write_port(_slot(9000), 9000)
        (pid_dir / "console-9000.json").write_text("{}")
        rc = console._cmd_stop(_stop_args(["stop", "--web-port=9000"]))
        assert rc == 0
        assert pidfile.read_pid(_slot(9000)) is None


# --- dispatch routing -----------------------------------------------------


class TestDispatchStopRouting:
    def test_stop_short_circuits_before_passthrough(self, pid_dir):
        ns = argparse.Namespace(console_args=["stop"])
        with pytest.raises(SystemExit) as excinfo:
            console.dispatch(ns)
        assert excinfo.value.code == 0

    def test_non_stop_verb_proceeds_to_passthrough(self, pid_dir):
        ns = argparse.Namespace(console_args=["explain"])
        with patch.object(console._passthrough, "run") as mock_run:
            console.dispatch(ns)
        mock_run.assert_called_once()
        forwarded_argv = mock_run.call_args[0][1]
        assert forwarded_argv == ["explain"]

    def test_empty_console_args_does_not_crash_on_index(self, pid_dir):
        """Sonar S6466 guard: dispatch must not IndexError on empty argv."""
        ns = argparse.Namespace(console_args=[])
        with patch.object(console._passthrough, "run"):
            # Will try to resolve a default server, which raises SystemExit
            # — that's fine; we only care that no IndexError fires here.
            with pytest.raises(SystemExit):
                console.dispatch(ns)


# --- run_serve cleanup ----------------------------------------------------


class TestRunServeCleanup:
    def test_cleanup_runs_after_irc_lens_returns(self, pid_dir):
        """`_run_serve` must clean up the pidfile when irc-lens exits cleanly."""
        argv = ["serve", "--host", "127.0.0.1", "--port", "6667", "--nick", "spark-ada"]
        with (
            patch.object(console, "_check_port_conflict"),
            patch.object(console, "_ensure_default_irc_lens_config"),
            patch.object(console, "_invoke_irc_lens", return_value=0) as mock_lens,
        ):
            rc = console._run_serve(argv, server_name="spark")
        assert rc == 0
        mock_lens.assert_called_once_with(argv)
        assert pidfile.read_pid(_slot(8765)) is None
        assert not (pid_dir / "console-8765.json").exists()

    def test_cleanup_runs_after_irc_lens_raises(self, pid_dir):
        """Cleanup must also run when irc-lens raises (e.g. SystemExit)."""
        argv = ["serve", "--host", "127.0.0.1", "--port", "6667", "--nick", "spark-ada"]
        with (
            patch.object(console, "_check_port_conflict"),
            patch.object(console, "_ensure_default_irc_lens_config"),
            patch.object(console, "_invoke_irc_lens", side_effect=SystemExit(1)),
            pytest.raises(SystemExit),
        ):
            console._run_serve(argv, server_name="spark")
        assert pidfile.read_pid(_slot(8765)) is None

    def test_server_name_overrides_parsed_target(self, pid_dir):
        """Hyphenated server names round-trip via the `server_name` param,
        not via `nick.split('-')`."""
        argv = [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "6667",
            "--nick",
            "my-server-ada",
        ]
        with (
            patch.object(console, "_check_port_conflict"),
            patch.object(console, "_ensure_default_irc_lens_config"),
            patch.object(console, "_invoke_irc_lens", return_value=0),
        ):
            console._run_serve(argv, server_name="my-server")
        # While we still inside `_invoke_irc_lens` the sidecar would be
        # written; cleanup runs after, so we have to check via the patched
        # `_check_port_conflict` indirectly. Instead, verify _register_state
        # wrote the right server_name by re-running with cleanup disabled:
        with (
            patch.object(console, "_check_port_conflict"),
            patch.object(console, "_ensure_default_irc_lens_config"),
            patch.object(console, "_invoke_irc_lens", return_value=0),
            patch.object(console, "_cleanup_state"),
        ):
            console._run_serve(argv, server_name="my-server")
        sidecar = json.loads((pid_dir / "console-8765.json").read_text())
        assert sidecar["server_name"] == "my-server"
        assert sidecar["nick"] == "my-server-ada"


class TestArgvHasFlag:
    def test_long_form_present(self):
        assert console._argv_has_flag(["serve", "--config", "/tmp/x.yaml"], "--config")

    def test_equals_form_present(self):
        assert console._argv_has_flag(["serve", "--config=/tmp/x.yaml"], "--config")

    def test_absent(self):
        assert not console._argv_has_flag(["serve", "--host", "127.0.0.1"], "--config")

    def test_substring_does_not_match(self):
        # `--config-path` should not match `--config`.
        assert not console._argv_has_flag(["serve", "--config-path", "x"], "--config")


class TestEnsureDefaultIrcLensConfig:
    def test_skips_when_default_path_exists(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("placeholder")
        with (
            patch("irc_lens.config.default_config_path", return_value=cfg),
            patch.object(console, "_invoke_irc_lens") as mock_lens,
        ):
            console._ensure_default_irc_lens_config()
        mock_lens.assert_not_called()

    def test_initializes_when_default_path_missing(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        with (
            patch("irc_lens.config.default_config_path", return_value=cfg),
            patch.object(console, "_invoke_irc_lens") as mock_lens,
        ):
            console._ensure_default_irc_lens_config()
        mock_lens.assert_called_once_with(["config", "init", "--path", str(cfg)])


class TestRunServeAutoInitsConfig:
    def test_auto_init_runs_when_no_config_flag(self, pid_dir):
        argv = ["serve", "--host", "127.0.0.1", "--port", "6667", "--nick", "spark-ada"]
        with (
            patch.object(console, "_check_port_conflict"),
            patch.object(console, "_ensure_default_irc_lens_config") as mock_ensure,
            patch.object(console, "_invoke_irc_lens", return_value=0),
        ):
            console._run_serve(argv, server_name="spark")
        mock_ensure.assert_called_once()

    def test_auto_init_skipped_when_user_passed_config(self, pid_dir):
        argv = [
            "serve",
            "--config",
            "/tmp/custom.yaml",
            "--host",
            "127.0.0.1",
            "--port",
            "6667",
            "--nick",
            "spark-ada",
        ]
        with (
            patch.object(console, "_check_port_conflict"),
            patch.object(console, "_ensure_default_irc_lens_config") as mock_ensure,
            patch.object(console, "_invoke_irc_lens", return_value=0),
        ):
            console._run_serve(argv, server_name="spark")
        mock_ensure.assert_not_called()

    def test_auto_init_skipped_when_user_passed_config_equals_form(self, pid_dir):
        argv = [
            "serve",
            "--config=/tmp/custom.yaml",
            "--host",
            "127.0.0.1",
            "--port",
            "6667",
            "--nick",
            "spark-ada",
        ]
        with (
            patch.object(console, "_check_port_conflict"),
            patch.object(console, "_ensure_default_irc_lens_config") as mock_ensure,
            patch.object(console, "_invoke_irc_lens", return_value=0),
        ):
            console._run_serve(argv, server_name="spark")
        mock_ensure.assert_not_called()
