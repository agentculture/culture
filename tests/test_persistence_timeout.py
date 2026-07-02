"""Regression tests: subprocess timeout handling in persistence._run_cmd.

Covers the hang reported for ``culture mesh update``: a broken/hung systemd
unit caused ``systemctl --user restart`` to block indefinitely because the
underlying subprocess call had no timeout.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from culture_core.persistence import (
    DEFAULT_CMD_TIMEOUT,
    _restart_linux_service,
    _run_cmd,
)


def test_run_cmd_returns_true_on_success():
    with patch("culture_core.persistence.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=["/bin/true"], returncode=0)
        assert _run_cmd(["/bin/true"]) is True


def test_run_cmd_returns_false_on_timeout():
    """_run_cmd must not propagate TimeoutExpired — callers treat it as failure."""
    with patch("culture_core.persistence.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["systemctl"], timeout=30)
        assert _run_cmd(["systemctl", "restart", "x"]) is False


def test_run_cmd_passes_timeout_to_subprocess():
    with patch("culture_core.persistence.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        _run_cmd(["echo", "hi"], timeout=5)
        kwargs = mock_run.call_args.kwargs
        assert kwargs["timeout"] == 5


def test_run_cmd_default_timeout_bounds_wait():
    """A default timeout must be applied so hung services never hang the CLI."""
    with patch("culture_core.persistence.subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0)
        _run_cmd(["systemctl", "restart", "x"])
        assert mock_run.call_args.kwargs["timeout"] == DEFAULT_CMD_TIMEOUT


def test_restart_linux_service_returns_false_on_timeout(tmp_path, monkeypatch):
    """When the restart command times out, the restarter must report failure."""
    svc_name = "culture-test-timeout"
    unit_dir = tmp_path / "systemd-user"
    unit_dir.mkdir()
    (unit_dir / f"{svc_name}.service").write_text("[Service]\nExecStart=/bin/true\n")

    monkeypatch.setattr("culture_core.persistence._systemd_user_dir", lambda: unit_dir)
    with patch("culture_core.persistence.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd=["systemctl"], timeout=30)
        assert _restart_linux_service(svc_name) is False
