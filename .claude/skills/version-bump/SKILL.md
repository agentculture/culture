---
name: version-bump
description: >
  Bump the semver version in pyproject.toml (major, minor, or patch) and
  prepend a Keep-a-Changelog entry to CHANGELOG.md. Use when preparing a
  release, before creating a PR (the version-check CI job blocks merge if
  you don't), or when the user says "bump version", "release", or
  "increment version".
---

# Version Bump

Bump the semver version in `pyproject.toml` and prepend a new entry to
`CHANGELOG.md`. Mirrors the AgentCulture workflow used by `culture`,
`afi-cli`, `cfafi`, and other org repos; vendored here so the repo is
self-contained.

## Usage

Run from the repo root.

```bash
# With changelog content (pipe JSON via stdin):
echo '{"added":["New X"],"changed":["Refactored Y"],"fixed":["Bug in Z"]}' \
  | python3 .claude/skills/version-bump/scripts/bump.py minor

# Without changelog content (inserts empty ### Added/Changed/Fixed stubs):
python3 .claude/skills/version-bump/scripts/bump.py patch

# Check current version without bumping:
python3 .claude/skills/version-bump/scripts/bump.py show
```

## Bump Types

| Type    | Example        | When to use                                                       |
|---------|----------------|-------------------------------------------------------------------|
| `major` | 0.1.0 → 1.0.0  | Breaking changes, namespace restructures, CLI surface breaks      |
| `minor` | 0.1.0 → 0.2.0  | New features, new commands, new modules                           |
| `patch` | 0.1.0 → 0.1.1  | Bug fixes, doc updates, dependency bumps, CI-only changes         |
| `show`  | prints `0.1.0` | Read-only — no files changed                                      |

## Changelog JSON Format

Pass via stdin. All fields are optional — only non-empty sections are rendered.

```json
{
  "added":   ["List of new features"],
  "changed": ["List of changes to existing functionality"],
  "fixed":   ["List of bug fixes"]
}
```

## What it touches

- `pyproject.toml` — the `version = "x.y.z"` field (single source of truth;
  `culture/__init__.py` reads it via `importlib.metadata`, so there's no
  separate `__version__` literal to keep in sync).
- `CHANGELOG.md` — inserts a new `## [x.y.z] - YYYY-MM-DD` entry at the top.

The script does the rest. Pick a bump type from the diff (patch for fixes,
minor for new features, major for breaking changes), summarize the diff into
`added` / `changed` / `fixed` lists, pipe as JSON, and commit the resulting
`pyproject.toml` + `CHANGELOG.md` alongside the code change so the
`version-check` CI job sees a consistent bump.
