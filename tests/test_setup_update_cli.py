# tests/test_setup_update_cli.py
"""Lightweight parser tests for setup and update subcommands."""

import subprocess
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
    """update subcommand parses --dry-run, --skip-upgrade, --config, --upgrade-timeout."""
    p = _build_parser()
    args = p.parse_args(["mesh", "update", "--dry-run", "--skip-upgrade"])
    assert args.command == "mesh"
    assert args.mesh_command == "update"
    assert args.dry_run is True
    assert args.skip_upgrade is True
    # default upgrade-timeout matches the constant in culture.cli.mesh
    from culture.cli.mesh import _UPGRADE_TIMEOUT_SECONDS

    assert args.upgrade_timeout == _UPGRADE_TIMEOUT_SECONDS
    assert (
        _UPGRADE_TIMEOUT_SECONDS >= 300
    ), "default must be high enough to absorb a fresh major-version install"

    args = p.parse_args(["mesh", "update", "--config", "/tmp/mesh.yaml"])
    assert args.config == "/tmp/mesh.yaml"

    args = p.parse_args(["mesh", "update", "--upgrade-timeout", "900"])
    assert args.upgrade_timeout == 900


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


# ---- _run_upgrade tests ----


def test_run_upgrade_streams_output():
    """_run_upgrade must NOT capture output — uv/pip progress has to reach the terminal.

    Capturing output is what made the bare 'culture mesh update' look like a
    hang on slow links: the 73 MiB claude-agent-sdk download is silent for
    ~2 minutes when stdout/stderr are swallowed. Guard against a regression.
    """
    from culture.cli.mesh import _run_upgrade

    with patch(f"{_MESH_MOD}.subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        _run_upgrade("uv", ["uv", "tool", "upgrade", "culture"], timeout_seconds=600)

    assert mock_run.call_count == 1
    _args, kwargs = mock_run.call_args
    assert (
        "capture_output" not in kwargs
    ), "subprocess.run must inherit stdout/stderr so progress is visible"
    assert kwargs.get("timeout") == 600
    # stdin=DEVNULL guards against the upgrader hanging on an interactive prompt
    # (e.g., pip dependency-resolution confirmations) when run non-interactively.
    assert kwargs.get("stdin") == subprocess.DEVNULL


def test_run_upgrade_timeout_message_has_three_hints(capsys):
    """On timeout, the hint must offer all three recovery paths.

    Listing only --skip-upgrade leaves a user who genuinely wants the new
    version with no path forward. The hint must also point at running uv/pip
    directly and at extending the timeout.
    """
    from culture.cli.mesh import _run_upgrade

    with patch(f"{_MESH_MOD}.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=5)
        with pytest.raises(SystemExit) as exc_info:
            _run_upgrade("uv", ["uv", "tool", "upgrade", "culture"], timeout_seconds=5)

    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "uv tool upgrade culture" in err  # direct-tool suggestion
    assert "--upgrade-timeout" in err  # extend-timeout suggestion
    assert "--skip-upgrade" in err  # skip suggestion
    assert "timed out after 5s" in err


def test_run_upgrade_timeout_message_uses_pip_when_pip_selected(capsys):
    """The direct-tool hint should match whichever upgrader was picked."""
    from culture.cli.mesh import _run_upgrade

    with patch(f"{_MESH_MOD}.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="pip", timeout=5)
        with pytest.raises(SystemExit):
            _run_upgrade("pip", ["pip", "install", "--upgrade", "culture"], timeout_seconds=5)

    err = capsys.readouterr().err
    assert "pip install --upgrade culture" in err
    assert "uv tool upgrade culture" not in err


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


# ---- _install_mesh_services regression tests ----
#
# Regression guard for the 10.3.x crashloop bug: systemd units used to
# pin --config <workdir>/.culture/agents.yaml — a layout culture
# migrated away from. On a real machine the units crashed 38000+ times
# under systemd's restart-on-failure policy because the path didn't
# exist. The fix drops --config so `culture agent start` falls through
# to its argparse default (~/.culture/server.yaml).


def _captured_install_calls(install_mock) -> list:
    """Pull (svc_name, command, description) tuples out of a mocked
    install_service callable. install_service is called as
    install_service(name, command, description) — extract the kwargs
    or positional args uniformly."""
    out = []
    for call in install_mock.call_args_list:
        args, kwargs = call
        if len(args) >= 3:
            out.append((args[0], args[1], args[2]))
        else:
            out.append((kwargs["name"], kwargs["command"], kwargs.get("description", "")))
    return out


def test_install_mesh_services_omits_legacy_config_path():
    """Generated agent unit ExecStart must not pin <workdir>/.culture/agents.yaml.

    The systemd unit body should only carry `culture agent start <nick>
    --foreground`; any --config token would re-introduce the crashloop
    when the legacy per-workdir layout doesn't exist.
    """
    from culture.cli.mesh import _install_mesh_services

    mesh = MeshConfig(
        server=MeshServerConfig(name="spark", host="127.0.0.1", port=6667),
        agents=[
            MeshAgentConfig(nick="claude", workdir="/home/u/work"),
            MeshAgentConfig(nick="codex", workdir="/home/u/work2"),
        ],
    )

    # install_service is imported inside the function (`from
    # culture.persistence import install_service`), so patch the source
    # module rather than the attribute on culture.cli.mesh.
    with (
        patch("culture.persistence.install_service") as mock_install,
        patch(f"{_MESH_MOD}.build_server_start_cmd", return_value=["culture", "server", "start"]),
    ):
        _install_mesh_services(mesh, "spark", "/usr/bin/culture", "/etc/mesh.yaml")

    calls = _captured_install_calls(mock_install)
    agent_calls = [c for c in calls if c[0].startswith("culture-agent-")]
    assert len(agent_calls) == 2, f"expected 2 agent units, got {len(agent_calls)}"
    for svc_name, command, _desc in agent_calls:
        assert "--config" not in command, (
            f"{svc_name} ExecStart still carries --config: {command}. "
            "Regression: the legacy <workdir>/.culture/agents.yaml pin must stay out."
        )
        assert not any(
            ".culture/agents.yaml" in tok for tok in command
        ), f"{svc_name} ExecStart references the legacy agents.yaml path: {command}"
        # Sanity: the command is still a valid agent-start invocation.
        assert "agent" in command and "start" in command and "--foreground" in command


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
