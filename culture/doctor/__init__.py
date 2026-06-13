"""Culture doctor — run_doctor orchestrator."""

from __future__ import annotations

from culture.config import ServerConfig
from culture.doctor.checks import (
    check_registrations,
    check_suffix_collisions,
    check_unregistered,
)
from culture.doctor.discovery import discover_ondisk_repos, resolve_scan_root
from culture.doctor.fix import register_unregistered
from culture.doctor.model import DoctorReport, Finding

__all__ = ["run_doctor", "DoctorReport", "Finding"]


def run_doctor(
    config: ServerConfig,
    root_override: str | None = None,
    fix: bool = False,
    config_path: str | None = None,
    cwd: str | None = None,
) -> DoctorReport:
    """Run the full culture doctor diagnostic pass."""
    report = DoctorReport()

    report.class1 = check_registrations(config)

    root = resolve_scan_root(config, cwd=cwd, override=root_override)
    discovered = discover_ondisk_repos(root)

    report.class2 = check_unregistered(config, discovered)
    report.class3 = check_suffix_collisions(config, discovered)

    if fix and config_path is not None:
        register_unregistered(config_path, report.class2)

    return report
