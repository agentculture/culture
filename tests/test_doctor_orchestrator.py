"""Integration tests for culture_core/doctor/__init__.py — the run_doctor orchestrator."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from culture_core.config import ServerConfig, ServerConnConfig
from culture_core.doctor import run_doctor


@pytest.fixture(autouse=True)
def _no_real_service_probe(monkeypatch):
    """Keep run_doctor hermetic: never probe this host's real systemd units.

    Class-4 (service health, #15) tests inject their own canned seam."""
    monkeypatch.setattr("culture_core.doctor.checks.list_services", lambda: [])


def _write_culture_yaml(directory: str, suffix: str) -> None:
    """Write a minimal culture.yaml into *directory*."""
    path = Path(directory) / "culture.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump({"suffix": suffix, "backend": "claude"}, f)


def test_spark_like_report_exit_nonzero(tmp_path):
    """A workspace with missing, unregistered, and colliding repos yields nonzero exit."""
    ws = tmp_path / "ws"
    ws.mkdir()

    # Valid registered repo
    culture_dir = ws / "culture"
    _write_culture_yaml(str(culture_dir), "culture")

    # Unregistered repo (class-2 only)
    guildmaster_dir = ws / "guildmaster"
    _write_culture_yaml(str(guildmaster_dir), "guildmaster")

    # Collision repo: suffix "culture" already in manifest at a different path
    # → class-2 (unregistered) AND class-3 (suffix collision)
    sonar_dir = ws / "sonar"
    _write_culture_yaml(str(sonar_dir), "culture")

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={
            "culture": str(culture_dir),
            "shushu": str(ws / "nope-shushu"),
            "agentpypi": str(ws / "nope-pypi"),
            "agexcli": str(ws / "nope-agex"),
        },
    )

    report = run_doctor(config, root_override=str(ws))

    assert len(report.class1) == 3
    assert len(report.class2) >= 1  # guildmaster + sonar
    assert len(report.class3) >= 1  # sonar's "culture" collides
    assert report.exit_code != 0


def test_clean_report_exit_zero(tmp_path):
    """A perfectly clean workspace produces an empty report with exit code 0."""
    ws2 = tmp_path / "ws2"
    ws2.mkdir()

    culture_dir = ws2 / "culture"
    _write_culture_yaml(str(culture_dir), "culture")

    config2 = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"culture": str(culture_dir)},
    )

    report = run_doctor(config2, root_override=str(ws2))

    assert report.class1 == []
    assert report.class2 == []
    assert report.class3 == []
    assert report.exit_code == 0
    assert report.ok is True


def test_run_doctor_writes_nothing(tmp_path):
    """run_doctor is diagnosis-only — it must never modify the config file."""
    server_yaml = tmp_path / "server.yaml"
    server_yaml.write_text("server:\n  name: spark\nagents: {}\n")
    original_bytes = server_yaml.read_bytes()

    ws = tmp_path / "ws"
    ws.mkdir()

    # One unregistered repo so class-2 is non-empty
    rogue_dir = ws / "rogue"
    _write_culture_yaml(str(rogue_dir), "rogue")

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={},
    )

    run_doctor(config, root_override=str(ws))

    assert server_yaml.read_bytes() == original_bytes


def test_run_doctor_includes_service_health(tmp_path, monkeypatch):
    """run_doctor folds class-4 service findings into the report (#15) —
    a parked (failed) unit makes the run fail via canned systemctl output."""
    ws = tmp_path / "ws"
    ws.mkdir()

    culture_dir = ws / "culture"
    _write_culture_yaml(str(culture_dir), "culture")

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"culture": str(culture_dir)},
    )

    monkeypatch.setattr("culture_core.doctor.checks.get_platform", lambda: "linux")
    monkeypatch.setattr(
        "culture_core.doctor.checks.shutil.which", lambda _cmd: "/usr/bin/systemctl"
    )
    monkeypatch.setattr(
        "culture_core.doctor.checks.list_services", lambda: ["culture-server-spark"]
    )
    monkeypatch.setattr(
        "culture_core.doctor.checks._systemctl_show",
        lambda unit: "ActiveState=failed\nNRestarts=2\nExecMainStatus=78\nResult=exit-code\n",
    )

    report = run_doctor(config, root_override=str(ws))

    assert report.class1 == []
    assert report.class2 == []
    assert report.class3 == []
    assert len(report.class4) == 1
    assert report.class4[0].subject == "culture-server-spark"
    assert report.ok is False
    assert report.exit_code == 1
