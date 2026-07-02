# tests/test_register_cli.py
import argparse

import pytest


def test_register_current_dir(tmp_path, monkeypatch):
    """Register with no path uses cwd."""
    from culture_core.config import (
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

    from culture_core.cli.agents import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=None, suffix=None)
    _cmd_register(args)

    config = load_server_config(str(server_yaml))
    assert "myagent" in config.manifest
    assert config.manifest["myagent"] == str(proj.resolve())


def test_register_explicit_path(tmp_path):
    """Register with explicit path."""
    from culture_core.config import (
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

    from culture_core.cli.agents import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=str(proj), suffix=None)
    _cmd_register(args)

    config = load_server_config(str(server_yaml))
    assert "myagent" in config.manifest


def test_register_multi_agent_needs_suffix(tmp_path, capsys):
    """Multi-agent culture.yaml without --suffix errors."""
    from culture_core.config import ServerConfig, ServerConnConfig, save_server_config

    server_yaml = tmp_path / "server.yaml"
    save_server_config(str(server_yaml), ServerConfig(server=ServerConnConfig(name="spark")))

    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "culture.yaml").write_text(
        "agents:\n  - suffix: a\n    backend: claude\n  - suffix: b\n    backend: codex\n"
    )

    from culture_core.cli._errors import CultureError
    from culture_core.cli.agents import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=str(proj), suffix=None)
    with pytest.raises(CultureError) as exc:
        _cmd_register(args)
    assert "Multiple agents" in exc.value.message
    assert "--suffix" in exc.value.remediation


def test_register_no_culture_yaml(tmp_path):
    """Register dir without culture.yaml errors."""
    from culture_core.config import ServerConfig, ServerConnConfig, save_server_config

    server_yaml = tmp_path / "server.yaml"
    save_server_config(str(server_yaml), ServerConfig(server=ServerConnConfig(name="spark")))

    from culture_core.cli._errors import CultureError
    from culture_core.cli.agents import _cmd_register

    args = argparse.Namespace(config=str(server_yaml), path=str(tmp_path / "empty"), suffix=None)
    with pytest.raises(CultureError) as exc:
        _cmd_register(args)
    assert "No culture.yaml found" in exc.value.message
    assert exc.value.remediation


def test_unregister_by_suffix(tmp_path):
    """Unregister removes from manifest."""
    from culture_core.config import (
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

    from culture_core.cli.agents import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target="culture")
    _cmd_unregister(args)

    config = load_server_config(str(server_yaml))
    assert "culture" not in config.manifest


def test_unregister_by_nick(tmp_path):
    """Unregister accepts full nick format."""
    from culture_core.config import (
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

    from culture_core.cli.agents import _cmd_unregister

    args = argparse.Namespace(config=str(server_yaml), target="spark-culture")
    _cmd_unregister(args)

    config = load_server_config(str(server_yaml))
    assert "culture" not in config.manifest
