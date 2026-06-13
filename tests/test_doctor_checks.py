"""Tests for culture/doctor/checks.py — the three drift-class checks."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from culture.config import ServerConfig, ServerConnConfig
from culture.doctor.checks import (
    check_registrations,
    check_suffix_collisions,
    check_unregistered,
)
from culture.doctor.discovery import RepoOnDisk
from culture.doctor.model import Finding


def _write_culture_yaml(directory: str, suffix: str) -> None:
    """Write a minimal culture.yaml into *directory*."""
    path = Path(directory) / "culture.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump({"suffix": suffix, "backend": "claude"}, f)


def test_class1_three_kinds(tmp_path):
    """check_registrations catches three kinds of broken manifest entries."""
    gone_dir = str(tmp_path / "gone")  # nonexistent dir
    noyaml_dir = str(tmp_path / "noyaml")
    Path(noyaml_dir).mkdir()  # exists but has no culture.yaml
    wrong_dir = str(tmp_path / "wrong")
    _write_culture_yaml(wrong_dir, "other")  # declares a DIFFERENT suffix

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={
            "gone": gone_dir,
            "noyaml": noyaml_dir,
            "wrong": wrong_dir,
        },
    )

    findings = check_registrations(config)
    assert len(findings) == 3
    assert all(f.drift_class == 1 for f in findings)
    assert all(f.severity == "error" for f in findings)


def test_class1_clean(tmp_path):
    """A well-formed manifest entry produces no findings."""
    good_dir = str(tmp_path / "good")
    _write_culture_yaml(good_dir, "good")

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"good": good_dir},
    )

    findings = check_registrations(config)
    assert findings == []


def test_class1_parity(tmp_path, caplog):
    """check_registrations findings match resolve_agents warnings for broken entries."""
    import culture.config as cfg

    gone_dir = str(tmp_path / "gone")
    noyaml_dir = str(tmp_path / "noyaml")
    Path(noyaml_dir).mkdir()
    wrong_dir = str(tmp_path / "wrong")
    _write_culture_yaml(wrong_dir, "other")

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={
            "gone": gone_dir,
            "noyaml": noyaml_dir,
            "wrong": wrong_dir,
        },
    )

    cfg.reset_manifest_warning_state()

    with caplog.at_level(logging.WARNING, logger="culture"):
        cfg.resolve_agents(config)

    # Extract suffixes from the "unregister SUFFIX" portion of each warning.
    warned_suffixes = {
        re.search(r"unregister (\w+)", record.getMessage()).group(1) for record in caplog.records
    }

    # check_registrations subjects are like "spark-gone", "spark-noyaml", "spark-wrong"
    finding_suffixes = {f.subject.split("-")[-1] for f in check_registrations(config)}

    # resolve_agents warns for both FileNotFoundError and ValueError cases,
    # so parity should hold across all three broken entries.
    assert warned_suffixes == finding_suffixes


def test_class2_warns_unregistered():
    """Unregistered on-disk repos produce class-2 warnings."""
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={},
    )
    discovered = [RepoOnDisk("/x/foo", ["foo"])]

    findings = check_unregistered(config, discovered)
    assert len(findings) == 1
    assert findings[0].drift_class == 2
    assert findings[0].severity == "warning"


def test_class2_skips_registered(tmp_path):
    """A discovered repo whose dir IS in the manifest produces no class-2 finding."""
    good_dir = str(tmp_path / "good")
    _write_culture_yaml(good_dir, "good")

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"good": good_dir},
    )
    discovered = [RepoOnDisk(good_dir, ["good"])]

    findings = check_unregistered(config, discovered)
    assert findings == []


def test_class3_collision_with_manifest():
    """A discovered suffix colliding with a manifest entry produces a class-3 error."""
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"daria": "/path/daria"},
    )
    discovered = [RepoOnDisk("/other/sonar", ["daria"])]

    findings = check_suffix_collisions(config, discovered)
    assert len(findings) == 1
    assert findings[0].drift_class == 3
    assert findings[0].severity == "error"


def test_class3_no_false_positive():
    """Discovered repos with unique suffixes not in manifest produce no findings."""
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"alpha": "/a/alpha"},
    )
    discovered = [
        RepoOnDisk("/b/beta", ["beta"]),
        RepoOnDisk("/c/gamma", ["gamma"]),
    ]

    findings = check_suffix_collisions(config, discovered)
    assert findings == []
