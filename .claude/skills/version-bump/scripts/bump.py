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
an empty stub is inserted. Otherwise stdin is read until it closes. Any pause
longer than BUMP_STDIN_IDLE_SECONDS (default 5), including before the first
byte, stops the bump with an error; raise it for a slow producer. Stdin that
isn't valid JSON in the shape above (an object whose keys are added / changed /
fixed, each a list of strings) is an error too. Every check runs before any
file is written, so a failed bump changes nothing.

The new section goes above the first ``## [`` release heading, ignoring
headings inside fenced code blocks. A leading ``## [Unreleased]`` section stays
on top, untouched. A CHANGELOG with no ``##`` heading yet gets the section at
the end, or after an Unreleased-only section; one whose ``##`` headings aren't
``## [...]`` release headings (e.g. ``## 1.2.3``) is an error.
"""

import argparse
import json
import math
import os
import queue
import re
import sys
import threading
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
SECTIONS = ("added", "changed", "fixed")


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


def _fail(message: str) -> None:
    print(f"ERROR: {message}; nothing was changed.", file=sys.stderr)
    sys.exit(1)


def _stdin_encoding() -> str:
    return getattr(sys.stdin, "encoding", None) or "utf-8"


def _pump_stdin(out: queue.Queue) -> None:
    """Thread body: put stdin's chunks (b"" at EOF) or the read error on ``out``.

    Reads the raw fd with os.read, not the buffered sys.stdin, so a thread
    still blocked in a read at exit can't hold the stdin buffer's lock during
    interpreter shutdown.
    """
    try:
        fd = sys.stdin.fileno()
    except (OSError, ValueError):  # a text-only stdin, e.g. io.StringIO
        encoding = _stdin_encoding()

        def read() -> bytes:
            return sys.stdin.read(65536).encode(encoding)

    else:

        def read() -> bytes:
            return os.read(fd, 65536)

    try:
        while True:
            chunk = read()
            out.put(chunk)
            if not chunk:
                return
    except (OSError, ValueError) as exc:
        out.put(exc)


def _read_stdin_until_eof(idle: float) -> bytes | None:
    """Read stdin to EOF; None if no new input arrives within ``idle`` s."""
    chunks: queue.Queue = queue.Queue()
    threading.Thread(target=_pump_stdin, args=(chunks,), daemon=True).start()
    data = []
    while True:
        try:
            chunk = chunks.get(timeout=idle)
        except queue.Empty:
            return None
        if isinstance(chunk, Exception):
            _fail(f"could not read stdin ({chunk})")
        if not chunk:
            return b"".join(data)
        data.append(chunk)


def _parse_entries(raw: bytes) -> dict:
    """Decode and validate changelog JSON; an error rather than a dropped entry."""
    try:
        text = raw.decode(_stdin_encoding()).strip()
    except (UnicodeDecodeError, LookupError) as exc:
        _fail(f"could not decode stdin as {_stdin_encoding()} ({exc})")
    if not text:
        return {}
    try:
        entries = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"stdin is not valid changelog JSON ({exc})")
    if not isinstance(entries, dict):
        _fail('changelog JSON must be an object like {"fixed": ["..."]}')
    unknown = sorted(set(entries) - set(SECTIONS))
    if unknown:
        _fail(
            f"unknown changelog key(s) {', '.join(map(repr, unknown))}; use {', '.join(SECTIONS)}"
        )
    for key, items in entries.items():
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            _fail(f"changelog key {key!r} must be a list of strings")
    return entries


def read_changelog_entries(no_stdin: bool = False) -> dict:
    """Read changelog entries from stdin as JSON."""
    if no_stdin or sys.stdin is None or sys.stdin.isatty():
        return {}
    idle = _stdin_idle_seconds()
    raw = _read_stdin_until_eof(idle)
    if raw is None:
        _fail(
            f"stdin stayed open with no input for {idle:g}s and never closed. Pipe the "
            "changelog JSON (echo '{...}' | bump.py patch), raise BUMP_STDIN_IDLE_SECONDS "
            "for a slow producer, or pass --no-stdin (or set BUMP_NO_STDIN=1) for an "
            "empty stub"
        )
    return _parse_entries(raw)


def format_changelog_section(new: str, entries: dict) -> str:
    """Format a changelog section from entries dict.

    Output uses single blank lines between elements (markdownlint MD012
    compliant). Trailing blank line before the next existing entry is
    included.
    """
    today = date.today().isoformat()
    chunks = [f"## [{new}] - {today}\n"]

    for section in SECTIONS:
        items = entries.get(section, [])
        if items:
            chunks.append(f"\n### {section.capitalize()}\n\n")
            chunks.extend(f"- {item}\n" for item in items)

    # If no entries at all, emit empty section stubs.
    if not any(entries.get(s) for s in SECTIONS):
        chunks.append("\n### Added\n\n### Changed\n\n### Fixed\n")

    chunks.append("\n")  # blank line before the next existing entry
    return "".join(chunks)


_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _section_headings(lines: list[str]) -> list[tuple[int, str]]:
    """``## `` headings outside fenced code blocks, as (line index, line).

    A fence opens with 3+ backticks or tildes (a backtick fence's info string
    can't contain a backtick, so ```` ```x``` text ```` is inline code, not a
    fence) and closes only on a line of the same character, at least as long,
    with nothing after it.
    """
    headings, fence = [], None
    for i, line in enumerate(lines):
        match = _FENCE.match(line.rstrip("\r\n"))
        if fence:
            marker, info = match.groups() if match else ("", "x")
            if marker[:1] == fence[0] and len(marker) >= len(fence) and not info.strip():
                fence = None
        elif match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
            fence = match.group(1)
        elif line.startswith("## "):
            headings.append((i, line))
    return headings


def _insertion_index(lines: list[str]) -> int | None:
    """Line index for the new section: above the first ``## [`` release heading
    that isn't Unreleased, so a leading Unreleased section stays on top; after
    an Unreleased-only section, or at the end of a file with no ``##`` heading.
    None for ``##`` headings this script doesn't recognise (e.g. ``## 1.2.3``)."""
    headings = _section_headings(lines)
    unreleased = re.compile(r"^## \[Unreleased\]", re.IGNORECASE)
    releases = [i for i, line in headings if line.startswith("## [") and not unreleased.match(line)]
    if releases:
        return releases[0]
    after = [i for i, line in headings if unreleased.match(line)]
    if after:
        return next((i for i, _ in headings if i > after[0]), len(lines))
    return None if headings else len(lines)


def plan_changelog(project_root: Path, new: str, entries: dict) -> tuple[Path, str] | None:
    """The CHANGELOG path and its new text, without writing; None if there is none."""
    changelog = project_root / "CHANGELOG.md"
    if not changelog.exists():
        return None
    lines = changelog.read_text().splitlines(keepends=True)
    idx = _insertion_index(lines)
    if idx is None:
        _fail(
            "CHANGELOG.md has ## headings but no '## [x.y.z]' release heading to insert "
            "above (Keep a Changelog format)"
        )
    before, after = "".join(lines[:idx]), "".join(lines[idx:])
    new_entry = format_changelog_section(new, entries)
    if not after:  # appending at the end of the file
        before = before.rstrip("\n") + "\n\n" if before.strip() else ""
        new_entry = new_entry.rstrip("\n") + "\n"
    return changelog, before + new_entry + after


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bump.py",
        description=__doc__,
        allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("part", type=str.lower, choices=("major", "minor", "patch", "show"))
    parser.add_argument(
        "--no-stdin", action="store_true", help="don't read stdin; insert the empty stub"
    )
    if not argv:
        parser.print_help()
        sys.exit(0)
    return parser.parse_args(argv)


def main():
    args = _parse_args(sys.argv[1:])
    path = find_pyproject()
    current = read_version(path)

    if args.part == "show":
        print(current)
        sys.exit(0)

    # Every check runs before any write, so a failed bump changes nothing.
    new = bump(current, args.part)
    entries = read_changelog_entries(args.no_stdin or _env_flag("BUMP_NO_STDIN"))
    changelog = plan_changelog(path.parent, new, entries)

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

    if changelog is None:
        print("No CHANGELOG.md found — skipping")
    else:
        changelog[0].write_text(changelog[1])
        print(f"Updated CHANGELOG.md with [{new}]")

    print(f"{current} -> {new}")


if __name__ == "__main__":
    main()
