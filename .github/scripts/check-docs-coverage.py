#!/usr/bin/env python3
"""Docs coverage gate for the v8.19.6 quality-gates wedge.

Fails CI when a PR adds public surface (CLI subcommand, IRC verb,
exception class, ``_ipc_<name>`` handler, AgentConfig field) but
the diff doesn't touch ``docs/`` or ``protocol/extensions/``.

Mirrors the ``doc-test-alignment`` subagent's intent in CI form —
the subagent does deeper LLM-assisted analysis, this script is a
fast deterministic spine that catches the common cases.

The script reads the base..HEAD diff via ``git diff`` (no untrusted
shell input — only the literal arg list is passed). Heuristics are
deliberately permissive: false negatives over false positives, so
nuisance failures stay rare. When a heuristic does fire, the script
prints WHICH new surface it found and WHERE so the author has a
clear next step.

Exits 0 on pass, 1 on failure, 2 on invocation error.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Patterns that flag "public surface added" in a diff line.
# Each entry: (regex, human description).
_PUBLIC_SURFACE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # CLI: a new add_parser("name", ...) call
    (re.compile(r"\.add_parser\(\s*['\"](\w[\w-]*)['\"]"), "new CLI subcommand"),
    # IPC: a new _ipc_<name> handler (per-backend daemon dispatch)
    (re.compile(r"^\+\s*async def _ipc_(\w+)"), "new IPC handler"),
    # Exception classes (new error types callers must catch)
    (re.compile(r"^\+\s*class (\w+Error|\w+Exception)\("), "new exception class"),
    # IRC verb: numeric reply added (444+) or VERB handler in agentirc
    (
        re.compile(r"^\+\s*async def _on_(\w+)\(self, msg: Message\)"),
        "new IRC verb handler in agentirc",
    ),
    # Backend config: new field on AgentConfig dataclass (heuristic on dataclass field syntax)
    (
        re.compile(r"^\+\s+(\w+): [a-zA-Z_][\w\[\], |]+= "),
        "new AgentConfig / ServerConfig field",
    ),
)

# Files whose presence in the diff counts as "docs updated".
_DOC_PATH_PREFIXES = ("docs/", "protocol/extensions/", "CHANGELOG.md")


def _run_git(args: list[str]) -> str:
    """Run ``git <args>`` and return stdout. Exit 2 on failure."""
    try:
        result = subprocess.run(  # noqa: S603 — literal arg list, no shell
            ["git", *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        print(f"git failed: {exc.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return result.stdout


# Refs we accept: alphanumerics, dot, underscore, slash, hyphen — but
# NOT a leading hyphen, which git would parse as an option flag.
# Matches the bulk of valid git branch / tag names without trying to
# reimplement ``git check-ref-format`` in regex. Qodo PR #50 round-3
# #2: ``GITHUB_BASE_REF`` comes from the GH Actions env (set by GitHub
# from the PR target branch). It's untrusted from the script's
# perspective and must NOT be allowed to start with ``-``.
_REF_NAME_RE = re.compile(r"^[A-Za-z0-9_./][A-Za-z0-9_./-]*$")


def _is_safe_ref(value: str | None) -> bool:
    """True iff *value* is a non-empty git-ref-shaped string that
    cannot be parsed as a CLI option flag by git.

    Rejects: ``None`` / empty / leading-``-`` / chars outside the
    conservative allowlist. The allowlist is narrower than
    ``git check-ref-format`` to avoid having to think about edge
    cases — anything that fails this check falls through to the
    dev-fallback candidates.

    Extra explicit rejections beyond the regex shape:
    - ``..`` anywhere — git's parent-revision shorthand. A ref like
      ``..main`` resolves to "the commits in main not in HEAD",
      which is NOT the merge base.
    - ``@{`` — git's reflog-index shorthand.
    """
    if not value:
        return False
    if ".." in value or "@{" in value:
        return False
    return _REF_NAME_RE.fullmatch(value) is not None


def _resolve_base() -> str:
    """Return a ref-spec that resolves to the PR's merge base.

    Resolution order:

    1. ``GITHUB_BASE_REF`` env var (set by GitHub Actions on
       ``pull_request`` events to the target branch name). We fetch it
       on demand because ``actions/checkout@v4`` does a shallow clone
       by default and the base ref is NOT present in the working tree.
       The ref must pass :func:`_is_safe_ref` so a hostile value like
       ``--upload-pack=...`` (Qodo PR #50 round-3 #2) cannot be
       interpreted by git as an option flag.
    2. ``origin/main`` (already-fetched remote — common in dev).
    3. ``main`` (local main branch — common in dev).
    4. ``HEAD~1`` (last-resort one-commit-back probe for local runs).

    The previous implementation skipped step 1, so on a GitHub Actions
    fork PR the script would print ``could not resolve a base ref``
    and exit 2 — false-failing the docs-coverage gate on every PR
    until the operator hand-fetched the base.

    Every git invocation below uses an explicit ``--`` end-of-options
    separator AS WELL as the allowlist validation, as defense-in-
    depth — even if the allowlist were ever relaxed, git would still
    refuse to interpret the value as an option.
    """
    import os as _os

    github_base = _os.environ.get("GITHUB_BASE_REF")
    if github_base and not _is_safe_ref(github_base):
        print(
            f"warn: rejecting GITHUB_BASE_REF={github_base!r} — "
            "fails ref-format allowlist; falling back",
            file=sys.stderr,
        )
        github_base = None
    if github_base:
        # Try the already-fetched remote tracking ref first (no network call).
        remote_ref = f"origin/{github_base}"
        try:
            subprocess.run(  # noqa: S603
                ["git", "rev-parse", "--verify", "--", remote_ref],
                check=True,
                capture_output=True,
            )
            return remote_ref
        except subprocess.CalledProcessError:
            pass
        # Shallow clone — fetch the base branch with a single network call.
        # ``git fetch`` accepts the ref as a refspec (no ``--`` needed for
        # the positional refspec slot — fetch's syntax differs from
        # rev-parse), but we have ALREADY validated the ref above. As an
        # extra defense, pin the option boundary with ``--`` before the
        # remote name: any future modifications can't accidentally turn
        # an option into a refspec.
        try:
            subprocess.run(  # noqa: S603
                [
                    "git",
                    "fetch",
                    "--no-tags",
                    "--depth=1",
                    "--",
                    "origin",
                    github_base,
                ],
                check=True,
                capture_output=True,
            )
            return f"origin/{github_base}"
        except subprocess.CalledProcessError as exc:
            print(
                f"warn: could not fetch GITHUB_BASE_REF={github_base!r}: "
                f"{exc.stderr.decode(errors='replace').strip()}",
                file=sys.stderr,
            )
            # Fall through to dev-fallback candidates.

    candidates = ["origin/main", "main", "HEAD~1"]
    for ref in candidates:
        try:
            subprocess.run(  # noqa: S603
                ["git", "rev-parse", "--verify", "--", ref],
                check=True,
                capture_output=True,
            )
            return ref
        except subprocess.CalledProcessError:
            continue
    print("could not resolve a base ref", file=sys.stderr)
    sys.exit(2)


def _changed_files(base: str) -> list[str]:
    """Files that differ between *base* and HEAD."""
    return [
        line.strip()
        for line in _run_git(["diff", "--name-only", f"{base}..HEAD"]).splitlines()
        if line.strip()
    ]


def _additions(base: str) -> str:
    """Unified diff (added lines only) between *base* and HEAD."""
    return _run_git(["diff", f"{base}..HEAD"])


def _find_new_surface(diff: str) -> list[tuple[str, str, str]]:
    """Scan *diff* for new public surface.

    Returns a list of ``(file, description, matched_token)`` tuples.
    """
    findings: list[tuple[str, str, str]] = []
    current_file = ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[len("+++ b/") :]
            continue
        if not line.startswith("+") or line.startswith("+++"):
            continue
        # Heuristic: only flag surface added in production code, not tests.
        if current_file.startswith(
            ("tests/", "packages/", "docs/", ".")
        ) or not current_file.endswith(".py"):
            continue
        for pattern, label in _PUBLIC_SURFACE_PATTERNS:
            m = pattern.search(line)
            if m:
                token = m.group(1) if m.groups() else line.strip()
                findings.append((current_file, label, token))
                break  # one finding per line is enough
    return findings


def main() -> int:
    base = _resolve_base()
    files = _changed_files(base)
    if not files:
        print("docs-coverage: no changed files — pass")
        return 0

    diff = _additions(base)
    new_surface = _find_new_surface(diff)
    if not new_surface:
        print("docs-coverage: no new public surface detected — pass")
        return 0

    docs_touched = [f for f in files if f.startswith(_DOC_PATH_PREFIXES)]
    if docs_touched:
        print(
            f"docs-coverage: {len(new_surface)} surface additions detected "
            f"AND docs/changelog touched ({len(docs_touched)} files) — pass"
        )
        return 0

    # No docs change despite new surface — fail with a specific report.
    print("docs-coverage: FAIL — new public surface added without docs update")
    print()
    print("New surface detected:")
    seen: set[tuple[str, str]] = set()
    for file, label, token in new_surface:
        key = (label, token)
        if key in seen:
            continue
        seen.add(key)
        print(f"  - {label} `{token}` in {file}")
    print()
    print("Add a docs/<feature>.md page or update an existing one, OR add")
    print("a CHANGELOG.md entry, OR if this is a refactor that doesn't")
    print("change the user-visible surface, justify with a `[skip-docs]`")
    print("token in the PR title.")
    return 1


if __name__ == "__main__":
    # PR titles can opt out with [skip-docs] (e.g. pure-internal refactors).
    # The check then short-circuits to pass.
    pr_title = _run_git(["log", "-1", "--pretty=%s"]).strip()
    if "[skip-docs]" in pr_title:
        print("docs-coverage: [skip-docs] in commit title — bypass")
        sys.exit(0)
    sys.exit(main())
