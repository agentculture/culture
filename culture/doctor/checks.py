"""Drift-class checks for the culture doctor."""

from __future__ import annotations

from pathlib import Path

from yaml import YAMLError

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
