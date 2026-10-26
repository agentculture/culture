"""Cited from agentculture/culture-rules@b5e8635 tests/test_sonar_pr_issues.py.

Tests for scripts/sonar-pr-issues.py: a PR with an unresolved SonarCloud issue fails CI.

The SonarCloud API is a fake ``fetch`` keyed by path; nothing outside is called.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "sonar-pr-issues.py"

_spec = importlib.util.spec_from_file_location("sonar_pr_issues", SCRIPT)
spi = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
sys.modules["sonar_pr_issues"] = spi
_spec.loader.exec_module(spi)

PROJECT = "agentculture_culture"
HEAD = "c8207ca" + "0" * 33
ENV = {"PR_NUMBER": "43", "HEAD_SHA": HEAD, "SONAR_PROJECT_KEY": PROJECT}


def issue(rule="python:S3776", status="OPEN", line=153):
    return {
        "rule": rule,
        "component": f"{PROJECT}:culture_rules/events/emit.py",
        "line": line,
        "severity": "CRITICAL",
        "issueStatus": status,
        "message": "Refactor this function",
    }


class FakeSonar:
    """``/api/project_pull_requests/list`` answers ``commits`` in turn (the last repeats);
    ``/api/issues/search`` answers the issues whose status was asked for, one per page."""

    def __init__(self, commits, issues=()):
        self.commits = list(commits)
        self.issues = list(issues)
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        if path == "/api/project_pull_requests/list":
            sha = self.commits.pop(0) if len(self.commits) > 1 else self.commits[0]
            return {"pullRequests": [{"key": "42"}, {"key": "43", "commit": {"sha": sha}}]}
        wanted = params["issueStatuses"].split(",")
        found = [i for i in self.issues if i["issueStatus"] in wanted]
        page = int(params["p"])
        return {"total": len(found), "issues": found[page - 1 : page]}


def run(fake, capsys):
    code = spi.main(env=ENV, fetch=fake, root=REPO_ROOT, sleep=lambda s: None)
    return code, capsys.readouterr()


def test_a_pr_without_issues_passes(capsys):
    code, out = run(FakeSonar([HEAD]), capsys)
    assert code == 0
    assert "no unresolved issue" in out.out


def test_an_open_issue_fails_and_is_listed(capsys):
    code, out = run(FakeSonar([HEAD], [issue()]), capsys)
    assert code == 1
    assert "[python:S3776] culture_rules/events/emit.py:153" in out.out


def test_accepted_and_false_positive_issues_pass(capsys):
    fake = FakeSonar([HEAD], [issue(status="ACCEPTED"), issue(status="FALSE_POSITIVE")])
    code, _ = run(fake, capsys)
    assert code == 0
    (search,) = [p for path, p in fake.calls if path == "/api/issues/search"]
    assert search["issueStatuses"] == "OPEN,CONFIRMED"


def test_a_confirmed_issue_fails(capsys):
    code, _ = run(FakeSonar([HEAD], [issue(status="CONFIRMED")]), capsys)
    assert code == 1


def test_every_page_is_read(capsys):
    issues = [issue(line=n) for n in range(1, 4)]
    code, out = run(FakeSonar([HEAD], issues), capsys)
    assert code == 1
    assert "3 unresolved issue(s)" in out.out


def test_it_waits_for_the_analysis_of_the_head_commit(capsys):
    fake = FakeSonar(["old" + "0" * 37, "old" + "0" * 37, HEAD], [issue()])
    code, _ = run(fake, capsys)
    assert code == 1
    lists = [c for c in fake.calls if c[0] == "/api/project_pull_requests/list"]
    assert len(lists) == 3


def test_no_analysis_of_the_head_is_an_environment_error(capsys):
    ticks = iter(range(0, 10_000, 100))
    fake = FakeSonar(["old" + "0" * 37])
    ok = spi.wait_for_analysis(
        fake, PROJECT, "43", HEAD, wait_s=300, sleep=lambda s: None, clock=lambda: next(ticks)
    )
    assert ok is False


def test_an_unreadable_api_is_an_environment_error(capsys):
    def broken(path, params):
        raise OSError("connection refused")

    code, out = run(broken, capsys)
    assert code == 2
    assert "could not be read" in out.err


def test_the_project_key_comes_from_sonar_project_properties():
    assert spi.project_key({}, REPO_ROOT) == PROJECT


def test_missing_pr_or_head_is_an_environment_error(capsys):
    code = spi.main(env={}, fetch=FakeSonar([HEAD]), root=REPO_ROOT, sleep=lambda s: None)
    assert code == 2
