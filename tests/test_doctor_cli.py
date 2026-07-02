"""Tests for culture_core/cli/doctor.py — the doctor CLI group."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest
import yaml

from culture_core.cli.doctor import dispatch


@pytest.fixture(autouse=True)
def _no_real_service_probe(monkeypatch):
    """Keep dispatch hermetic: never probe this host's real systemd units.

    Class-4 (service health, #15) tests inject their own canned seam."""
    monkeypatch.setattr("culture_core.doctor.checks.list_services", lambda: [])


def _fake_service_probe(monkeypatch, output):
    """Point the class-4 check at one fake unit with canned systemctl output."""
    monkeypatch.setattr("culture_core.doctor.checks.get_platform", lambda: "linux")
    monkeypatch.setattr(
        "culture_core.doctor.checks.shutil.which", lambda _cmd: "/usr/bin/systemctl"
    )
    monkeypatch.setattr(
        "culture_core.doctor.checks.list_services", lambda: ["culture-server-spark"]
    )
    monkeypatch.setattr("culture_core.doctor.checks._systemctl_show", lambda unit: output)


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


def test_json_fix_output_stays_valid_json(tmp_path, capsys):
    """`--json --fix` must emit a single parseable JSON object — the registered
    pairs go INTO the payload, not as trailing 'registered: ...' lines that
    would corrupt the stream (Qodo finding #3)."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()

    rogue_dir = ws / "rogue"
    _write_culture_yaml(str(rogue_dir), "rogue")

    _write_server_yaml(server_yaml, manifest={})

    with pytest.raises(SystemExit):
        dispatch(_args(server_yaml, root=ws, json=True, fix=True))

    out = capsys.readouterr().out
    data = json.loads(out)  # must not raise — the whole stream is one JSON object
    assert [r["suffix"] for r in data["registered"]] == ["rogue"]
    # And the human-readable trailer must NOT leak into JSON mode.
    assert "registered: rogue" not in out


def test_json_output_carries_empty_registered_without_fix(tmp_path, capsys):
    """Without --fix the JSON still exposes a stable `registered` key (empty)."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_server_yaml(server_yaml, manifest={"shushu": str(ws / "nope")})

    with pytest.raises(SystemExit):
        dispatch(_args(server_yaml, root=ws, json=True))

    data = json.loads(capsys.readouterr().out)
    assert data["registered"] == []


def test_parked_service_renders_and_exits_nonzero(tmp_path, capsys, monkeypatch):
    """A parked (failed) unit surfaces under 'Service health (class 4)' with
    its remediation and fails the run (#15)."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_server_yaml(server_yaml, manifest={})

    _fake_service_probe(
        monkeypatch,
        "ActiveState=failed\nNRestarts=2\nExecMainStatus=78\nResult=exit-code\n",
    )

    with pytest.raises(SystemExit) as e:
        dispatch(_args(server_yaml, root=ws))
    assert e.value.code == 1

    out = capsys.readouterr().out
    assert "Service health (class 4)" in out
    assert "culture-server-spark" in out
    assert "reset-failed" in out


def test_looping_service_warns_but_exits_zero(tmp_path, capsys, monkeypatch):
    """A restart-looping unit is surfaced as a warning without failing the run."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_server_yaml(server_yaml, manifest={})

    _fake_service_probe(
        monkeypatch,
        "ActiveState=activating\nNRestarts=12\nExecMainStatus=1\nResult=exit-code\n",
    )

    with pytest.raises(SystemExit) as e:
        dispatch(_args(server_yaml, root=ws))
    assert e.value.code == 0

    out = capsys.readouterr().out
    assert "restart-looping" in out
    assert "journalctl --user -u culture-server-spark.service" in out


def test_json_includes_class4(tmp_path, capsys, monkeypatch):
    """--json exposes the class4 findings with the rest of the payload."""
    server_yaml = tmp_path / "server.yaml"
    ws = tmp_path / "ws"
    ws.mkdir()
    _write_server_yaml(server_yaml, manifest={})

    _fake_service_probe(
        monkeypatch,
        "ActiveState=failed\nNRestarts=2\nExecMainStatus=78\nResult=exit-code\n",
    )

    with pytest.raises(SystemExit):
        dispatch(_args(server_yaml, root=ws, json=True))

    data = json.loads(capsys.readouterr().out)
    assert len(data["class4"]) == 1
    assert data["class4"][0]["severity"] == "error"
    assert data["class4"][0]["fix_hint"]
    assert data["exit_code"] == 1


def test_invalid_root_exits_cleanly(tmp_path, capsys):
    """A non-existent --root exits with a clear error, not an uncaught
    iterdir() traceback (Qodo finding #4)."""
    server_yaml = tmp_path / "server.yaml"
    _write_server_yaml(server_yaml, manifest={})

    with pytest.raises(SystemExit) as e:
        dispatch(_args(server_yaml, root=tmp_path / "does-not-exist"))

    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "--root is not a directory" in err
