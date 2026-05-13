"""Tests for culture.pidfile — PID file management and process validation."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from culture.pidfile import (
    is_culture_process,
    is_process_alive,
    list_servers,
    read_default_server,
    read_pid,
    read_port,
    remove_pid,
    remove_port,
    rename_pid,
    write_default_server,
    write_pid,
    write_port,
)


@pytest.fixture()
def pid_dir(tmp_path):
    """Use a temporary directory for PID files."""
    with patch("culture.pidfile.PID_DIR", str(tmp_path)):
        yield tmp_path


class TestWriteReadRemove:
    def test_write_and_read(self, pid_dir):
        write_pid("agent-bot", 12345)
        assert read_pid("agent-bot") == 12345

    def test_read_missing(self, pid_dir):
        assert read_pid("nonexistent") is None

    def test_remove(self, pid_dir):
        write_pid("agent-bot", 12345)
        remove_pid("agent-bot")
        assert read_pid("agent-bot") is None

    def test_remove_missing_is_noop(self, pid_dir):
        remove_pid("nonexistent")  # should not raise


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
            patch("culture.pidfile.Path") as mock_path,
            patch("culture.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is True

    def test_cmdline_with_culture_basename(self):
        """argv[0] with basename 'culture' is recognized."""
        raw = b"/home/user/.local/bin/culture\x00server\x00start\x00"
        with (
            patch("culture.pidfile.Path") as mock_path,
            patch("culture.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is True

    def test_cmdline_without_culture(self):
        """Process with unrelated cmdline is rejected."""
        raw = b"/sbin/init\x00--system\x00"
        with (
            patch("culture.pidfile.Path") as mock_path,
            patch("culture.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is False

    def test_cmdline_substring_not_matched(self):
        """Substring 'culture' inside another token is NOT matched."""
        raw = b"/usr/bin/agriculture-daemon\x00--flag\x00"
        with (
            patch("culture.pidfile.Path") as mock_path,
            patch("culture.pidfile.os.path.isdir", return_value=True),
        ):
            mock_path.return_value.read_bytes.return_value = raw
            assert is_culture_process(999) is False

    def test_no_proc_returns_true(self):
        """When /proc doesn't exist (macOS/Windows), assume valid."""
        with patch("culture.pidfile.os.path.isdir", return_value=False):
            assert is_culture_process(999) is True

    def test_oserror_on_linux_returns_false(self):
        """On Linux, read failures fail closed (return False)."""
        with (
            patch("culture.pidfile.Path") as mock_path,
            patch("culture.pidfile.os.path.isdir", return_value=True),
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


class TestIsProcessAlive:
    def test_permission_error_returns_true(self):
        """If the OS says we can't signal the PID, the process still exists."""
        with patch("culture.pidfile.os.kill", side_effect=PermissionError("denied")):
            assert is_process_alive(1) is True


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
        with patch("culture.pidfile.PID_DIR", str(tmp_path / "nope")):
            assert list_servers() == []

    def test_lists_running_culture_servers(self, pid_dir):
        write_pid("server-alpha", 1234)
        write_port("server-alpha", 6667)
        with (
            patch("culture.pidfile.is_process_alive", return_value=True),
            patch("culture.pidfile.is_culture_process", return_value=True),
        ):
            servers = list_servers()
        assert servers == [{"name": "alpha", "pid": 1234, "port": 6667}]

    def test_skips_dead_pids(self, pid_dir):
        write_pid("server-zombie", 9999)
        write_port("server-zombie", 6667)
        with patch("culture.pidfile.is_process_alive", return_value=False):
            assert list_servers() == []

    def test_skips_non_culture_processes(self, pid_dir):
        write_pid("server-rogue", 1234)
        write_port("server-rogue", 6667)
        with (
            patch("culture.pidfile.is_process_alive", return_value=True),
            patch("culture.pidfile.is_culture_process", return_value=False),
        ):
            assert list_servers() == []

    def test_defaults_port_when_missing(self, pid_dir):
        write_pid("server-noport", 1234)
        with (
            patch("culture.pidfile.is_process_alive", return_value=True),
            patch("culture.pidfile.is_culture_process", return_value=True),
        ):
            servers = list_servers()
        assert servers == [{"name": "noport", "pid": 1234, "port": 6667}]

    def test_skips_entries_with_unreadable_pid(self, pid_dir):
        (pid_dir / "server-corrupt.pid").write_text("not-a-pid")
        assert list_servers() == []
