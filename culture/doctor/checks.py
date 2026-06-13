"""Drift-class checks for the culture doctor."""

from __future__ import annotations

from pathlib import Path

from culture.config import load_culture_yaml
from culture.doctor.model import Finding


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
        except ValueError as e:
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


def check_suffix_collisions(config, discovered) -> list[Finding]:
    """Class 3: suffix collisions between manifest and discovered repos."""
    findings: list[Finding] = []

    # (a) a discovered suffix already bound to a DIFFERENT path in the manifest
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

    # (b) duplicate suffix across two discovered repos
    seen: dict[str, str] = {}
    for repo in discovered:
        for s in repo.suffixes:
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
