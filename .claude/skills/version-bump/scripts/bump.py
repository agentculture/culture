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
    bump.py patch --no-stdin   # don't read stdin; insert the empty stub

Changelog entries are passed via stdin as a JSON object:
    {
      "added": ["New CLI command", "Observer module"],
      "changed": ["Restructured namespace"],
      "fixed": ["WHO reply index bug"]
    }

If stdin is a terminal or empty, or --no-stdin (or BUMP_NO_STDIN=1) is given,
an empty stub is inserted. Stdin is read until it closes; if it stays open
with no new input for BUMP_STDIN_IDLE_SECONDS (default 5), the bump stops with
an error and changes nothing, rather than hanging or dropping entries.

The new section goes above the first ``## [`` release heading, ignoring
headings inside fenced code blocks. A leading ``## [Unreleased]`` section stays
on top, untouched; a CHANGELOG with no release heading yet gets the section
after its Unreleased section, or at the end.
"""

import json
import math
import os
import re
import select
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


STDIN_IDLE_SECONDS_DEFAULT = 5.0


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _stdin_idle_seconds() -> float:
    raw = os.environ.get("BUMP_STDIN_IDLE_SECONDS")
    if raw is None:
        return STDIN_IDLE_SECONDS_DEFAULT
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not math.isfinite(value) or value <= 0:
        print(
            f"ERROR: BUMP_STDIN_IDLE_SECONDS must be a positive number, got {raw!r}",
            file=sys.stderr,
        )
        sys.exit(1)
    return value


def _read_stdin_until_eof(idle: float) -> str | None:
    """Read stdin to EOF; None if it stays open with no new input for ``idle`` s."""
    fd = sys.stdin.fileno()
    chunks = []
    while True:
        try:
            ready, _, _ = select.select([fd], [], [], idle)
        except (OSError, ValueError):
            return sys.stdin.read()  # select() can't poll this stdin (e.g. Windows): read as before
        if not ready:
            return None
        chunk = os.read(fd, 65536)
        if not chunk:
            return b"".join(chunks).decode()
        chunks.append(chunk)


def read_changelog_entries(no_stdin: bool = False) -> dict:
    """Read changelog entries from stdin as JSON."""
    if no_stdin or sys.stdin is None or sys.stdin.isatty():
        return {}
    idle = _stdin_idle_seconds()
    raw = _read_stdin_until_eof(idle)
    if raw is None:
        print(
            f"ERROR: stdin stayed open with no input for {idle:g}s and never closed; nothing "
            "was changed. Pipe the changelog JSON (echo '{...}' | bump.py patch), or pass "
            "--no-stdin (or set BUMP_NO_STDIN=1) for an empty stub.",
            file=sys.stderr,
        )
        sys.exit(1)
    raw = raw.strip()
    if not raw:
        return {}
    try:
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


def _section_headings(lines: list[str]) -> list[tuple[int, str]]:
    """``## `` headings outside fenced code blocks, as (line index, line)."""
    headings, fenced = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith("## "):
            headings.append((i, line))
    return headings


def _insertion_index(lines: list[str]) -> int:
    """Line index for the new section: above the first ``## [`` release heading
    that isn't Unreleased, so a leading Unreleased section stays on top; after
    an Unreleased-only section, or at the end of a file with neither."""
    headings = _section_headings(lines)
    unreleased = re.compile(r"^## \[Unreleased\]", re.IGNORECASE)
    releases = [i for i, line in headings if line.startswith("## [") and not unreleased.match(line)]
    if releases:
        return releases[0]
    after = [i for i, line in headings if unreleased.match(line)]
    if after:
        return next((i for i, _ in headings if i > after[0]), len(lines))
    return len(lines)


def update_changelog(project_root: Path, new: str, entries: dict) -> None:
    """Insert a new changelog entry into CHANGELOG.md."""
    changelog = project_root / "CHANGELOG.md"
    if not changelog.exists():
        print("No CHANGELOG.md found — skipping")
        return

    lines = changelog.read_text().splitlines(keepends=True)
    idx = _insertion_index(lines)
    before, after = "".join(lines[:idx]), "".join(lines[idx:])
    new_entry = format_changelog_section(new, entries)
    if not after:  # appending at the end of the file
        before = before.rstrip("\n") + "\n\n" if before.strip() else ""
        new_entry = new_entry.rstrip("\n") + "\n"
    changelog.write_text(before + new_entry + after)
    print(f"Updated CHANGELOG.md with [{new}]")


def main():
    args = [a for a in sys.argv[1:] if a != "--no-stdin"]
    no_stdin = len(args) < len(sys.argv) - 1 or _env_flag("BUMP_NO_STDIN")
    if not args or args[0] in ("-h", "--help"):
        print(__doc__.strip())
        sys.exit(0)

    part = args[0].lower()
    path = find_pyproject()
    current = read_version(path)

    if part == "show":
        print(current)
        sys.exit(0)

    # Read changelog entries before bumping
    entries = read_changelog_entries(no_stdin)

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
