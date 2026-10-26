#!/usr/bin/env python3
# Cited from agentculture/culture-rules@b5e8635 scripts/sonar-pr-issues.py (cite, don't import).
"""Fail a pull request that has unresolved SonarCloud issues.

SonarCloud's quality gate can pass with open issues on new code (a code smell does not
turn it red), so CI runs this right after the scan: it waits until SonarCloud's analysis
of the PR is for the PR's head commit, then lists the PR's issues whose status is OPEN or
CONFIRMED and exits 1 when there is any. ACCEPTED and FALSE_POSITIVE issues are resolved
by a person and pass; FIXED ones are gone.

Environment: ``SONAR_HOST_URL`` (the server; defaults to localhost so nothing outside is
called by accident), ``SONAR_PROJECT_KEY`` (else ``sonar.projectKey`` from
``sonar-project.properties``), ``PR_NUMBER``, ``HEAD_SHA`` and optionally ``SONAR_TOKEN``.
Exit codes: 0 no unresolved issue, 1 unresolved issues, 2 the analysis or the API could
not be read. Standard library only.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

UNRESOLVED = ("OPEN", "CONFIRMED")
PAGE_SIZE = 100
WAIT_S = 300
POLL_S = 10

Fetch = Callable[[str, Mapping[str, str]], Mapping[str, Any]]


def project_key(env: Mapping[str, str], root: Path) -> str:
    """``SONAR_PROJECT_KEY``, else ``sonar.projectKey`` from ``sonar-project.properties``."""
    if env.get("SONAR_PROJECT_KEY"):
        return env["SONAR_PROJECT_KEY"]
    for line in (root / "sonar-project.properties").read_text().splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "sonar.projectKey" and value.strip():
            return value.strip()
    raise SystemExit("sonar-pr-issues: no SONAR_PROJECT_KEY and no sonar.projectKey")


def http_fetch(host: str, token: str | None) -> Fetch:
    """A GET of ``host + path`` with ``params``, as JSON (token as basic auth, if any)."""

    def fetch(path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        url = f"{host.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url)  # noqa: S310 - the host is the configured server
        if token:
            basic = base64.b64encode(f"{token}:".encode()).decode()
            req.add_header("Authorization", f"Basic {basic}")
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return json.load(resp)

    return fetch


def analysed_commit(fetch: Fetch, project: str, pr: str) -> str | None:
    """The commit SonarCloud last analysed for pull request ``pr``, or ``None``."""
    data = fetch("/api/project_pull_requests/list", {"project": project})
    for item in data.get("pullRequests") or ():
        if str(item.get("key")) == pr:
            commit = item.get("commit") or {}
            sha = commit.get("sha")
            return sha if isinstance(sha, str) else None
    return None


def wait_for_analysis(
    fetch: Fetch,
    project: str,
    pr: str,
    head: str,
    *,
    wait_s: float = WAIT_S,
    poll_s: float = POLL_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """True once the PR's analysis is for ``head``; False when ``wait_s`` runs out."""
    deadline = clock() + wait_s
    while True:
        if analysed_commit(fetch, project, pr) == head:
            return True
        if clock() >= deadline:
            return False
        sleep(poll_s)


def unresolved_issues(fetch: Fetch, project: str, pr: str) -> list[Mapping[str, Any]]:
    """Every OPEN or CONFIRMED issue of pull request ``pr``, across pages."""
    out: list[Mapping[str, Any]] = []
    page = 1
    while True:
        data = fetch(
            "/api/issues/search",
            {
                "componentKeys": project,
                "pullRequest": pr,
                "issueStatuses": ",".join(UNRESOLVED),
                "ps": str(PAGE_SIZE),
                "p": str(page),
            },
        )
        issues = [i for i in data.get("issues") or () if isinstance(i, Mapping)]
        out.extend(issues)
        total = data.get("total")
        if not issues or not isinstance(total, int) or len(out) >= total:
            return out
        page += 1


def describe(issue: Mapping[str, Any], project: str) -> str:
    """One line: rule, file and line, severity, message."""
    component = str(issue.get("component") or "").removeprefix(f"{project}:")
    line = issue.get("line")
    where = f"{component}:{line}" if line else component
    return (
        f"[{issue.get('rule')}] {where} ({issue.get('severity')}, "
        f"{issue.get('issueStatus') or issue.get('status')}): {issue.get('message')}"
    )


def main(
    env: Mapping[str, str] | None = None,
    fetch: Fetch | None = None,
    root: Path | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    env = os.environ if env is None else env
    root = Path(__file__).resolve().parents[1] if root is None else root
    pr, head = env.get("PR_NUMBER", ""), env.get("HEAD_SHA", "")
    if not pr or not head:
        print("sonar-pr-issues: PR_NUMBER and HEAD_SHA are required", file=sys.stderr)
        return 2
    project = project_key(env, root)
    if fetch is None:
        host = env.get("SONAR_HOST_URL") or "http://localhost:9000"
        fetch = http_fetch(host, env.get("SONAR_TOKEN") or None)
    try:
        if not wait_for_analysis(fetch, project, pr, head, sleep=sleep):
            print(
                f"sonar-pr-issues: no SonarCloud analysis of PR #{pr} at {head[:12]}",
                file=sys.stderr,
            )
            return 2
        issues = unresolved_issues(fetch, project, pr)
    except (OSError, ValueError) as exc:  # URLError is an OSError; bad JSON a ValueError
        print(f"sonar-pr-issues: SonarCloud could not be read: {exc}", file=sys.stderr)
        return 2
    if not issues:
        print(f"SonarCloud: PR #{pr} has no unresolved issue (accepted ones pass)")
        return 0
    print(f"SonarCloud: PR #{pr} has {len(issues)} unresolved issue(s); fix or accept each:")
    for issue in issues:
        print(f"  - {describe(issue, project)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
