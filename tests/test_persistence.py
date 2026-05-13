# tests/test_persistence.py
"""Tests for platform-specific auto-start service generation."""

import subprocess
import sys
from unittest.mock import patch

import pytest

from culture.persistence import (
    _build_launchd_plist,
    _build_systemd_unit,
    _build_windows_bat,
    _run_cmd,
    get_platform,
    install_service,
    list_services,
    restart_service,
    uninstall_service,
)


def test_get_platform_linux():
    with patch.object(sys, "platform", "linux"):
        assert get_platform() == "linux"


def test_get_platform_macos():
    with patch.object(sys, "platform", "darwin"):
        assert get_platform() == "macos"


def test_get_platform_windows():
    with patch.object(sys, "platform", "win32"):
        assert get_platform() == "windows"


def test_build_systemd_unit():
    unit = _build_systemd_unit(
        name="culture-server-spark",
        command=["culture", "server", "start", "--foreground", "--name", "spark"],
        description="culture server spark",
    )
    assert "[Unit]" in unit
    assert "Description=culture server spark" in unit
    assert "ExecStart=culture server start --foreground --name spark" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_build_launchd_plist():
    plist = _build_launchd_plist(
        name="com.culture.server-spark",
        command=["culture", "server", "start", "--foreground", "--name", "spark"],
        description="culture server spark",
    )
    assert "<key>Label</key>" in plist
    assert "com.culture.server-spark" in plist
    assert "<string>culture</string>" in plist
    assert "<key>KeepAlive</key>" in plist
    assert "<true/>" in plist


def test_build_windows_bat():
    bat = _build_windows_bat(
        command=["culture", "server", "start", "--foreground", "--name", "spark"],
    )
    assert ":loop" in bat
    assert "culture server start --foreground --name spark" in bat
    assert "if %ERRORLEVEL% EQU 0 goto end" in bat
    assert "timeout /t 5" in bat
    assert "goto loop" in bat
    assert ":end" in bat


def test_install_service_linux(tmp_path):
    """Install writes a systemd unit file and returns its path."""
    unit_dir = tmp_path / "systemd" / "user"
    with (
        patch("culture.persistence.get_platform", return_value="linux"),
        patch("culture.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture.persistence._run_cmd"),
    ):
        path = install_service(
            "culture-server-spark",
            ["culture", "server", "start", "--foreground", "--name", "spark"],
            "culture server spark",
        )
    assert path.exists()
    assert path.name == "culture-server-spark.service"
    content = path.read_text()
    assert "ExecStart=" in content


def test_list_services_linux(tmp_path):
    """list_services returns installed service names."""
    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / "culture-server-spark.service").write_text("[Unit]\n")
    (unit_dir / "culture-agent-spark-claude.service").write_text("[Unit]\n")
    (unit_dir / "unrelated.service").write_text("[Unit]\n")

    with (
        patch("culture.persistence.get_platform", return_value="linux"),
        patch("culture.persistence._systemd_user_dir", return_value=unit_dir),
    ):
        services = list_services()

    assert "culture-server-spark" in services
    assert "culture-agent-spark-claude" in services
    assert "unrelated" not in services


# ---------------------------------------------------------------------------
# Phase 4a — coverage extensions
# ---------------------------------------------------------------------------


class TestGetPlatformFallback:
    def test_freebsd_falls_back_to_linux(self):
        """Anything that isn't darwin/win32 maps to 'linux' branch."""
        with patch.object(sys, "platform", "freebsd14"):
            assert get_platform() == "linux"


class TestRunCmd:
    def test_success_returns_true(self):
        completed = subprocess.CompletedProcess(args=["true"], returncode=0)
        with patch("culture.persistence.subprocess.run", return_value=completed):
            assert _run_cmd(["systemctl", "--user", "status"]) is True

    def test_nonzero_exit_still_returns_true(self):
        """_run_cmd doesn't fail on non-zero exit — that's the caller's job."""
        completed = subprocess.CompletedProcess(args=["x"], returncode=5)
        with patch("culture.persistence.subprocess.run", return_value=completed):
            assert _run_cmd(["x"]) is True

    def test_timeout_returns_false(self):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=a[0] if a else "x", timeout=kw.get("timeout", 30))

        with patch("culture.persistence.subprocess.run", side_effect=_raise):
            assert _run_cmd(["systemctl", "--user", "status"]) is False


# ---------------------------------------------------------------------------
# install_service — per-platform
# ---------------------------------------------------------------------------


class TestInstallService:
    def test_macos_writes_plist_and_loads(self, tmp_path):
        agent_dir = tmp_path / "LaunchAgents"
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=agent_dir),
            patch("culture.persistence._run_cmd") as mock_run,
        ):
            path = install_service("server-spark", ["culture", "server", "start"], "culture spark")
        assert path.exists()
        assert path.name == "com.culture.server-spark.plist"
        # launchctl load was called
        assert mock_run.call_args.args[0][:2] == ["launchctl", "load"]

    def test_windows_writes_bat_and_schtasks(self, tmp_path):
        svc_dir = tmp_path / "services"
        with (
            patch("culture.persistence.get_platform", return_value="windows"),
            patch("culture.persistence._windows_service_dir", return_value=svc_dir),
            patch("culture.persistence._run_cmd") as mock_run,
        ):
            path = install_service("server-spark", ["culture", "server", "start"], "culture spark")
        assert path.exists()
        assert path.name == "server-spark.bat"
        # schtasks /Create invoked
        assert mock_run.call_args.args[0][:2] == ["schtasks", "/Create"]

    def test_unsupported_platform_raises(self):
        with patch("culture.persistence.get_platform", return_value="aix"):
            with pytest.raises(RuntimeError, match="Unsupported platform"):
                install_service("x", ["echo"], "desc")


# ---------------------------------------------------------------------------
# uninstall_service — per-platform + dispatcher
# ---------------------------------------------------------------------------


class TestUninstallService:
    def test_linux_disables_stops_and_removes(self, tmp_path):
        unit_dir = tmp_path / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        unit = unit_dir / "culture-server-spark.service"
        unit.write_text("[Unit]\n")
        with (
            patch("culture.persistence.get_platform", return_value="linux"),
            patch("culture.persistence._systemd_user_dir", return_value=unit_dir),
            patch("culture.persistence._run_cmd") as mock_run,
        ):
            uninstall_service("culture-server-spark")
        assert not unit.exists()
        # disable + stop + daemon-reload all fired
        verbs = [call.args[0][1:3] for call in mock_run.call_args_list]
        assert ["--user", "disable"] in verbs
        assert ["--user", "stop"] in verbs

    def test_linux_when_unit_already_gone(self, tmp_path):
        unit_dir = tmp_path / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        with (
            patch("culture.persistence.get_platform", return_value="linux"),
            patch("culture.persistence._systemd_user_dir", return_value=unit_dir),
            patch("culture.persistence._run_cmd"),
        ):
            uninstall_service("missing")  # no raise

    def test_macos_unloads_then_removes(self, tmp_path):
        agent_dir = tmp_path / "LaunchAgents"
        agent_dir.mkdir(parents=True)
        plist = agent_dir / "com.culture.server-spark.plist"
        plist.write_text("<plist/>\n")
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=agent_dir),
            patch("culture.persistence._run_cmd") as mock_run,
        ):
            uninstall_service("server-spark")
        assert not plist.exists()
        # launchctl unload was called
        assert mock_run.call_args.args[0][:2] == ["launchctl", "unload"]

    def test_macos_when_plist_absent_does_nothing(self, tmp_path):
        agent_dir = tmp_path / "LaunchAgents"
        agent_dir.mkdir(parents=True)
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=agent_dir),
            patch("culture.persistence._run_cmd") as mock_run,
        ):
            uninstall_service("ghost")
        # No run_cmd at all when plist is missing
        mock_run.assert_not_called()

    def test_windows_schtasks_delete_and_remove_bat(self, tmp_path):
        svc_dir = tmp_path / "services"
        svc_dir.mkdir(parents=True)
        bat = svc_dir / "server-spark.bat"
        bat.write_text("@echo off\n")
        with (
            patch("culture.persistence.get_platform", return_value="windows"),
            patch("culture.persistence._windows_service_dir", return_value=svc_dir),
            patch("culture.persistence._run_cmd") as mock_run,
        ):
            uninstall_service("server-spark")
        assert not bat.exists()
        assert mock_run.call_args.args[0][:2] == ["schtasks", "/Delete"]

    def test_unsupported_platform_is_noop(self):
        with (
            patch("culture.persistence.get_platform", return_value="aix"),
            patch("culture.persistence._run_cmd") as mock_run,
        ):
            uninstall_service("x")  # no raise
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# list_services — macOS + Windows + unsupported
# ---------------------------------------------------------------------------


class TestListServices:
    def test_macos_filters_to_com_culture_prefix(self, tmp_path):
        agent_dir = tmp_path / "LaunchAgents"
        agent_dir.mkdir()
        (agent_dir / "com.culture.server-spark.plist").write_text("<plist/>\n")
        (agent_dir / "com.culture.agent-spark-ada.plist").write_text("<plist/>\n")
        (agent_dir / "com.other.service.plist").write_text("<plist/>\n")
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=agent_dir),
        ):
            services = list_services()
        assert sorted(services) == ["agent-spark-ada", "server-spark"]

    def test_windows_filters_to_culture_prefix(self, tmp_path):
        svc_dir = tmp_path / "services"
        svc_dir.mkdir()
        (svc_dir / "culture-server-spark.bat").write_text("@echo off\n")
        (svc_dir / "unrelated.bat").write_text("@echo off\n")
        with (
            patch("culture.persistence.get_platform", return_value="windows"),
            patch("culture.persistence._windows_service_dir", return_value=svc_dir),
        ):
            services = list_services()
        assert services == ["culture-server-spark"]

    def test_macos_returns_empty_when_no_agent_dir(self, tmp_path):
        ghost = tmp_path / "no-such-dir"
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=ghost),
        ):
            assert list_services() == []

    def test_unsupported_platform_returns_empty(self):
        with patch("culture.persistence.get_platform", return_value="aix"):
            assert list_services() == []


# ---------------------------------------------------------------------------
# restart_service — per-platform + dispatcher
# ---------------------------------------------------------------------------


class TestRestartService:
    def test_linux_missing_unit_returns_false(self, tmp_path):
        unit_dir = tmp_path / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        with (
            patch("culture.persistence.get_platform", return_value="linux"),
            patch("culture.persistence._systemd_user_dir", return_value=unit_dir),
            patch("culture.persistence._run_cmd"),
        ):
            assert restart_service("ghost") is False

    def test_linux_restart_invokes_systemctl(self, tmp_path):
        unit_dir = tmp_path / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "culture-server-spark.service").write_text("[Unit]\n")
        with (
            patch("culture.persistence.get_platform", return_value="linux"),
            patch("culture.persistence._systemd_user_dir", return_value=unit_dir),
            patch("culture.persistence._run_cmd", return_value=True) as mock_run,
        ):
            assert restart_service("culture-server-spark") is True
        assert mock_run.call_args.args[0][:3] == ["systemctl", "--user", "restart"]

    def test_macos_missing_plist_returns_false(self, tmp_path):
        agent_dir = tmp_path / "LaunchAgents"
        agent_dir.mkdir()
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=agent_dir),
        ):
            assert restart_service("ghost") is False

    def test_macos_unload_failure_returns_false(self, tmp_path):
        agent_dir = tmp_path / "LaunchAgents"
        agent_dir.mkdir()
        (agent_dir / "com.culture.server-spark.plist").write_text("<plist/>\n")
        # First call (unload) returns False
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=agent_dir),
            patch("culture.persistence._run_cmd", return_value=False),
        ):
            assert restart_service("server-spark") is False

    def test_macos_load_after_unload_returns_true(self, tmp_path):
        agent_dir = tmp_path / "LaunchAgents"
        agent_dir.mkdir()
        (agent_dir / "com.culture.server-spark.plist").write_text("<plist/>\n")
        with (
            patch("culture.persistence.get_platform", return_value="macos"),
            patch("culture.persistence._launchd_dir", return_value=agent_dir),
            patch("culture.persistence._run_cmd", return_value=True),
        ):
            assert restart_service("server-spark") is True

    def test_windows_query_timeout_returns_false(self):
        def _raise(*a, **kw):
            raise subprocess.TimeoutExpired(cmd=a[0], timeout=kw.get("timeout", 30))

        with (
            patch("culture.persistence.get_platform", return_value="windows"),
            patch("culture.persistence.subprocess.run", side_effect=_raise),
        ):
            assert restart_service("server-spark") is False

    def test_windows_query_missing_returns_false(self):
        completed = subprocess.CompletedProcess(args=["schtasks"], returncode=1)
        with (
            patch("culture.persistence.get_platform", return_value="windows"),
            patch("culture.persistence.subprocess.run", return_value=completed),
        ):
            assert restart_service("ghost") is False

    def test_windows_run_after_query_returns_true(self):
        completed = subprocess.CompletedProcess(args=["schtasks"], returncode=0)
        with (
            patch("culture.persistence.get_platform", return_value="windows"),
            patch("culture.persistence.subprocess.run", return_value=completed),
            patch("culture.persistence._run_cmd", return_value=True) as mock_run,
        ):
            assert restart_service("server-spark") is True
        assert mock_run.call_args.args[0][:2] == ["schtasks", "/Run"]

    def test_unsupported_platform_returns_false(self):
        with patch("culture.persistence.get_platform", return_value="aix"):
            assert restart_service("x") is False
