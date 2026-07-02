"""Culture doctor — run_doctor orchestrator."""

from __future__ import annotations

from culture_core.config import ServerConfig
from culture_core.doctor.checks import (
    check_registrations,
    check_services,
    check_suffix_collisions,
    check_unregistered,
)
from culture_core.doctor.discovery import discover_ondisk_repos, resolve_scan_root
from culture_core.doctor.model import DoctorReport, Finding

__all__ = ["run_doctor", "DoctorReport", "Finding"]


def run_doctor(
    config: ServerConfig,
    root_override: str | None = None,
    cwd: str | None = None,
) -> DoctorReport:
    """Run the full culture doctor diagnostic pass (read-only).

    Diagnosis only — applying the opt-in fix is the caller's job (the CLI calls
    :func:`culture_core.doctor.fix.register_unregistered` on ``report.class2``), so
    the registered-pairs list is surfaced to the operator.
    """
    report = DoctorReport()

    report.class1 = check_registrations(config)

    root = resolve_scan_root(config, cwd=cwd, override=root_override)
    discovered = discover_ondisk_repos(root)

    report.class2 = check_unregistered(config, discovered)
    report.class3 = check_suffix_collisions(config, discovered)
    report.class4 = check_services()

    return report
