"""Culture doctor — run_doctor orchestrator."""

from __future__ import annotations

from culture_core.config import ServerConfig
from culture_core.doctor.accounts import check_accounts
from culture_core.doctor.checks import (
    check_registrations,
    check_services,
    check_suffix_collisions,
    check_unregistered,
)
from culture_core.doctor.discovery import discover_ondisk_repos, resolve_scan_root
from culture_core.doctor.model import DoctorReport, Finding

__all__ = ["run_doctor", "DoctorReport", "Finding"]


def _server_data_dir(config: ServerConfig, data_dir: str | None):
    """The server's own data dir (M5); None for a --no-persist server."""
    from culture_core import accounts

    return accounts.resolve_server_data_dir(config.server.name, data_dir).path


def run_doctor(
    config: ServerConfig,
    root_override: str | None = None,
    cwd: str | None = None,
    config_path: str | None = None,
    data_dir: str | None = None,
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
    resolved = _server_data_dir(config, data_dir)
    # A --no-persist server reads no accounts file: nothing to check.
    report.class5 = (
        check_accounts(config, config_path=config_path, data_dir=resolved) if resolved else []
    )

    return report
