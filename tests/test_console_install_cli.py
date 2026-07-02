# tests/test_console_install_cli.py
"""Tests for `culture console install/uninstall` (durable-mesh provisioning).

`culture console` is an irc-lens passthrough — these verbs are
culture-side, intercepted in dispatch() before anything reaches
irc-lens (which knows nothing about service units). The generated
`culture-console-<name>` unit runs `<python> -m culture_core console
serve [--config <path>]` and is ordered After=/Wants= the server unit.
"""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from culture_core.cli import console


def _server_yaml(tmp_path, name="spark"):
    """Write a minimal ~/.culture/server.yaml stand-in and return its path."""
    from culture_core.config import (
        ServerConfig,
        ServerConnConfig,
        save_server_config,
    )

    path = tmp_path / "server.yaml"
    save_server_config(str(path), ServerConfig(server=ServerConnConfig(name=name)))
    return path


def _args(argv):
    return argparse.Namespace(console_args=argv)


class TestConsoleInstall:
    def test_install_intercepted_before_passthrough(self, tmp_path):
        """`install` must never reach irc-lens — it's a culture-side verb."""
        server_yaml = _server_yaml(tmp_path)
        with (
            patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
            patch("culture_core.persistence.install_service") as mock_install,
            patch.object(console, "_invoke_irc_lens") as mock_lens,
            pytest.raises(SystemExit) as excinfo,
        ):
            mock_install.return_value = Path("/tmp/fake.service")
            console.dispatch(_args(["install"]))

        assert excinfo.value.code == 0
        mock_lens.assert_not_called()
        mock_install.assert_called_once()

    def test_install_unit_name_command_and_ordering(self, tmp_path):
        """Unit is culture-console-<name>, runs `console serve --config <path>`,
        and orders After=/Wants= the server unit."""
        server_yaml = _server_yaml(tmp_path, name="spark")
        with (
            patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
            patch("culture_core.persistence.install_service") as mock_install,
            pytest.raises(SystemExit) as excinfo,
        ):
            mock_install.return_value = Path("/tmp/fake.service")
            console.dispatch(_args(["install", "--config", "/etc/irc-lens/lens.yaml"]))

        assert excinfo.value.code == 0
        args_, kwargs = mock_install.call_args
        svc_name, command, description = args_[0], args_[1], args_[2]
        assert svc_name == "culture-console-spark"
        assert command == [
            sys.executable,
            "-m",
            "culture_core",
            "console",
            "serve",
            "--config",
            "/etc/irc-lens/lens.yaml",
        ]
        assert description == "culture-core console spark"
        assert kwargs.get("after") == "culture-server-spark.service"

    def test_install_accepts_equals_form_config(self, tmp_path):
        server_yaml = _server_yaml(tmp_path, name="spark")
        with (
            patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
            patch("culture_core.persistence.install_service") as mock_install,
            pytest.raises(SystemExit),
        ):
            mock_install.return_value = Path("/tmp/fake.service")
            console.dispatch(_args(["install", "--config=/x/lens.yaml"]))

        command = mock_install.call_args[0][1]
        assert command[-2:] == ["--config", "/x/lens.yaml"]

    def test_install_without_config_omits_flag(self, tmp_path):
        """No --config -> ExecStart defers to irc-lens's own default config
        path (mirrors the agents-install rule of not pinning config paths)."""
        server_yaml = _server_yaml(tmp_path, name="spark")
        with (
            patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
            patch("culture_core.persistence.install_service") as mock_install,
            pytest.raises(SystemExit),
        ):
            mock_install.return_value = Path("/tmp/fake.service")
            console.dispatch(_args(["install"]))

        command = mock_install.call_args[0][1]
        assert command == [sys.executable, "-m", "culture_core", "console", "serve"]
        assert "--config" not in command

    def test_install_twice_is_idempotent(self, tmp_path):
        """Install twice = identical unit content, re-enabled, exit 0 both
        times. Targets a tmp XDG path — never the real ~/.config/systemd."""
        server_yaml = _server_yaml(tmp_path, name="spark")
        unit_dir = tmp_path / "systemd" / "user"

        for _ in range(2):
            with (
                patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
                patch("culture_core.persistence.get_platform", return_value="linux"),
                patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
                patch("culture_core.persistence._run_cmd"),
                pytest.raises(SystemExit) as excinfo,
            ):
                console.dispatch(_args(["install"]))
            assert excinfo.value.code == 0

        units = list(unit_dir.glob("*.service"))
        assert [u.name for u in units] == ["culture-console-spark.service"]
        content = units[0].read_text()
        assert "After=culture-server-spark.service" in content
        assert "Wants=culture-server-spark.service" in content


class TestConsoleUninstall:
    def test_uninstall_removes_unit(self, tmp_path, capsys):
        server_yaml = _server_yaml(tmp_path, name="spark")
        with (
            patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
            patch("culture_core.persistence.uninstall_service", return_value=True) as mock_un,
            pytest.raises(SystemExit) as excinfo,
        ):
            console.dispatch(_args(["uninstall"]))

        assert excinfo.value.code == 0
        mock_un.assert_called_once_with("culture-console-spark")
        assert "Uninstalled culture-console-spark" in capsys.readouterr().out

    def test_uninstall_when_absent_is_friendly_noop(self, tmp_path, capsys):
        server_yaml = _server_yaml(tmp_path, name="spark")
        with (
            patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
            patch("culture_core.persistence.uninstall_service", return_value=False),
            pytest.raises(SystemExit) as excinfo,
        ):
            console.dispatch(_args(["uninstall"]))

        assert excinfo.value.code == 0
        out = capsys.readouterr().out
        assert "culture-console-spark" in out
        assert "not installed" in out

    def test_uninstall_intercepted_before_passthrough(self, tmp_path):
        server_yaml = _server_yaml(tmp_path)
        with (
            patch.object(console, "DEFAULT_CONFIG", str(server_yaml)),
            patch("culture_core.persistence.uninstall_service", return_value=True),
            patch.object(console, "_invoke_irc_lens") as mock_lens,
            pytest.raises(SystemExit),
        ):
            console.dispatch(_args(["uninstall"]))
        mock_lens.assert_not_called()
