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

# Without changelog content (inserts empty ### Added/Changed/Fixed stubs).
# From scripts, daemons and agent harnesses, pass --no-stdin so the bump
# never waits on a stdin it was handed but nobody writes to:
python3 .claude/skills/version-bump/scripts/bump.py patch --no-stdin

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
The only keys are `added`, `changed` and `fixed`, each a list of strings.

Stdin is read until it closes. Any pause longer than `BUMP_STDIN_IDLE_SECONDS`
(default 5), **including before the first byte**, stops the bump with an
error; raise it for a slow producer (an LLM call, a cold `git log`).
`--no-stdin` (or `BUMP_NO_STDIN=1`) skips stdin and writes the empty stub.

Invalid JSON, an unknown key (e.g. `removed`), a value that isn't a list of
strings, or bytes that don't decode are errors too, never a silent empty
stub. Every check runs before any file is written, so a failed bump changes
nothing; fix the input and run it again.

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
- `CHANGELOG.md` — inserts a new `## [x.y.z] - YYYY-MM-DD` entry above the
  first `## [` release heading (headings inside fenced code blocks don't
  count). A leading `## [Unreleased]` section stays on top, untouched; its
  content is never moved into the release for you. A CHANGELOG with no `##`
  heading yet gets the entry at the end, or after an Unreleased-only section.
  One whose `##` headings aren't `## [...]` release headings (e.g. `## 1.2.3`)
  stops the bump with an error.

The script does the rest. Pick a bump type from the diff (patch for fixes,
minor for new features, major for breaking changes), summarize the diff into
`added` / `changed` / `fixed` lists, pipe as JSON, and commit the resulting
`pyproject.toml` + `CHANGELOG.md` alongside the code change so the
`version-check` CI job sees a consistent bump.
