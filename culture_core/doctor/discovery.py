"""On-disk discovery helpers for the culture doctor."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from yaml import YAMLError

from culture_core.config import load_culture_yaml

logger = logging.getLogger("culture")


@dataclass
class RepoOnDisk:
    """A repo directory discovered on disk."""

    directory: str  # resolved absolute path
    suffixes: list[str]  # declared suffix(es) from its culture.yaml


def resolve_scan_root(config, cwd=None, override=None) -> Path:
    """Determine the root directory to scan.

    Priority:
    1. *override* if provided.
    2. Parent of the culture repo found via manifest or git walk.
    """
    if override is not None:
        return Path(override).resolve()

    repo_dir = _find_culture_repo(config, Path(cwd) if cwd else Path.cwd())
    return repo_dir.parent.resolve()


def _find_culture_repo(config, start: Path) -> Path:
    """Find the culture repo directory.

    PRIMARY: check manifest entries for a path that contains this module.
    FALLBACK: walk up from *start* until a ``.git`` entry is found.
    """
    this_file = Path(__file__).resolve()
    for d in config.manifest.values():
        rd = Path(d).resolve()
        if rd == this_file or rd in this_file.parents:
            return rd

    current = start.resolve()
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return start
        current = parent


def discover_ondisk_repos(root) -> list[RepoOnDisk]:
    """Discover repos under *root* that have a culture.yaml."""

    root = Path(root)
    results: list[RepoOnDisk] = []

    for child in sorted(p for p in root.iterdir() if p.is_dir()):
        try:
            agents = load_culture_yaml(str(child))
        except (OSError, ValueError, YAMLError) as exc:
            # No culture.yaml (the common case) or an unreadable/malformed one:
            # skip it, but don't swallow the reason entirely.
            logger.debug("skipping %s during doctor scan: %s", child, exc)
            continue
        results.append(
            RepoOnDisk(
                directory=str(child.resolve()),
                suffixes=[a.suffix for a in agents],
            )
        )

    return results
