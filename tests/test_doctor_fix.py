"""Tests for culture doctor fix actions (register_unregistered)."""

from pathlib import Path

import pytest
import yaml

from culture.doctor.fix import register_unregistered
from culture.doctor.model import Finding


def _write_server_yaml(path: str) -> None:
    """Write a minimal manifest-format server.yaml."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write("server:\n  name: spark\nagents: {}\n")


def _write_repo_culture_yaml(repo_dir: str, suffix: str) -> None:
    """Write a minimal culture.yaml in repo_dir."""
    Path(repo_dir).mkdir(parents=True, exist_ok=True)
    culture_path = Path(repo_dir) / "culture.yaml"
    with open(culture_path, "w") as f:
        f.write(f"suffix: {suffix}\nbackend: claude\n")


def test_registers_unregistered_repo(tmp_path):
    server_yaml = tmp_path / "server.yaml"
    repo = tmp_path / "foo"
    _write_server_yaml(str(server_yaml))
    _write_repo_culture_yaml(str(repo), "foo")

    finding = Finding(2, "warning", "foo", str(repo), "", "")
    added = register_unregistered(str(server_yaml), [finding])

    assert added == [("foo", str(repo))]

    with open(server_yaml) as f:
        data = yaml.safe_load(f)
    assert data["agents"]["foo"] == str(repo)


def test_idempotent(tmp_path):
    server_yaml = tmp_path / "server.yaml"
    repo = tmp_path / "foo"
    _write_server_yaml(str(server_yaml))
    _write_repo_culture_yaml(str(repo), "foo")

    finding = Finding(2, "warning", "foo", str(repo), "", "")
    added1 = register_unregistered(str(server_yaml), [finding])
    assert added1 == [("foo", str(repo))]

    with open(server_yaml) as f:
        data_after_first = yaml.safe_load(f)

    added2 = register_unregistered(str(server_yaml), [finding])
    assert added2 == []

    with open(server_yaml) as f:
        data_after_second = yaml.safe_load(f)

    assert data_after_first["agents"] == data_after_second["agents"]


def test_empty_findings_no_writes(tmp_path):
    server_yaml = tmp_path / "server.yaml"
    _write_server_yaml(str(server_yaml))

    before = server_yaml.read_bytes()
    added = register_unregistered(str(server_yaml), [])
    after = server_yaml.read_bytes()

    assert added == []
    assert before == after


def test_culture_yaml_never_modified(tmp_path):
    server_yaml = tmp_path / "server.yaml"
    repo = tmp_path / "foo"
    _write_server_yaml(str(server_yaml))
    _write_repo_culture_yaml(str(repo), "foo")

    culture_yaml = repo / "culture.yaml"
    before = culture_yaml.read_bytes()

    finding = Finding(2, "warning", "foo", str(repo), "", "")
    register_unregistered(str(server_yaml), [finding])

    after = culture_yaml.read_bytes()
    assert before == after
