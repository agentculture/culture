# tests/test_register_cli.py
import argparse

import pytest


def test_register_current_dir(tmp_path, monkeypatch):
    """Register with no path uses cwd."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    save_server_config(str(server_yaml), ServerConfig(server=ServerConnConfig(name="spark")))

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text("suffix: myagent\nbackend: claude\n")

    monkeypatch.chdir(str(proj))

    from culture.cli.agent import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=None, suffix=None)
    _cmd_register(args)

    config = load_server_config(str(server_yaml))
    assert "myagent" in config.manifest
    assert config.manifest["myagent"] == str(proj.resolve())


def test_register_explicit_path(tmp_path):
    """Register with explicit path."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    save_server_config(str(server_yaml), ServerConfig(server=ServerConnConfig(name="spark")))

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text("suffix: myagent\nbackend: claude\n")

    from culture.cli.agent import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=str(proj), suffix=None)
    _cmd_register(args)

    config = load_server_config(str(server_yaml))
    assert "myagent" in config.manifest


def test_register_multi_agent_needs_suffix(tmp_path, capsys):
    """Multi-agent culture.yaml without --suffix errors."""
    from culture.config import ServerConfig, ServerConnConfig, save_server_config

    server_yaml = tmp_path / "server.yaml"
    save_server_config(str(server_yaml), ServerConfig(server=ServerConnConfig(name="spark")))

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text(
        "agents:\n  - suffix: a\n    backend: claude\n  - suffix: b\n    backend: codex\n"
    )

    from culture.cli.agent import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=str(proj), suffix=None)
    with pytest.raises(SystemExit):
        _cmd_register(args)


def test_register_no_culture_yaml(tmp_path):
    """Register dir without culture.yaml errors."""
    from culture.config import ServerConfig, ServerConnConfig, save_server_config

    server_yaml = tmp_path / "server.yaml"
    save_server_config(str(server_yaml), ServerConfig(server=ServerConnConfig(name="spark")))

    from culture.cli.agent import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=str(tmp_path / "empty"), suffix=None)
    with pytest.raises(SystemExit):
        _cmd_register(args)


def test_unregister_by_suffix(tmp_path):
    """Unregister removes from manifest."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    save_server_config(
        str(server_yaml),
        ServerConfig(server=ServerConnConfig(name="spark"), manifest={"culture": "/tmp/proj"}),
    )

    from culture.cli.agent import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target="culture")
    _cmd_unregister(args)

    config = load_server_config(str(server_yaml))
    assert "culture" not in config.manifest


def test_unregister_by_nick(tmp_path):
    """Unregister accepts full nick format."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    save_server_config(
        str(server_yaml),
        ServerConfig(server=ServerConnConfig(name="spark"), manifest={"culture": "/tmp/proj"}),
    )

    from culture.cli.agent import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target="spark-culture", all_missing=False)
    _cmd_unregister(args)

    config = load_server_config(str(server_yaml))
    assert "culture" not in config.manifest


def test_unregister_all_missing_gcs_stale_entries(tmp_path, capsys):
    """`--all-missing` removes manifest entries whose directory no longer exists."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"

    alive_dir = tmp_path / "alive"
    alive_dir.mkdir()
    missing_a = tmp_path / "ghost-a"
    missing_b = tmp_path / "ghost-b"
    # Note: missing_a/b are intentionally NOT created on disk.

    manifest = {
        "alive": str(alive_dir),
        "prd-check-w": str(missing_a),
        "qa658b": str(missing_b),
    }
    save_server_config(
        str(server_yaml),
        ServerConfig(server=ServerConnConfig(name="local"), manifest=manifest),
    )

    from culture.cli.agent import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target=None, all_missing=True)
    _cmd_unregister(args)

    config = load_server_config(str(server_yaml))
    assert "alive" in config.manifest
    assert "prd-check-w" not in config.manifest
    assert "qa658b" not in config.manifest

    out = capsys.readouterr().out
    assert "Unregistered 2 stale entries" in out
    assert "local-prd-check-w" in out
    assert "local-qa658b" in out


def test_unregister_all_missing_is_idempotent(tmp_path, capsys):
    """Re-running `--all-missing` on a clean manifest reports zero removals."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    alive_dir = tmp_path / "alive"
    alive_dir.mkdir()

    save_server_config(
        str(server_yaml),
        ServerConfig(server=ServerConnConfig(name="local"), manifest={"alive": str(alive_dir)}),
    )

    from culture.cli.agent import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target=None, all_missing=True)
    _cmd_unregister(args)
    out_first = capsys.readouterr().out
    assert "Unregistered 0 stale entries" in out_first

    # Re-run on the same (still-clean) manifest.
    _cmd_unregister(args)
    out_second = capsys.readouterr().out
    assert "Unregistered 0 stale entries" in out_second

    config = load_server_config(str(server_yaml))
    assert "alive" in config.manifest


def test_unregister_all_missing_handles_empty_directory_field(tmp_path, capsys):
    """Manifest entries with an empty/None directory string are treated as stale."""
    from culture.config import (
        ServerConfig,
        ServerConnConfig,
        load_server_config,
        save_server_config,
    )

    server_yaml = tmp_path / "server.yaml"
    save_server_config(
        str(server_yaml),
        ServerConfig(
            server=ServerConnConfig(name="local"),
            manifest={"broken": ""},
        ),
    )

    from culture.cli.agent import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target=None, all_missing=True)
    _cmd_unregister(args)

    config = load_server_config(str(server_yaml))
    assert "broken" not in config.manifest
    out = capsys.readouterr().out
    assert "Unregistered 1 stale entries" in out
    assert "local-broken" in out


def test_unregister_without_target_or_flag_errors(tmp_path, capsys):
    """Running unregister with neither a target nor --all-missing exits with usage error."""
    from culture.config import ServerConfig, ServerConnConfig, save_server_config

    server_yaml = tmp_path / "server.yaml"
    save_server_config(
        str(server_yaml),
        ServerConfig(server=ServerConnConfig(name="local"), manifest={}),
    )

    from culture.cli.agent import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target=None, all_missing=False)
    with pytest.raises(SystemExit):
        _cmd_unregister(args)
    err = capsys.readouterr().err
    assert "Usage" in err
