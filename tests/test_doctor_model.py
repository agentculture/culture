"""Tests for doctor result model."""

from culture_core.doctor.model import DoctorReport, Finding


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


def test_class4_error_finding_makes_exit_code_nonzero():
    """A parked (failed) service is an error — the run must fail (#15)."""
    finding = Finding(
        drift_class=4,
        severity="error",
        subject="culture-server-spark",
        path="/units/culture-server-spark.service",
        message="unit is failed",
        fix_hint="systemctl --user reset-failed culture-server-spark.service",
    )
    report = DoctorReport(class4=[finding])
    assert report.ok is False
    assert report.exit_code != 0


def test_class4_warning_only_report_is_ok_but_not_clean():
    """A restart-looping service is a warning — surfaced, but exit stays 0."""
    finding = Finding(
        drift_class=4,
        severity="warning",
        subject="culture-server-spark",
        path="/units/culture-server-spark.service",
        message="unit restarted 12 times",
        fix_hint="journalctl --user -u culture-server-spark.service",
    )
    report = DoctorReport(class4=[finding])
    assert report.ok is False
    assert report.exit_code == 0
