"""Tests for culture_core.pidfile — PID file management and process validation."""

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from culture_core.pidfile import (
    _process_identity,
    classify_pidfile,
    is_culture_process,
    is_process_alive,
    list_servers,
    read_default_server,
    read_pid,
    read_port,
    remove_pid,
    remove_port,
    rename_pid,
    terminate_verified,
    write_default_server,
    write_pid,
    write_port,
)


@pytest.fixture()
def pid_dir(tmp_path):
    """Use a temporary directory for PID files."""
    with patch("culture_core.pidfile.PID_DIR", str(tmp_path)):
        yield tmp_path


class TestWriteReadRemove:
    def test_write_and_read(self, pid_dir):
        write_pid("agent-bot", 12345)
        assert read_pid("agent-bot") == 12345
        assert (pid_dir / "agent-bot.pid").read_text() == "12345"

    def test_sibling_readers_accept_pid(self, pid_dir, monkeypatch):
        from agentirc._internal import pidfile as agentirc_pidfile
        from cultureagent import pidfile as cultureagent_pidfile

        monkeypatch.setattr(agentirc_pidfile, "PID_DIR", str(pid_dir))
        monkeypatch.setattr(cultureagent_pidfile, "PID_DIR", str(pid_dir))
        write_pid("agent-bot", 12345)
        assert agentirc_pidfile.read_pid("agent-bot") == 12345
        assert cultureagent_pidfile.read_pid("agent-bot") == 12345

    def test_read_missing(self, pid_dir):
        assert read_pid("nonexistent") is None

    def test_plain_integer_read(self, pid_dir):
        (pid_dir / "server-old.pid").write_text("12345\n")
        assert read_pid("server-old") == 12345

    def test_remove(self, pid_dir):
        write_pid("agent-bot", os.getpid())
        remove_pid("agent-bot")
        assert read_pid("agent-bot") is None
        assert not (pid_dir / "agent-bot.identity").exists()

    def test_remove_missing_is_noop(self, pid_dir):
        remove_pid("nonexistent")  # should not raise

    def test_expected_pid_does_not_remove_replacement(self, pid_dir):
        (pid_dir / "server-spark.pid").write_text("111")
        assert classify_pidfile("server-spark", 222) == "changed"
        remove_pid("server-spark", 222)
        assert (pid_dir / "server-spark.pid").read_text() == "111"

    def test_empty_pidfile_is_stale(self, pid_dir):
        (pid_dir / "server-spark.pid").write_text("")
        assert classify_pidfile("server-spark") == "stale"

    def test_non_utf8_pidfile_is_stale(self, pid_dir):
        (pid_dir / "server-bad.pid").write_bytes(b"\xff\xfe\x80")
        assert classify_pidfile("server-bad") == "stale"

    def test_atomic_write_leaves_no_temporary_files(self, pid_dir):
        write_pid("agent-bot", os.getpid())
        assert {path.name for path in pid_dir.iterdir()} == {
            "agent-bot.identity",
            "agent-bot.pid",
        }


class TestIsProcessAlive:
    def test_current_process_alive(self):
        assert is_process_alive(os.getpid()) is True

    def test_nonexistent_pid(self):
        # Use a very high PID unlikely to exist.
        assert is_process_alive(4_000_000) is False


class TestIsCultureProcess:
    @pytest.mark.skipif(
        not Path("/proc/self/cmdline").exists(),
        reason="/proc not available",
    )
    def test_current_process_is_python(self):
        # We're running under pytest — cmdline won't contain "culture"
        # as an exact argv token, so this should return False.
        result = is_culture_process(os.getpid())
        assert isinstance(result, bool)

    def test_cmdline_with_culture_token(self):
        """Exact argv token 'culture' is recognized (e.g. -m culture)."""
        raw = b"/usr/bin/python3\x00-m\x00culture\x00start\x00"
        with (
            patch("culture_core.pidfile.Path") as mock_path,
            patch("culture_core.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is True

    def test_cmdline_with_culture_basename(self):
        """argv[0] with basename 'culture' is recognized."""
        raw = b"/home/user/.local/bin/culture\x00server\x00start\x00"
        with (
            patch("culture_core.pidfile.Path") as mock_path,
            patch("culture_core.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is True

    def test_cmdline_with_culture_core_module(self):
        """'-m culture_core' token is recognized (new python -m culture_core form)."""
        raw = b"/usr/bin/python3\x00-m\x00culture_core\x00server\x00start\x00"
        with (
            patch("culture_core.pidfile.Path") as mock_path,
            patch("culture_core.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is True

    def test_cmdline_with_culture_core_basename(self):
        """argv[0] with basename 'culture-core' is recognized."""
        raw = b"/home/user/.local/bin/culture-core\x00server\x00start\x00"
        with (
            patch("culture_core.pidfile.Path") as mock_path,
            patch("culture_core.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is True

    def test_cmdline_without_culture(self):
        """Process with unrelated cmdline is rejected."""
        raw = b"/sbin/init\x00--system\x00"
        with (
            patch("culture_core.pidfile.Path") as mock_path,
            patch("culture_core.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is False

    def test_cmdline_substring_not_matched(self):
        """Substring 'culture' inside another token is NOT matched."""
        raw = b"/usr/bin/agriculture-daemon\x00--flag\x00"
        with (
            patch("culture_core.pidfile.Path") as mock_path,
            patch("culture_core.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is False

    def test_no_proc_returns_true(self):
        """When /proc doesn't exist (macOS/Windows), assume valid."""
        with patch("culture_core.pidfile.os.path.isdir", return_value=False):
            assert is_culture_process(999) is True

    def test_oserror_on_linux_returns_false(self):
        """On Linux, read failures fail closed (return False)."""
        with (
            patch("culture_core.pidfile.Path") as mock_path,
            patch("culture_core.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.side_effect = OSError("denied")
            assert is_culture_process(999) is False

    @pytest.mark.skipif(
        not Path("/proc/self/cmdline").exists(),
        reason="/proc not available",
    )
    def test_nonexistent_pid_fails_closed(self):
        """/proc exists but PID doesn't — should return False."""
        assert is_culture_process(4_000_000) is False


# ---------------------------------------------------------------------------
# Phase 6 additions — port files, default_server, rename_pid, list_servers,
# is_process_alive PermissionError
# ---------------------------------------------------------------------------


class TestPortFiles:
    def test_write_and_read_port(self, pid_dir):
        write_port("server-spark", 6667)
        assert read_port("server-spark") == 6667

    def test_read_port_missing_returns_none(self, pid_dir):
        assert read_port("nonexistent") is None

    def test_read_port_invalid_int_returns_none(self, pid_dir):
        path = pid_dir / "server-bogus.port"
        path.write_text("not-a-number")
        assert read_port("server-bogus") is None

    def test_remove_port(self, pid_dir):
        write_port("server-spark", 6667)
        remove_port("server-spark")
        assert read_port("server-spark") is None

    def test_remove_port_missing_is_noop(self, pid_dir):
        remove_port("nonexistent")  # should not raise


class TestReadPidErrorPath:
    def test_read_pid_invalid_int_returns_none(self, pid_dir):
        path = pid_dir / "server-bogus.pid"
        path.write_text("not-a-number")
        assert read_pid("server-bogus") is None


class TestIsProcessAliveErrorPaths:
    def test_permission_error_returns_true(self):
        """If the OS says we can't signal the PID, the process still exists."""
        with patch("culture_core.pidfile.os.kill", side_effect=PermissionError("denied")):
            assert is_process_alive(1) is True

    def test_process_lookup_error_returns_false(self):
        """If os.kill raises ProcessLookupError, the PID is gone — return False.

        The high-PID variant at TestIsProcessAlive.test_nonexistent_pid is racy
        on busy CI; this monkeypatched version covers the same branch
        deterministically.
        """
        with patch("culture_core.pidfile.os.kill", side_effect=ProcessLookupError):
            assert is_process_alive(99999) is False


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
class TestClassification:
    @staticmethod
    def _spawn(tmp_path, *args):
        launcher = tmp_path / "culture"
        launcher.write_text(f"#!{sys.executable}\nimport time; time.sleep(30)\n")
        launcher.chmod(0o755)
        proc = subprocess.Popen([str(launcher), *args])
        path = Path(f"/proc/{proc.pid}/cmdline")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if path.exists() and str(launcher).encode() in path.read_bytes():
                return proc
            time.sleep(0.01)
        proc.kill()
        proc.wait()
        pytest.fail("launcher did not start")

    def test_recorded_identity_and_other_server(self, pid_dir):
        proc = self._spawn(pid_dir, "server", "start", "--name", "sbx")
        try:
            write_pid("server-spark", proc.pid)
            path = pid_dir / "server-spark.pid"
            identity_path = pid_dir / "server-spark.identity"
            pid, start, boot = identity_path.read_text().split()
            identity_path.write_text(f"{pid} {int(start) + 1} {boot}\n")
            assert classify_pidfile("server-spark") == "stale"
            identity_path.write_text(f"{pid} {start} different-boot\n")
            assert classify_pidfile("server-spark") == "stale"
        finally:
            proc.kill()
            proc.wait()

    def test_agent_recorded_identity(self, pid_dir):
        proc = self._spawn(pid_dir, "agents", "start", "ada")
        try:
            write_pid("agent-ada", proc.pid)
            assert classify_pidfile("agent-ada") == "current"
        finally:
            proc.kill()
            proc.wait()

    def test_sibling_rewrite_same_pid_keeps_sidecar(self, pid_dir, monkeypatch):
        from cultureagent import pidfile as cultureagent_pidfile

        proc = self._spawn(pid_dir, "agents", "start", "ada")
        monkeypatch.setattr(cultureagent_pidfile, "PID_DIR", str(pid_dir))
        try:
            write_pid("agent-ada", proc.pid)
            cultureagent_pidfile.write_pid("agent-ada", proc.pid)
            assert classify_pidfile("agent-ada") == "current"
            assert (pid_dir / "agent-ada.identity").exists()
        finally:
            proc.kill()
            proc.wait()

    def test_different_sidecar_pid_falls_back_to_legacy(self, pid_dir):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            write_pid("server-spark", proc.pid)
            identity_path = pid_dir / "server-spark.identity"
            _, start, boot = identity_path.read_text().split()
            identity_path.write_text(f"{proc.pid + 1} {start} {boot}\n")
            assert classify_pidfile("server-spark") == "current"
        finally:
            proc.kill()
            proc.wait()

    def test_unreadable_identity_removes_old_sidecar(self, pid_dir):
        write_pid("server-spark", os.getpid())
        assert (pid_dir / "server-spark.identity").exists()
        write_pid("server-spark", 4_000_000)
        assert (pid_dir / "server-spark.pid").read_text() == "4000000"
        assert not (pid_dir / "server-spark.identity").exists()

    def test_zombie_is_stale(self, pid_dir):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        try:
            while Path(f"/proc/{proc.pid}/stat").read_text().split(")")[-1].strip()[0] != "Z":
                time.sleep(0.01)
            write_pid("server-zombie", proc.pid)
            assert classify_pidfile("server-zombie") == "stale"
        finally:
            proc.wait()

    @pytest.mark.parametrize("error", [FileNotFoundError, ProcessLookupError])
    def test_proc_exit_race_is_stale(self, pid_dir, error):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            write_pid("server-spark", proc.pid)
            real_read_bytes = Path.read_bytes

            def missing_stat(path, *args, **kwargs):
                if str(path) == f"/proc/{proc.pid}/stat":
                    raise error
                return real_read_bytes(path, *args, **kwargs)

            with patch.object(Path, "read_bytes", missing_stat):
                assert classify_pidfile("server-spark") == "stale"
        finally:
            proc.kill()
            proc.wait()

    def test_huge_pid_is_stale(self, pid_dir):
        (pid_dir / "server-huge.pid").write_text("9" * 1000)
        assert classify_pidfile("server-huge") == "stale"

    def test_own_pid_and_thread_id_are_stale(self, pid_dir):
        write_pid("server-self", os.getpid())
        assert classify_pidfile("server-self") == "stale"
        ready, done = threading.Event(), threading.Event()
        tid = {}

        def worker():
            tid["value"] = threading.get_native_id()
            ready.set()
            done.wait(5)

        thread = threading.Thread(target=worker)
        thread.start()
        assert ready.wait(5)
        try:
            write_pid("server-thread", tid["value"])
            assert classify_pidfile("server-thread") == "stale"
        finally:
            done.set()
            thread.join()

    @pytest.mark.parametrize(
        "args, name, expected",
        [
            (("server", "start", "--name", "overview"), "overview-spark", "stale"),
            (("mesh", "overview", "--room", "server"), "server-spark", "stale"),
            (("mesh", "overview", "--serve"), "overview-spark", "current"),
            (("server", "start"), "server-spark", "current"),
            (("server", "start"), "agent-ada", "stale"),
            (("agents", "start", "ada"), "agent-ada", "current"),
        ],
    )
    def test_old_format_positional_role(self, pid_dir, args, name, expected):
        proc = self._spawn(pid_dir, *args)
        try:
            (pid_dir / f"{name}.pid").write_text(str(proc.pid))
            assert classify_pidfile(name) == expected
        finally:
            proc.kill()
            proc.wait()

    def test_tail_with_culture_words_is_not_a_launcher(self, pid_dir):
        (pid_dir / "culture").touch()
        (pid_dir / "server").touch()
        proc = subprocess.Popen(
            ["tail", "-f", "culture", "server"],
            cwd=pid_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            path = Path(f"/proc/{proc.pid}/cmdline")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and b"culture\x00server" not in path.read_bytes():
                time.sleep(0.01)
            assert is_process_alive(proc.pid)
            (pid_dir / "server-spark.pid").write_text(str(proc.pid))
            assert classify_pidfile("server-spark") == "stale"
        finally:
            proc.kill()
            proc.wait()

    def test_unrecognized_culture_launcher_is_stale(self, pid_dir):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "culture"])
        try:
            (pid_dir / "server-spark.pid").write_text(str(proc.pid))
            assert classify_pidfile("server-spark") == "stale"
        finally:
            proc.kill()
            proc.wait()

    def test_python_flag_module_launcher(self, pid_dir):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            (pid_dir / "server-spark.pid").write_text(str(proc.pid))
            real_read_bytes = Path.read_bytes

            def flagged_cmdline(path, *args, **kwargs):
                if str(path) == f"/proc/{proc.pid}/cmdline":
                    return b"python3\x00-u\x00-m\x00culture_core\x00server\x00start\x00"
                return real_read_bytes(path, *args, **kwargs)

            with patch.object(Path, "read_bytes", flagged_cmdline):
                assert classify_pidfile("server-spark") == "current"
        finally:
            proc.kill()
            proc.wait()

    @pytest.mark.parametrize(
        "cmdline",
        [
            b"/usr/bin/agentirc\x00start\x00--name\x00spark\x00",
            b"/usr/bin/agentirc-cli\x00start\x00--name\x00spark\x00",
            b"python3\x00-u\x00-m\x00agentirc\x00start\x00--name\x00spark\x00",
        ],
    )
    def test_agentirc_launcher_is_current(self, pid_dir, cmdline):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            (pid_dir / "server-spark.pid").write_text(str(proc.pid))
            real_read_bytes = Path.read_bytes

            def sibling_cmdline(path, *args, **kwargs):
                if str(path) == f"/proc/{proc.pid}/cmdline":
                    return cmdline
                return real_read_bytes(path, *args, **kwargs)

            with patch.object(Path, "read_bytes", sibling_cmdline):
                assert classify_pidfile("server-spark") == "current"
        finally:
            proc.kill()
            proc.wait()

    @pytest.mark.parametrize(
        "cmdline, expected",
        [
            (b"culture\x00server\x00status\x00--name\x00spark\x00", "stale"),
            (b"culture\x00server\x00start\x00--name\x00spark\x00", "current"),
            (b"culture\x00server\x00start\x00--name=thor\x00", "unknown"),
            (b"culture\x00server\x00restart\x00--name\x00spark\x00", "current"),
            (b"agentirc\x00start\x00--name=spark\x00", "current"),
            (b"agentirc\x00start\x00--name\x00thor\x00", "unknown"),
            (b"agentirc\x00restart\x00--name\x00spark\x00", "current"),
        ],
    )
    def test_legacy_server_launcher_requires_start_and_matching_name(
        self, pid_dir, cmdline, expected
    ):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            (pid_dir / "server-spark.pid").write_text(str(proc.pid))
            real_read_bytes = Path.read_bytes

            def launcher_cmdline(path, *args, **kwargs):
                if str(path) == f"/proc/{proc.pid}/cmdline":
                    return cmdline
                return real_read_bytes(path, *args, **kwargs)

            with patch.object(Path, "read_bytes", launcher_cmdline):
                assert classify_pidfile("server-spark") == expected
        finally:
            proc.kill()
            proc.wait()

    @pytest.mark.parametrize(
        "cmdline, expected",
        [
            (b"culture\x00agents\x00start\x00spark-ada\x00--foreground\x00", "current"),
            (b"culture\x00agents\x00start\x00spark-bob\x00", "unknown"),
            (b"culture\x00agents\x00start\x00spark-ada\x00spark-bob\x00", "unknown"),
            (b"culture\x00agents\x00start\x00--all\x00", "unknown"),
            (b"cultureagent\x00start\x00spark-ada\x00", "current"),
            (
                b"python3\x00-m\x00cultureagent.clients.claude\x00start\x00spark-ada\x00",
                "current",
            ),
        ],
    )
    def test_legacy_agent_launcher_matches_one_nick(self, pid_dir, cmdline, expected):
        proc = self._spawn(pid_dir, "agents", "start", "spark-ada")
        try:
            (pid_dir / "agent-spark-ada.pid").write_text(str(proc.pid))
            real_read_bytes = Path.read_bytes

            def launcher_cmdline(path, *args, **kwargs):
                if str(path) == f"/proc/{proc.pid}/cmdline":
                    return cmdline
                return real_read_bytes(path, *args, **kwargs)

            with patch.object(Path, "read_bytes", launcher_cmdline):
                assert classify_pidfile("agent-spark-ada") == expected
        finally:
            proc.kill()
            proc.wait()

    def test_orphan_identity_is_ignored_or_detects_reuse(self, pid_dir, monkeypatch):
        from cultureagent import pidfile as cultureagent_pidfile

        proc = self._spawn(pid_dir, "server", "start")
        monkeypatch.setattr(cultureagent_pidfile, "PID_DIR", str(pid_dir))
        try:
            write_pid("server-spark", proc.pid)
            identity_path = pid_dir / "server-spark.identity"
            _, start, boot = identity_path.read_text().split()
            cultureagent_pidfile.remove_pid("server-spark")
            assert identity_path.exists()

            (pid_dir / "server-spark.pid").write_text(str(proc.pid + 1))
            assert classify_pidfile("server-spark", proc.pid) == "changed"

            (pid_dir / "server-spark.pid").write_text(str(proc.pid))
            identity_path.write_text(f"{proc.pid} {int(start) + 1} {boot}\n")
            assert classify_pidfile("server-spark") == "stale"
        finally:
            proc.kill()
            proc.wait()

    def test_transient_proc_failure_is_unknown(self, pid_dir):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            write_pid("server-spark", proc.pid)
            real_read_bytes = Path.read_bytes

            def fail_stat(path, *args, **kwargs):
                if str(path) == f"/proc/{proc.pid}/stat":
                    raise OSError("transient /proc error")
                return real_read_bytes(path, *args, **kwargs)

            with patch.object(Path, "read_bytes", fail_stat):
                assert classify_pidfile("server-spark") == "unknown"
        finally:
            proc.kill()
            proc.wait()

    def test_missing_boot_id_uses_start_time(self, pid_dir):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            real_read_text = Path.read_text

            def missing_boot(path, *args, **kwargs):
                if str(path) == "/proc/sys/kernel/random/boot_id":
                    raise FileNotFoundError
                return real_read_text(path, *args, **kwargs)

            with patch.object(Path, "read_text", missing_boot):
                write_pid("server-spark", proc.pid)
                assert classify_pidfile("server-spark") == "current"
        finally:
            proc.kill()
            proc.wait()

    def test_no_proc_is_current(self, pid_dir):
        proc = self._spawn(pid_dir, "server", "start")
        try:
            (pid_dir / "server-spark.pid").write_text(f"{proc.pid}\n")
            with patch("culture_core.pidfile.PROC_DIR", str(pid_dir / "no-proc")):
                assert classify_pidfile("server-spark") == "current"
        finally:
            proc.kill()
            proc.wait()

    def test_stop_never_signals_real_stale_or_unknown_process(self, pid_dir):
        from culture_core.cli import server as server_cli

        proc = self._spawn(pid_dir, "server", "start", "--name", "sbx")
        try:
            write_pid("server-spark", proc.pid)
            path = pid_dir / "server-spark.pid"
            identity_path = pid_dir / "server-spark.identity"
            recorded_pid, start, boot = identity_path.read_text().split()
            identity_path.write_text(f"{recorded_pid} {int(start) + 1} {boot}\n")
            real_kill = os.kill
            signals = []

            def capture_kill(target, sig):
                if sig == 0:
                    return real_kill(target, sig)
                signals.append((target, sig))

            with patch.object(os, "kill", capture_kill):
                server_cli._server_stop(argparse.Namespace(name="spark"))
            assert signals == []
            assert is_process_alive(proc.pid)

            write_pid("server-spark", proc.pid)
            real_read_bytes = Path.read_bytes

            def fail_stat(proc_path, *args, **kwargs):
                if str(proc_path) == f"/proc/{proc.pid}/stat":
                    raise OSError("transient /proc error")
                return real_read_bytes(proc_path, *args, **kwargs)

            stop_args = argparse.Namespace(name="spark")
            with (
                patch.object(Path, "read_bytes", fail_stat),
                patch.object(os, "kill", capture_kill),
            ):
                with pytest.raises(server_cli.CultureError, match="Cannot verify"):
                    server_cli._check_already_running("server-spark", "spark")
                with pytest.raises(server_cli.CultureError, match="Cannot verify"):
                    server_cli._server_stop(stop_args)
            assert signals == []
            assert path.exists()
        finally:
            proc.kill()
            proc.wait()

    def test_stat_parser_uses_last_closing_parenthesis(self):
        real_read_bytes = Path.read_bytes

        def synthetic_stat(path, *args, **kwargs):
            if str(path) == "/proc/4242/stat":
                return b"4242 (a name ) with (parens) S " + b" ".join([b"0"] * 18 + [b"123456"])
            return real_read_bytes(path, *args, **kwargs)

        with patch.object(Path, "read_bytes", synthetic_stat):
            assert _process_identity(4242)[0] == "123456"

    def test_stat_parser_tolerates_non_utf8_comm(self):
        real_read_bytes = Path.read_bytes

        def synthetic_stat(path, *args, **kwargs):
            if str(path) == "/proc/4242/stat":
                return b"4242 (bad-\xff-name) S " + b" ".join([b"0"] * 18 + [b"654321"])
            return real_read_bytes(path, *args, **kwargs)

        with patch.object(Path, "read_bytes", synthetic_stat):
            assert _process_identity(4242)[0] == "654321"


@pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
class TestTerminateVerified:
    def test_process_exit_between_classification_and_termination_is_stopped(self, pid_dir):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        write_pid("server-spark", proc.pid)

        def exits_before_recheck(_name, _pid):
            proc.terminate()
            proc.wait(timeout=5)
            return "stale"

        with patch("culture_core.pidfile.classify_pidfile", exits_before_recheck):
            assert terminate_verified("server-spark", proc.pid) == "stopped"

    def test_kills_child_that_removes_pidfile_and_ignores_sigterm(self, pid_dir):
        pid_path = pid_dir / "server-spark.pid"
        ready_path = pid_dir / "ready"
        code = (
            "import os,signal,time\n"
            f"pid_path={str(pid_path)!r}\n"
            f"ready_path={str(ready_path)!r}\n"
            "def term(*_):\n"
            "    try: os.unlink(pid_path)\n"
            "    except FileNotFoundError: pass\n"
            "signal.signal(signal.SIGTERM, term)\n"
            "open(ready_path, 'w').close()\n"
            "time.sleep(30)\n"
        )
        proc = subprocess.Popen([sys.executable, "-c", code])
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not ready_path.exists():
                time.sleep(0.01)
            write_pid("server-spark", proc.pid)
            assert terminate_verified("server-spark", proc.pid, timeout=0.1) == "killed"
            assert not pid_path.exists()
            assert proc.wait(timeout=5) == -signal.SIGKILL
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_zombie_after_sigterm_is_stopped_without_sigkill(self, pid_dir):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        write_pid("server-spark", proc.pid)
        real_kill = os.kill
        signals = []

        def record_kill(pid, sig):
            if sig:
                signals.append(sig)
            return real_kill(pid, sig)

        try:
            with patch("culture_core.pidfile.os.kill", record_kill):
                assert terminate_verified("server-spark", proc.pid, timeout=1) == "stopped"
            assert signals == [signal.SIGTERM]
        finally:
            proc.wait(timeout=5)

    def test_aborts_sigkill_when_start_time_changes(self):
        signals = []

        with (
            patch("culture_core.pidfile.classify_pidfile", return_value="current"),
            patch("culture_core.pidfile._process_identity", return_value=("100", "boot")),
            patch("culture_core.pidfile.is_process_alive", return_value=True),
            patch(
                "culture_core.pidfile._read_stat_identity",
                return_value=("S", ("101", "boot")),
            ),
            patch("culture_core.pidfile.os.kill", lambda pid, sig: signals.append((pid, sig))),
        ):
            assert terminate_verified("server-spark", 4242, timeout=0) == "refused"
        assert signals == [(4242, signal.SIGTERM)]

    @pytest.mark.parametrize("fail_on", [signal.SIGTERM, signal.SIGKILL])
    def test_process_lookup_on_signal_is_stopped(self, fail_on):
        signals = []

        def signal_process(pid, sig):
            signals.append(sig)
            if sig == fail_on:
                raise ProcessLookupError

        with (
            patch("culture_core.pidfile.classify_pidfile", return_value="current"),
            patch("culture_core.pidfile._process_identity", return_value=("100", "boot")),
            patch("culture_core.pidfile._stopped_or_reused", return_value="live"),
            patch("culture_core.pidfile.os.kill", signal_process),
        ):
            assert terminate_verified("server-spark", 4242, timeout=0) == "stopped"
        expected = [signal.SIGTERM]
        if fail_on == signal.SIGKILL:
            expected.append(signal.SIGKILL)
        assert signals == expected

    def test_windows_escalation_repeats_sigterm(self):
        signals = []
        with (
            patch("culture_core.pidfile.classify_pidfile", return_value="current"),
            patch("culture_core.pidfile.os.path.isdir", return_value=False),
            patch("culture_core.pidfile._stopped_or_reused", return_value="live"),
            patch("culture_core.pidfile.sys.platform", "win32"),
            patch("culture_core.pidfile.os.kill", lambda pid, sig: signals.append(sig)),
        ):
            assert terminate_verified("server-spark", 4242, timeout=0) == "killed"
        assert signals == [signal.SIGTERM, signal.SIGTERM]


class TestDefaultServer:
    def test_write_and_read(self, pid_dir):
        write_default_server("spark")
        assert read_default_server() == "spark"

    def test_read_missing_returns_none(self, pid_dir):
        assert read_default_server() is None

    def test_read_empty_returns_none(self, pid_dir):
        (pid_dir / "default_server").write_text("   ")
        assert read_default_server() is None

    def test_read_oserror_returns_none(self, pid_dir):
        (pid_dir / "default_server").write_text("spark")
        with patch.object(Path, "read_text", side_effect=OSError("denied")):
            assert read_default_server() is None


class TestRenamePid:
    def test_rename_both_files(self, pid_dir):
        write_pid("server-old", 1234)
        write_port("server-old", 6667)
        assert rename_pid("server-old", "server-new") is True
        assert read_pid("server-new") == 1234
        assert read_port("server-new") == 6667

    @pytest.mark.skipif(not os.path.isdir("/proc"), reason="needs /proc")
    def test_rename_preserves_recorded_identity(self, pid_dir):
        proc = TestClassification._spawn(pid_dir, "server", "start")
        try:
            write_pid("server-old", proc.pid)
            before = (pid_dir / "server-old.identity").read_text()
            assert rename_pid("server-old", "server-new")
            assert (pid_dir / "server-new.identity").read_text() == before
            assert not (pid_dir / "server-old.identity").exists()
            assert classify_pidfile("server-new") == "current"
        finally:
            proc.kill()
            proc.wait()

    def test_rename_pid_only(self, pid_dir):
        write_pid("server-noport", 1234)
        assert rename_pid("server-noport", "server-renamed") is True
        assert read_pid("server-renamed") == 1234

    def test_rename_missing_returns_false(self, pid_dir):
        assert rename_pid("ghost", "ghost-renamed") is False

    def test_rename_oserror_swallowed(self, pid_dir):
        write_pid("server-fail", 1234)
        with patch.object(Path, "rename", side_effect=OSError("denied")):
            # Both renames fail; function returns False but doesn't raise.
            assert rename_pid("server-fail", "server-target") is False


class TestListServers:
    def test_empty_dir_returns_empty(self, pid_dir):
        # pid_dir exists but is empty — shouldn't fail.
        assert list_servers() == []

    def test_missing_dir_returns_empty(self, tmp_path):
        with patch("culture_core.pidfile.PID_DIR", str(tmp_path / "nope")):
            assert list_servers() == []

    def test_lists_running_culture_servers(self, pid_dir):
        write_pid("server-alpha", 1234)
        write_port("server-alpha", 6667)
        with patch("culture_core.pidfile.classify_pidfile", return_value="current"):
            servers = list_servers()
        assert servers == [{"name": "alpha", "pid": 1234, "port": 6667}]

    def test_skips_dead_pids(self, pid_dir):
        write_pid("server-zombie", 9999)
        write_port("server-zombie", 6667)
        with patch("culture_core.pidfile.classify_pidfile", return_value="stale"):
            assert list_servers() == []

    def test_skips_non_culture_processes(self, pid_dir):
        write_pid("server-rogue", 1234)
        write_port("server-rogue", 6667)
        with patch("culture_core.pidfile.classify_pidfile", return_value="stale"):
            assert list_servers() == []

    def test_defaults_port_when_missing(self, pid_dir):
        write_pid("server-noport", 1234)
        with patch("culture_core.pidfile.classify_pidfile", return_value="current"):
            servers = list_servers()
        assert servers == [{"name": "noport", "pid": 1234, "port": 6667}]

    def test_skips_entries_with_unreadable_pid(self, pid_dir):
        (pid_dir / "server-corrupt.pid").write_text("not-a-pid")
        assert list_servers() == []
