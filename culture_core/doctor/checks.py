"""Drift-class checks for the culture doctor."""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from yaml import YAMLError

from culture_core.config import load_culture_yaml
from culture_core.doctor.model import Finding
from culture_core.persistence import _systemd_user_dir, get_platform, list_services

# A unit that systemd restarted more than this many times is treated as
# restart-looping (class 4). Matches the "self-heal a transient blip, park
# a permanent failure" contract from #15.
SERVICE_RESTART_LOOP_THRESHOLD = 5

_SYSTEMCTL_TIMEOUT = 10.0


def check_registrations(config) -> list[Finding]:
    """Class 1: manifest entries that cannot be loaded."""
    findings: list[Finding] = []
    for suffix, directory in config.manifest.items():
        nick = f"{config.server.name}-{suffix}"
        try:
            load_culture_yaml(directory, suffix=suffix)
        except FileNotFoundError:
            findings.append(
                Finding(
                    1,
                    "error",
                    nick,
                    directory,
                    f"culture.yaml missing for {nick} at {directory}",
                    f"culture agents unregister {suffix}",
                )
            )
        except (ValueError, YAMLError) as e:
            # Malformed/unloadable culture.yaml is itself a broken registration.
            findings.append(
                Finding(
                    1,
                    "error",
                    nick,
                    directory,
                    f"{nick}: {e}",
                    f"culture agents unregister {suffix}",
                )
            )
    return findings


def check_unregistered(config, discovered) -> list[Finding]:
    """Class 2: on-disk repos not registered in the manifest."""
    manifest_dirs = {str(Path(d).resolve()) for d in config.manifest.values()}
    findings: list[Finding] = []
    for repo in discovered:
        if str(Path(repo.directory).resolve()) not in manifest_dirs:
            findings.append(
                Finding(
                    2,
                    "warning",
                    Path(repo.directory).name,
                    repo.directory,
                    f"on-disk culture.yaml not registered: {repo.directory}",
                    f"culture agents register {repo.directory}",
                )
            )
    return findings


def _manifest_collisions(config, discovered) -> list[Finding]:
    """A discovered suffix already bound to a DIFFERENT path in the manifest."""
    findings: list[Finding] = []
    for repo in discovered:
        for s in repo.suffixes:
            if s in config.manifest and str(Path(config.manifest[s]).resolve()) != str(
                Path(repo.directory).resolve()
            ):
                findings.append(
                    Finding(
                        3,
                        "error",
                        s,
                        repo.directory,
                        f"suffix '{s}' at {repo.directory} collides with registered {config.manifest[s]}",
                        "",
                    )
                )
    return findings


def _discovered_duplicates(config, discovered) -> list[Finding]:
    """The same suffix declared by two discovered repos. Suffixes already bound
    in the manifest are :func:`_manifest_collisions`' responsibility — skip them
    here so a manifest collision isn't double-reported."""
    findings: list[Finding] = []
    seen: dict[str, str] = {}
    for repo in discovered:
        for s in repo.suffixes:
            if s in config.manifest:
                continue
            resolved = str(Path(repo.directory).resolve())
            if s in seen and seen[s] != resolved:
                findings.append(
                    Finding(
                        3,
                        "error",
                        s,
                        repo.directory,
                        f"suffix '{s}' declared by both {seen[s]} and {repo.directory}",
                        "",
                    )
                )
            else:
                seen[s] = resolved
    return findings


def check_suffix_collisions(config, discovered) -> list[Finding]:
    """Class 3: suffix collisions between manifest and discovered repos."""
    return _manifest_collisions(config, discovered) + _discovered_duplicates(config, discovered)


def _systemctl_show(unit: str) -> str | None:
    """Return ``systemctl --user show`` health properties for *unit*.

    Returns the raw stdout, or None when the query cannot run (systemctl
    errored or timed out — e.g. no user bus on CI runners).
    """
    try:
        proc = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                unit,
                "--property=ActiveState,NRestarts,ExecMainStatus,Result",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=_SYSTEMCTL_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _parse_systemctl_show(output: str) -> dict[str, str]:
    """Parse ``Key=Value`` lines from ``systemctl show`` output."""
    props: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            props[key.strip()] = value.strip()
    return props


def _service_findings(name: str, props: dict[str, str]) -> list[Finding]:
    """Class-4 findings for one service from its systemd health properties."""
    # Lazy import: culture_core.cli.doctor imports this package, so a
    # module-level import of culture_core.cli._errors would be circular.
    from culture_core.cli._errors import EXIT_DAEMON_PERMANENT

    unit = f"{name}.service"
    unit_path = str(_systemd_user_dir() / unit)

    if props.get("ActiveState") == "failed":
        exec_status = props.get("ExecMainStatus", "?")
        parked = (
            " — the daemon reported a permanent error, systemd parked the unit"
            if exec_status == str(EXIT_DAEMON_PERMANENT)
            else ""
        )
        return [
            Finding(
                4,
                "error",
                name,
                unit_path,
                f"{unit} is failed (ExecMainStatus={exec_status}, "
                f"Result={props.get('Result', '?')}){parked}",
                f"journalctl --user -u {unit} — fix the config, then: "
                f"systemctl --user reset-failed {unit} && systemctl --user restart {unit}",
            )
        ]

    try:
        n_restarts = int(props.get("NRestarts", "0"))
    except ValueError:
        n_restarts = 0
    if n_restarts > SERVICE_RESTART_LOOP_THRESHOLD:
        return [
            Finding(
                4,
                "warning",
                name,
                unit_path,
                f"{unit} restarted {n_restarts} times — likely restart-looping",
                f"journalctl --user -u {unit} — if the failure is permanent, the daemon "
                f"should exit {EXIT_DAEMON_PERMANENT} so systemd parks it instead of looping",
            )
        ]
    return []


def check_services(
    services: list[str] | None = None,
    run_systemctl: Callable[[str], str | None] | None = None,
) -> list[Finding]:
    """Class 4: installed culture services that are parked or restart-looping.

    *run_systemctl* is the injectable command-runner seam — tests feed
    canned ``systemctl --user show`` output without systemd. With the
    default (real) runner the check passes gracefully on non-Linux
    platforms and when systemctl is absent.
    """
    if run_systemctl is None:
        if get_platform() != "linux" or shutil.which("systemctl") is None:
            return []
        run_systemctl = _systemctl_show
    if services is None:
        services = list_services()

    findings: list[Finding] = []
    for name in services:
        output = run_systemctl(f"{name}.service")
        if output is None:
            continue
        findings.extend(_service_findings(name, _parse_systemctl_show(output)))
    return findings
