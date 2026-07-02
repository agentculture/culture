# tests/test_agent_install_cli.py
"""Tests for `culture agents install/uninstall <nick>` (#348)."""

import argparse
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _write_manifest(tmp_path, server_name="spark", suffixes=("claude",)):
    """Write a minimal server.yaml manifest with the given suffixes."""
    from culture_core.config import (
        ServerConfig,
        ServerConnConfig,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    workdir = tmp_path / "proj"
    workdir.mkdir(exist_ok=True)
    config = ServerConfig(
        server=ServerConnConfig(name=server_name),
        manifest={suffix: str(workdir.resolve()) for suffix in suffixes},
    )
    save_server_config(str(server_yaml), config)
    return server_yaml


def test_install_passes_correct_argv_to_install_service(tmp_path):
    """`culture agents install <suffix>` builds argv using sys.executable -m culture_core."""
    from culture_core.cli.agents import _cmd_install

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="claude")

    with patch("culture_core.persistence.install_service") as mock_install:
        mock_install.return_value = Path("/tmp/fake.service")
        _cmd_install(args)

    assert mock_install.call_count == 1
    args_, _kwargs = mock_install.call_args
    svc_name, command, description = args_[0], args_[1], args_[2]

    assert svc_name == "culture-agent-spark-claude"
    assert command == [
        sys.executable,
        "-m",
        "culture_core",
        "agents",
        "start",
        "spark-claude",
        "--foreground",
    ]
    assert description == "culture-core agents spark-claude"


def test_install_accepts_full_nick(tmp_path):
    """The verb also accepts the full <server>-<suffix> nick form."""
    from culture_core.cli.agents import _cmd_install

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="spark-claude")

    with (
        patch("culture_core.persistence.install_service") as mock_install,
        patch("shutil.which", return_value="/usr/bin/culture"),
    ):
        mock_install.return_value = Path("/tmp/fake.service")
        _cmd_install(args)

    svc_name = mock_install.call_args[0][0]
    assert svc_name == "culture-agent-spark-claude"


def test_install_omits_config_flag_in_argv(tmp_path):
    """Regression guard: ExecStart argv must NOT carry --config.

    Pinning a per-workdir agents.yaml here re-introduces the crashloop
    fixed in PR #344. Mirrors
    test_setup_update_cli.py::test_install_mesh_services_omits_legacy_config_path.
    """
    from culture_core.cli.agents import _cmd_install

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="claude")

    with (
        patch("culture_core.persistence.install_service") as mock_install,
        patch("shutil.which", return_value="/usr/bin/culture"),
    ):
        mock_install.return_value = Path("/tmp/fake.service")
        _cmd_install(args)

    command = mock_install.call_args[0][1]
    assert "--config" not in command, (
        f"install argv carries --config: {command}. "
        "Regression: the legacy <workdir>/.culture/agents.yaml pin must stay out."
    )
    assert not any(".culture/agents.yaml" in tok for tok in command)


def test_install_rejects_unknown_nick(tmp_path):
    """An agent not in the manifest is rejected with exit code 1."""
    from culture_core.cli._errors import CultureError
    from culture_core.cli.agents import _cmd_install

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="ghost")

    with (
        patch("culture_core.persistence.install_service") as mock_install,
        pytest.raises(CultureError) as exc,
    ):
        _cmd_install(args)

    assert exc.value.code == 1
    assert "spark-ghost" in exc.value.message
    assert "not in manifest" in exc.value.message
    assert "culture agents register" in exc.value.remediation
    mock_install.assert_not_called()


def test_install_orders_agent_unit_after_server_unit(tmp_path):
    """Agent units gain After=/Wants= ordering on the server unit (durable
    mesh): the server name resolves from the same config the agent's nick
    resolves from."""
    from culture_core.cli.agents import _cmd_install

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="claude")

    with patch("culture_core.persistence.install_service") as mock_install:
        mock_install.return_value = Path("/tmp/fake.service")
        _cmd_install(args)

    _args_, kwargs = mock_install.call_args
    assert kwargs.get("after") == "culture-server-spark.service"


def test_uninstall_calls_uninstall_service_with_correct_name(tmp_path):
    """`culture agents uninstall <suffix>` calls uninstall_service('culture-agent-<full>')."""
    from culture_core.cli.agents import _cmd_uninstall

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="claude")

    with patch("culture_core.persistence.uninstall_service") as mock_uninstall:
        _cmd_uninstall(args)

    mock_uninstall.assert_called_once_with("culture-agent-spark-claude")


def test_uninstall_accepts_full_nick(tmp_path):
    """uninstall also accepts <server>-<suffix>."""
    from culture_core.cli.agents import _cmd_uninstall

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="spark-claude")

    with patch("culture_core.persistence.uninstall_service") as mock_uninstall:
        _cmd_uninstall(args)

    mock_uninstall.assert_called_once_with("culture-agent-spark-claude")


def test_uninstall_rejects_unknown_nick(tmp_path):
    """uninstall against a non-manifest agent exits 1 instead of silently no-op'ing."""
    from culture_core.cli._errors import CultureError
    from culture_core.cli.agents import _cmd_uninstall

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="ghost")

    with (
        patch("culture_core.persistence.uninstall_service") as mock_uninstall,
        pytest.raises(CultureError) as exc,
    ):
        _cmd_uninstall(args)

    assert exc.value.code == 1
    mock_uninstall.assert_not_called()


def test_install_disambiguates_suffix_that_starts_with_server_prefix(tmp_path):
    """Regression: a bare suffix like `spark-claude` (server `spark`) must
    not be silently stripped to `claude`. The manifest lookup takes priority
    over prefix stripping."""
    from culture_core.cli.agents import _cmd_install

    server_yaml = _write_manifest(tmp_path, server_name="spark", suffixes=("spark-claude",))
    args = argparse.Namespace(config=str(server_yaml), nick="spark-claude")

    with (
        patch("culture_core.persistence.install_service") as mock_install,
        patch("shutil.which", return_value="/usr/bin/culture"),
    ):
        mock_install.return_value = Path("/tmp/fake.service")
        _cmd_install(args)

    svc_name = mock_install.call_args[0][0]
    command = mock_install.call_args[0][1]
    assert svc_name == "culture-agent-spark-spark-claude"
    assert "spark-spark-claude" in command


def test_install_uninstall_parsers_registered():
    """Both verbs appear in the argparse surface."""
    from culture_core.cli import _build_parser

    p = _build_parser()
    args = p.parse_args(["agents", "install", "claude"])
    assert args.command == "agents"
    assert args.agents_command == "install"
    assert args.nick == "claude"

    args = p.parse_args(["agents", "uninstall", "claude"])
    assert args.agents_command == "uninstall"
    assert args.nick == "claude"


def test_install_uninstall_in_dispatch():
    """Handlers are wired into the dispatch table."""
    from culture_core.cli import agents

    assert callable(agents._cmd_install)
    assert callable(agents._cmd_uninstall)
