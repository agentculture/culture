"""Tests for culture/cli/doctor.py — the doctor CLI group."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest
import yaml

from culture.cli.doctor import dispatch


def _args(config, root=None, json=False, fix=False):
    return argparse.Namespace(
        config=str(config),
        root=str(root) if root else None,
        json=json,
        fix=fix,
    )


def _write_server_yaml(path, manifest=None):
    """Write a minimal server.yaml at *path*."""
    data = {"server": {"name": "spark"}, "agents": manifest or {}}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def _write_culture_yaml(directory, suffix):
    """Write a minimal culture.yaml into *directory*."""
    path = Path(directory) / "culture.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump({"suffix": suffix, "backend": "claude"}, f)


def test_clean_exits_zero(tmp_path, capsys):
    """A clean workspace with one valid registered repo exits 0."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()

    culture_dir = ws / "culture"
    _write_culture_yaml(str(culture_dir), "culture")

    _write_server_yaml(server_yaml, manifest={"culture": str(culture_dir)})

    with pytest.raises(SystemExit) as e:
        dispatch(_args(server_yaml, root=ws))
    assert e.value.code == 0
    assert "no drift" in capsys.readouterr().out


def test_problems_exit_nonzero_and_name_repo(tmp_path, capsys):
    """Broken manifest entries produce nonzero exit and name the repo."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()

    # Broken entry: directory does not exist
    _write_server_yaml(server_yaml, manifest={"shushu": str(ws / "nope")})

    with pytest.raises(SystemExit) as e:
        dispatch(_args(server_yaml, root=ws))
    assert e.value.code != 0
    out = capsys.readouterr().out
    assert "shushu" in out
    assert "culture agents unregister shushu" in out


def test_json_output(tmp_path, capsys):
    """--json emits parseable JSON with class1 findings."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()

    _write_server_yaml(server_yaml, manifest={"shushu": str(ws / "nope")})

    with pytest.raises(SystemExit):
        dispatch(_args(server_yaml, root=ws, json=True))

    out = capsys.readouterr().out
    data = json.loads(out)
    assert "class1" in data
    assert data["exit_code"] != 0


def test_no_writes_without_fix(tmp_path):
    """dispatch with fix=False must not modify server.yaml."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()

    # Unregistered repo so class-2 is non-empty
    rogue_dir = ws / "rogue"
    _write_culture_yaml(str(rogue_dir), "rogue")

    _write_server_yaml(server_yaml, manifest={})
    original_bytes = server_yaml.read_bytes()

    with pytest.raises(SystemExit):
        dispatch(_args(server_yaml, root=ws, fix=False))

    assert server_yaml.read_bytes() == original_bytes


def test_fix_registers_class2(tmp_path, capsys):
    """--fix registers unregistered repos into server.yaml."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()

    rogue_dir = ws / "rogue"
    _write_culture_yaml(str(rogue_dir), "rogue")

    _write_server_yaml(server_yaml, manifest={})

    with pytest.raises(SystemExit):
        dispatch(_args(server_yaml, root=ws, fix=True))

    # Reload server.yaml and check the manifest
    with open(server_yaml) as f:
        raw = yaml.safe_load(f)
    assert "rogue" in raw["agents"]

    out = capsys.readouterr().out
    assert "registered: rogue" in out
