"""Tests for culture_core/doctor/checks.py — the three drift-class checks."""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

import yaml

import culture_core.doctor.checks as checks_mod
from culture_core.config import ServerConfig, ServerConnConfig
from culture_core.doctor.checks import (
    SERVICE_RESTART_LOOP_THRESHOLD,
    check_registrations,
    check_services,
    check_suffix_collisions,
    check_unregistered,
)
from culture_core.doctor.discovery import RepoOnDisk
from culture_core.doctor.model import Finding


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


def test_class1_malformed_yaml_is_finding_not_crash(tmp_path):
    """A registered repo with a malformed culture.yaml is a class-1 finding,
    not an uncaught YAMLError crash (Qodo review)."""
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "culture.yaml").write_text("suffix: x\nbroken: [1, 2,\n")  # unterminated flow

    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"x": str(bad_dir)},
    )

    findings = check_registrations(config)  # must not raise
    assert len(findings) == 1
    assert findings[0].drift_class == 1


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
    import culture_core.config as cfg

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


def test_class3_manifest_collision_not_double_reported():
    """A manifest collision is reported once, even when the registered repo
    is also present on disk (the duplicate-across-discovered check must defer
    to the manifest-collision check)."""
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={"daria": "/path/daria"},
    )
    discovered = [
        RepoOnDisk("/path/daria", ["daria"]),  # the registered repo, on disk
        RepoOnDisk("/other/sonar", ["daria"]),  # collides with the manifest
    ]

    findings = check_suffix_collisions(config, discovered)
    assert len(findings) == 1
    assert findings[0].subject == "daria"
    assert "/other/sonar" in findings[0].path


def test_class3_pure_ondisk_duplicate_flagged():
    """Two unregistered repos sharing a suffix (not in the manifest) collide."""
    config = ServerConfig(
        server=ServerConnConfig(name="spark"),
        manifest={},
    )
    discovered = [
        RepoOnDisk("/x/one", ["dup"]),
        RepoOnDisk("/y/two", ["dup"]),
    ]

    findings = check_suffix_collisions(config, discovered)
    assert len(findings) == 1
    assert findings[0].drift_class == 3


# ---------------------------------------------------------------------------
# Class 4 — service health (#15)
# ---------------------------------------------------------------------------


def _canned_systemctl(payload: dict[str, str]):
    """Injectable run_systemctl seam feeding canned `systemctl show` output."""

    def _run(unit: str) -> str | None:
        return payload.get(unit)

    return _run


_PARKED_78 = "ActiveState=failed\nNRestarts=3\nExecMainStatus=78\nResult=exit-code\n"
_PARKED_OTHER = "ActiveState=failed\nNRestarts=3\nExecMainStatus=1\nResult=exit-code\n"
_LOOPING = "ActiveState=activating\nNRestarts=12\nExecMainStatus=1\nResult=exit-code\n"
_HEALTHY = "ActiveState=active\nNRestarts=0\nExecMainStatus=0\nResult=success\n"


def test_class4_parked_unit_flagged_with_remediation():
    """ActiveState=failed is a class-4 error with reset-failed remediation;
    ExecMainStatus=78 is called out as the permanent-error contract (#15)."""
    findings = check_services(
        services=["culture-server-spark"],
        run_systemctl=_canned_systemctl({"culture-server-spark.service": _PARKED_78}),
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.drift_class == 4
    assert f.severity == "error"
    assert "failed" in f.message
    assert "permanent error" in f.message
    assert "journalctl --user -u culture-server-spark.service" in f.fix_hint
    assert "systemctl --user reset-failed culture-server-spark.service" in f.fix_hint


def test_class4_parked_unit_without_contract_code_still_error():
    """A unit parked with a non-contract exit code is still an error, just
    without the permanent-error callout."""
    findings = check_services(
        services=["culture-server-spark"],
        run_systemctl=_canned_systemctl({"culture-server-spark.service": _PARKED_OTHER}),
    )
    assert len(findings) == 1
    assert findings[0].severity == "error"
    assert "permanent error" not in findings[0].message
    assert findings[0].fix_hint != ""


def test_class4_restart_looping_unit_flagged():
    """NRestarts above the threshold is a class-4 warning pointing at logs
    and at the exit-78 contract."""
    findings = check_services(
        services=["culture-agent-spark-ada"],
        run_systemctl=_canned_systemctl({"culture-agent-spark-ada.service": _LOOPING}),
    )
    assert len(findings) == 1
    f = findings[0]
    assert f.drift_class == 4
    assert f.severity == "warning"
    assert "restart-looping" in f.message
    assert "journalctl --user -u culture-agent-spark-ada.service" in f.fix_hint
    assert "78" in f.fix_hint


def test_class4_healthy_unit_produces_no_findings():
    findings = check_services(
        services=["culture-server-spark"],
        run_systemctl=_canned_systemctl({"culture-server-spark.service": _HEALTHY}),
    )
    assert findings == []


def test_class4_nrestarts_at_threshold_not_flagged():
    """The threshold is exclusive: exactly SERVICE_RESTART_LOOP_THRESHOLD
    restarts does not flag."""
    output = (
        f"ActiveState=active\nNRestarts={SERVICE_RESTART_LOOP_THRESHOLD}\n"
        f"ExecMainStatus=0\nResult=success\n"
    )
    findings = check_services(
        services=["culture-server-spark"],
        run_systemctl=_canned_systemctl({"culture-server-spark.service": output}),
    )
    assert findings == []


def test_class4_unavailable_unit_output_skipped():
    """A runner that can't answer (returns None) is skipped, not a crash."""
    findings = check_services(
        services=["culture-server-spark"],
        run_systemctl=_canned_systemctl({}),
    )
    assert findings == []


def test_class4_non_linux_passes(monkeypatch):
    """On non-Linux the real seam is never engaged — the check passes."""
    monkeypatch.setattr("culture_core.doctor.checks.get_platform", lambda: "macos")
    assert check_services() == []


def test_class4_malformed_nrestarts_treated_as_zero():
    """Unparseable NRestarts must not crash the check — treated as 0."""
    output = "ActiveState=active\nNRestarts=garbage\nExecMainStatus=0\nResult=success\n"
    findings = check_services(
        services=["culture-server-spark"],
        run_systemctl=_canned_systemctl({"culture-server-spark.service": output}),
    )
    assert findings == []


def test_class4_missing_systemctl_passes(monkeypatch):
    """Linux without systemctl on PATH also passes gracefully."""
    monkeypatch.setattr("culture_core.doctor.checks.get_platform", lambda: "linux")
    monkeypatch.setattr("culture_core.doctor.checks.shutil.which", lambda _cmd: None)
    assert check_services() == []


class TestSystemctlShowRunner:
    """The real `systemctl --user show` seam degrades to None, never raises."""

    def test_success_returns_stdout(self, monkeypatch):
        completed = subprocess.CompletedProcess(
            args=["systemctl"], returncode=0, stdout=_HEALTHY, stderr=""
        )
        monkeypatch.setattr("culture_core.doctor.checks.subprocess.run", lambda *a, **kw: completed)
        assert checks_mod._systemctl_show("culture-server-spark.service") == _HEALTHY

    def test_nonzero_exit_returns_none(self, monkeypatch):
        """No user bus (CI runners) → systemctl fails → skip gracefully."""
        completed = subprocess.CompletedProcess(
            args=["systemctl"], returncode=1, stdout="", stderr="Failed to connect to bus"
        )
        monkeypatch.setattr("culture_core.doctor.checks.subprocess.run", lambda *a, **kw: completed)
        assert checks_mod._systemctl_show("culture-server-spark.service") is None

    def test_timeout_returns_none(self, monkeypatch):
        def _raise(*_a, **kw):
            raise subprocess.TimeoutExpired(cmd="systemctl", timeout=kw.get("timeout", 10))

        monkeypatch.setattr("culture_core.doctor.checks.subprocess.run", _raise)
        assert checks_mod._systemctl_show("culture-server-spark.service") is None

    def test_oserror_returns_none(self, monkeypatch):
        def _raise(*_a, **_kw):
            raise OSError("exec failed")

        monkeypatch.setattr("culture_core.doctor.checks.subprocess.run", _raise)
        assert checks_mod._systemctl_show("culture-server-spark.service") is None
