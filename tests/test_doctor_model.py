"""Tests for doctor result model."""

from culture.doctor.model import DoctorReport, Finding


def test_empty_report_is_ok():
    report = DoctorReport()
    assert report.ok is True
    assert report.exit_code == 0


def test_class2_only_report_is_ok_but_not_clean():
    finding = Finding(
        drift_class=2,
        severity="warning",
        subject="some-agent",
        path="/some/path",
        message="Unregistered repo",
        fix_hint="culture agents register /some/path",
    )
    report = DoctorReport(class2=[finding])
    assert report.ok is False
    assert report.exit_code == 0


def test_class1_finding_makes_exit_code_nonzero():
    finding = Finding(
        drift_class=1,
        severity="error",
        subject="broken-agent",
        path="/missing/path",
        message="Missing directory",
        fix_hint="culture agents unregister broken-agent",
    )
    report = DoctorReport(class1=[finding])
    assert report.ok is False
    assert report.exit_code != 0


def test_class3_finding_makes_exit_code_nonzero():
    finding = Finding(
        drift_class=3,
        severity="error",
        subject="colliding-agent",
        path="/colliding/path",
        message="Suffix collision",
        fix_hint="",
    )
    report = DoctorReport(class3=[finding])
    assert report.ok is False
    assert report.exit_code != 0
