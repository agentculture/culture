"""Tests for culture/doctor/discovery.py."""

from __future__ import annotations

import types
from pathlib import Path

import yaml

from culture.doctor.discovery import discover_ondisk_repos, resolve_scan_root


def _write_culture_yaml(directory: str, suffix: str) -> None:
    """Write a minimal culture.yaml into *directory*."""
    path = Path(directory) / "culture.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump({"suffix": suffix, "backend": "claude"}, f)


def test_resolve_scan_root_tracks_repo_parent(tmp_path):
    """resolve_scan_root returns the parent of the git root."""
    # First repo: .git lives inside the culture checkout, so culture IS the
    # repo and its parent (the workspace) is the scan root.
    culture_dir = tmp_path / "git" / "culture"
    culture_dir.mkdir(parents=True)
    (culture_dir / ".git").mkdir()

    config = types.SimpleNamespace(manifest={})
    result = resolve_scan_root(config, cwd=str(culture_dir))
    assert result == (tmp_path / "git").resolve()

    # Second repo: relocating the checkout to git2/ moves the scan root with it.
    culture_dir2 = tmp_path / "git2" / "culture"
    culture_dir2.mkdir(parents=True)
    (culture_dir2 / ".git").mkdir()

    result2 = resolve_scan_root(config, cwd=str(culture_dir2))
    assert result2 == (tmp_path / "git2").resolve()


def test_resolve_scan_root_override(tmp_path):
    """An override bypasses all discovery logic."""
    config = types.SimpleNamespace(manifest={})
    result = resolve_scan_root(config, override="/tmp/x")
    assert result == Path("/tmp/x").resolve()


def test_discover_finds_only_culture_yaml_repos(tmp_path):
    """discover_ondisk_repos returns only dirs with a culture.yaml."""
    ws = tmp_path / "ws"
    ws.mkdir()

    for suffix in ("a", "b", "c"):
        d = ws / f"repo-{suffix}"
        d.mkdir()
        _write_culture_yaml(str(d), suffix)

    (ws / "plain").mkdir()

    results = discover_ondisk_repos(str(ws))
    assert len(results) == 3

    dirs = {r.directory for r in results}
    expected = {str((ws / f"repo-{s}").resolve()) for s in ("a", "b", "c")}
    assert dirs == expected
