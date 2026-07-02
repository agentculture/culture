# tests/test_server_install_cli.py
"""Tests for `culture server install/uninstall` (durable-mesh provisioning).

Symmetric with `culture agents install` (tests/test_agent_install_cli.py):
the verbs generate + enable a `culture-server-<name>` auto-start unit whose
ExecStart reuses the same config resolution `culture mesh setup` uses —
mesh.yaml, falling back to generating it from ~/.culture/server.yaml.
"""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_mesh(tmp_path, name="spark", host="0.0.0.0", port=6667):
    """Write a minimal mesh.yaml and return its path."""
    from culture_core.mesh_config import (
        MeshConfig,
        MeshServerConfig,
        save_mesh_config,
    )

    mesh_yaml = tmp_path / "mesh.yaml"
    mesh = MeshConfig(server=MeshServerConfig(name=name, host=host, port=port))
    save_mesh_config(mesh, mesh_yaml)
    return mesh_yaml


def test_install_passes_correct_argv_to_install_service(tmp_path):
    """The unit's ExecStart reuses build_server_start_cmd — same argv as
    the units `culture mesh setup` generates (host/port/mesh-config from
    the resolved mesh config, --foreground for the service manager)."""
    from culture_core.cli.server import _server_install

    mesh_yaml = _write_mesh(tmp_path, name="spark", host="0.0.0.0", port=6667)
    args = argparse.Namespace(config=str(mesh_yaml))

    with patch("culture_core.persistence.install_service") as mock_install:
        mock_install.return_value = Path("/tmp/fake.service")
        _server_install(args)

    assert mock_install.call_count == 1
    args_, _kwargs = mock_install.call_args
    svc_name, command, description = args_[0], args_[1], args_[2]

    assert svc_name == "culture-server-spark"
    assert command == [
        sys.executable,
        "-m",
        "culture_core",
        "server",
        "start",
        "--foreground",
        "--name",
        "spark",
        "--host",
        "0.0.0.0",
        "--port",
        "6667",
        "--mesh-config",
        str(mesh_yaml),
    ]
    assert description == "culture-core server spark"


def test_install_generates_mesh_from_server_manifest_when_missing(tmp_path):
    """No mesh.yaml -> fall back to generating one from ~/.culture/server.yaml,
    exactly like `culture mesh setup` (reuse, not a new resolution)."""
    from culture_core.cli.server import _server_install
    from culture_core.config import (
        ServerConfig,
        ServerConnConfig,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    save_server_config(str(server_yaml), ServerConfig(server=ServerConnConfig(name="thor")))
    mesh_yaml = tmp_path / "mesh.yaml"  # does not exist

    args = argparse.Namespace(config=str(mesh_yaml))
    with (
        patch("culture_core.cli.shared.mesh.DEFAULT_CONFIG", str(server_yaml)),
        patch("culture_core.persistence.install_service") as mock_install,
    ):
        mock_install.return_value = Path("/tmp/fake.service")
        _server_install(args)

    svc_name = mock_install.call_args[0][0]
    assert svc_name == "culture-server-thor"
    # The fallback saves the generated mesh.yaml (same side effect as setup).
    assert mesh_yaml.exists()


def test_install_errors_when_no_config_anywhere(tmp_path):
    """Neither mesh.yaml nor server.yaml -> CultureError exit 1 with remediation."""
    from culture_core.cli._errors import CultureError
    from culture_core.cli.server import _server_install

    args = argparse.Namespace(config=str(tmp_path / "mesh.yaml"))
    with (
        patch("culture_core.cli.shared.mesh.DEFAULT_CONFIG", str(tmp_path / "server.yaml")),
        patch("culture_core.persistence.install_service") as mock_install,
        pytest.raises(CultureError) as exc,
    ):
        _server_install(args)

    assert exc.value.code == 1
    mock_install.assert_not_called()


def test_install_twice_is_idempotent(tmp_path):
    """Running install twice rewrites identical unit content and re-enables —
    exit 0 both times, no duplicate units. Targets a tmp XDG path; never the
    real ~/.config/systemd of this machine."""
    from culture_core.cli.server import _server_install

    mesh_yaml = _write_mesh(tmp_path, name="spark")
    unit_dir = tmp_path / "systemd" / "user"
    args = argparse.Namespace(config=str(mesh_yaml))

    with (
        patch("culture_core.persistence.get_platform", return_value="linux"),
        patch("culture_core.persistence._systemd_user_dir", return_value=unit_dir),
        patch("culture_core.persistence._run_cmd") as mock_run,
    ):
        _server_install(args)
        first = (unit_dir / "culture-server-spark.service").read_text()
        _server_install(args)
        second = (unit_dir / "culture-server-spark.service").read_text()

    assert first == second
    assert len(list(unit_dir.glob("*.service"))) == 1
    # enable fired on both runs (re-enable is part of the idempotent contract)
    enables = [c for c in mock_run.call_args_list if "enable" in c.args[0]]
    assert len(enables) == 2


def test_uninstall_removes_unit(tmp_path, capsys):
    """`culture server uninstall` removes the unit resolved from the config."""
    from culture_core.cli.server import _server_uninstall

    mesh_yaml = _write_mesh(tmp_path, name="spark")
    args = argparse.Namespace(config=str(mesh_yaml))

    with patch("culture_core.persistence.uninstall_service", return_value=True) as mock_un:
        _server_uninstall(args)

    mock_un.assert_called_once_with("culture-server-spark")
    assert "Uninstalled culture-server-spark" in capsys.readouterr().out


def test_uninstall_when_absent_is_friendly_noop(tmp_path, capsys):
    """Uninstalling a unit that isn't installed is a friendly no-op, exit 0."""
    from culture_core.cli.server import _server_uninstall

    mesh_yaml = _write_mesh(tmp_path, name="spark")
    args = argparse.Namespace(config=str(mesh_yaml))

    with patch("culture_core.persistence.uninstall_service", return_value=False):
        _server_uninstall(args)  # must not raise

    out = capsys.readouterr().out
    assert "culture-server-spark" in out
    assert "not installed" in out


def test_install_uninstall_parsers_registered():
    """Both verbs appear on the argparse surface with a --config flag."""
    import os

    from culture_core.cli import _build_parser

    p = _build_parser()
    args = p.parse_args(["server", "install"])
    assert args.command == "server"
    assert args.server_command == "install"
    assert args.config == os.path.expanduser("~/.culture/mesh.yaml")

    args = p.parse_args(["server", "install", "--config", "/tmp/mesh.yaml"])
    assert args.config == "/tmp/mesh.yaml"

    args = p.parse_args(["server", "uninstall"])
    assert args.server_command == "uninstall"


def test_install_uninstall_in_dispatch():
    """Handlers are wired into the dispatch table."""
    from culture_core.cli import server

    args = argparse.Namespace(server_command="install", config="/nonexistent/mesh.yaml")
    with patch.object(server, "_server_install") as mock_handler:
        server.dispatch(args)
    mock_handler.assert_called_once_with(args)

    args = argparse.Namespace(server_command="uninstall", config="/nonexistent/mesh.yaml")
    with patch.object(server, "_server_uninstall") as mock_handler:
        server.dispatch(args)
    mock_handler.assert_called_once_with(args)
