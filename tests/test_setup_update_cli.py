# tests/test_setup_update_cli.py
"""Lightweight parser tests for setup and update subcommands."""

from unittest.mock import patch

import pytest

from culture.cli import _build_parser
from culture.mesh_config import MeshAgentConfig, MeshConfig, MeshLinkConfig, MeshServerConfig


def test_setup_parser():
    """setup subcommand parses --config and --uninstall."""
    p = _build_parser()
    args = p.parse_args(["mesh", "setup", "--uninstall"])
    assert args.command == "mesh"
    assert args.mesh_command == "setup"
    assert args.uninstall is True

    args = p.parse_args(["mesh", "setup", "--config", "/tmp/mesh.yaml"])
    assert args.config == "/tmp/mesh.yaml"


def test_update_parser():
    """update subcommand parses --dry-run, --skip-upgrade, --config."""
    p = _build_parser()
    args = p.parse_args(["mesh", "update", "--dry-run", "--skip-upgrade"])
    assert args.command == "mesh"
    assert args.mesh_command == "update"
    assert args.dry_run is True
    assert args.skip_upgrade is True

    args = p.parse_args(["mesh", "update", "--config", "/tmp/mesh.yaml"])
    assert args.config == "/tmp/mesh.yaml"


def test_setup_in_dispatch():
    """setup command is wired into the mesh module."""
    from culture.cli import mesh

    assert hasattr(mesh, "_cmd_setup")
    assert callable(mesh._cmd_setup)


def test_update_in_dispatch():
    """update command is wired into the mesh module."""
    from culture.cli import mesh

    assert hasattr(mesh, "_cmd_update")
    assert callable(mesh._cmd_update)


# ---- _cmd_update behaviour tests ----

_MESH_MOD = "culture.cli.mesh"


@pytest.fixture
def update_args(tmp_path):
    """Minimal argparse namespace for _cmd_update."""
    p = _build_parser()
    config = str(tmp_path / "mesh.yaml")
    return p.parse_args(["mesh", "update", "--skip-upgrade", "--dry-run", "--config", config])


@patch("culture.pidfile.list_servers")
@patch(f"{_MESH_MOD}._resolve_mesh_for_server")
@patch(f"{_MESH_MOD}._restart_mesh_services")
@patch(f"{_MESH_MOD}._upgrade_culture_package", return_value=True)
def test_update_discovers_running_servers(
    _mock_upgrade, mock_restart, mock_resolve, mock_list, update_args
):
    """_cmd_update restarts every running server, not just mesh.yaml."""
    from culture.cli.mesh import _cmd_update

    spark_mesh = MeshConfig(server=MeshServerConfig(name="spark"))
    mock_list.return_value = [{"name": "spark", "pid": 1, "port": 6667}]
    mock_resolve.return_value = spark_mesh

    _cmd_update(update_args)

    mock_resolve.assert_called_once_with("spark", update_args.config)
    mock_restart.assert_called_once()
    assert mock_restart.call_args[0][1] == "spark"


@patch("culture.pidfile.list_servers", return_value=[])
@patch("culture.mesh_config.load_mesh_config")
@patch(f"{_MESH_MOD}._restart_mesh_services")
@patch(f"{_MESH_MOD}._upgrade_culture_package", return_value=True)
def test_update_falls_back_to_mesh_yaml_when_no_servers(
    _mock_upgrade, mock_restart, mock_load, _mock_list, update_args
):
    """When no servers are running, fall back to mesh.yaml."""
    from culture.cli.mesh import _cmd_update

    mesh = MeshConfig(server=MeshServerConfig(name="culture"))
    mock_load.return_value = mesh

    _cmd_update(update_args)

    mock_load.assert_called_once_with(update_args.config)
    mock_restart.assert_called_once()
    assert mock_restart.call_args[0][1] == "culture"


@patch("culture.pidfile.list_servers")
@patch(f"{_MESH_MOD}._restart_mesh_services")
@patch(f"{_MESH_MOD}._upgrade_culture_package", return_value=True)
def test_update_skips_server_without_config(
    _mock_upgrade, mock_restart, mock_list, update_args, capsys
):
    """Servers with no matching config are skipped with a warning."""
    from culture.cli.mesh import _cmd_update

    mock_list.return_value = [{"name": "unknown", "pid": 1, "port": 6667}]

    with patch(f"{_MESH_MOD}._resolve_mesh_for_server", return_value=None):
        _cmd_update(update_args)

    mock_restart.assert_not_called()
    assert "no config found" in capsys.readouterr().err


# ---- _resolve_mesh_for_server tests ----


def test_resolve_uses_mesh_yaml_when_name_matches(tmp_path):
    """_resolve_mesh_for_server returns mesh.yaml config when server name matches."""
    from culture.cli.mesh import _resolve_mesh_for_server

    mesh = MeshConfig(
        server=MeshServerConfig(name="spark", links=[MeshLinkConfig(name="thor", host="1.2.3.4")]),
        agents=[MeshAgentConfig(nick="claude", workdir="/tmp")],
    )
    config_path = str(tmp_path / "mesh.yaml")
    from culture.mesh_config import save_mesh_config

    save_mesh_config(mesh, config_path)

    result = _resolve_mesh_for_server("spark", config_path)
    assert result is not None
    assert result.server.name == "spark"
    assert len(result.server.links) == 1


def test_resolve_rebuilds_from_agents_yaml_preserving_links(tmp_path):
    """When mesh.yaml has wrong name, rebuild from agents.yaml and keep links."""
    from culture.cli.mesh import _resolve_mesh_for_server
    from culture.clients.claude.config import AgentConfig, DaemonConfig, ServerConnConfig
    from culture.mesh_config import save_mesh_config

    # mesh.yaml says "culture" but running server is "spark"
    old_mesh = MeshConfig(
        server=MeshServerConfig(
            name="culture",
            host="127.0.0.1",
            port=7000,
            links=[MeshLinkConfig(name="thor", host="1.2.3.4")],
        ),
    )
    config_path = str(tmp_path / "mesh.yaml")
    save_mesh_config(old_mesh, config_path)

    # agents.yaml says "spark"
    daemon = DaemonConfig(
        server=ServerConnConfig(name="spark", host="localhost", port=6667),
        agents=[AgentConfig(nick="spark-claude", agent="claude", directory="/tmp")],
    )

    with patch(f"{_MESH_MOD}.DEFAULT_CONFIG", str(tmp_path / "agents.yaml")):
        from culture.clients.claude.config import save_config

        save_config(str(tmp_path / "agents.yaml"), daemon)
        result = _resolve_mesh_for_server("spark", config_path)

    assert result is not None
    assert result.server.name == "spark"
    assert len(result.agents) == 1
    assert result.agents[0].nick == "claude"
    # Server settings from old mesh.yaml are preserved
    assert result.server.host == "127.0.0.1"
    assert result.server.port == 7000
    assert len(result.server.links) == 1
    assert result.server.links[0].name == "thor"
