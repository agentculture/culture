# tests/test_setup_update_cli.py
"""Lightweight parser tests for setup and update subcommands."""

from culture.cli import _build_parser


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
