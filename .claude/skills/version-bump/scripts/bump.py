#!/usr/bin/env python3
"""Bump the version in pyproject.toml and CHANGELOG.md.

If the package's ``__init__.py`` contains a literal ``__version__ = "..."``
assignment, the script also rewrites that value. In repos that read
``__version__`` from package metadata (the steward-cli convention), the
``__init__.py`` step is a no-op.

Usage:
    bump.py major    # 0.1.0 -> 1.0.0
    bump.py minor    # 0.1.0 -> 0.2.0
    bump.py patch    # 0.1.0 -> 0.1.1
    bump.py show     # print current version

Changelog entries are passed via stdin as a JSON object:
    {
      "added": ["New CLI command", "Observer module"],
      "changed": ["Restructured namespace"],
      "fixed": ["WHO reply index bug"]
    }

If no stdin is provided, an empty stub is inserted.
"""

import json
import re
import sys
from datetime import date
from pathlib import Path


def find_pyproject() -> Path:
    """Walk up from cwd to find pyproject.toml."""
    current = Path.cwd()
    while current != current.parent:
        candidate = current / "pyproject.toml"
        if candidate.exists():
            return candidate
        current = current.parent
    print("ERROR: pyproject.toml not found", file=sys.stderr)
    sys.exit(1)


def read_version(path: Path) -> str:
    """Extract version string from pyproject.toml."""
    text = path.read_text()
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        print("ERROR: version field not found in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return match.group(1)


def bump(version: str, part: str) -> str:
    """Bump the specified part of a semver version."""
    parts = version.split(".")
    if len(parts) != 3:
        print(f"ERROR: version '{version}' is not semver (x.y.z)", file=sys.stderr)
        sys.exit(1)

    major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])

    if part == "major":
        return f"{major + 1}.0.0"
    elif part == "minor":
        return f"{major}.{minor + 1}.0"
    elif part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    else:
        print(f"ERROR: unknown bump type '{part}' (use major, minor, or patch)", file=sys.stderr)
        sys.exit(1)


def write_version(path: Path, old: str, new: str) -> None:
    """Replace old version with new in pyproject.toml."""
    text = path.read_text()
    updated = text.replace(f'version = "{old}"', f'version = "{new}"', 1)
    path.write_text(updated)


def read_changelog_entries() -> dict:
    """Read changelog entries from stdin as JSON."""
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read().strip()
        if not raw:
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print(
            "WARNING: could not parse changelog JSON from stdin, using empty stub", file=sys.stderr
        )
        return {}


def format_changelog_section(new: str, entries: dict) -> str:
    """Format a changelog section from entries dict.

    Output uses single blank lines between elements (markdownlint MD012
    compliant). Trailing blank line before the next existing entry is
    included.
    """
    today = date.today().isoformat()
    chunks = [f"## [{new}] - {today}\n"]

    for section in ("added", "changed", "fixed"):
        items = entries.get(section, [])
        if items:
            chunks.append(f"\n### {section.capitalize()}\n\n")
            chunks.extend(f"- {item}\n" for item in items)

    # If no entries at all, emit empty section stubs.
    if not any(entries.get(s) for s in ("added", "changed", "fixed")):
        chunks.append("\n### Added\n\n### Changed\n\n### Fixed\n")

    chunks.append("\n")  # blank line before the next existing entry
    return "".join(chunks)


def update_changelog(project_root: Path, new: str, entries: dict) -> None:
    """Insert a new changelog entry into CHANGELOG.md."""
    changelog = project_root / "CHANGELOG.md"
    if not changelog.exists():
        print("No CHANGELOG.md found — skipping")
        return

    text = changelog.read_text()
    new_entry = format_changelog_section(new, entries)

    marker = "## ["
    idx = text.find(marker)
    if idx >= 0:
        changelog.write_text(text[:idx] + new_entry + text[idx:])
        print(f"Updated CHANGELOG.md with [{new}]")
    else:
        print("WARNING: could not find insertion point in CHANGELOG.md", file=sys.stderr)


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    part = sys.argv[1].lower()
    path = find_pyproject()
    current = read_version(path)

    if part == "show":
        print(current)
        sys.exit(0)

    # Read changelog entries before bumping
    entries = read_changelog_entries()

    new = bump(current, part)
    write_version(path, current, new)

    # Also update __init__.py if it has __version__
    init_candidates = [
        path.parent / path.parent.name / "__init__.py",
    ]
    for init in init_candidates:
        if init.exists():
            init_text = init.read_text()
            if f'__version__ = "{current}"' in init_text:
                init.write_text(
                    init_text.replace(
                        f'__version__ = "{current}"',
                        f'__version__ = "{new}"',
                    )
                )
                print(f"Updated {init.relative_to(path.parent)}")

    # Update changelog
    update_changelog(path.parent, new, entries)

    print(f"{current} -> {new}")


if __name__ == "__main__":
    main()
