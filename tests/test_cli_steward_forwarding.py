"""Tests for steward verbs forwarded through `culture agents` / `culture skills`."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize("verb", ["doctor", "show", "overview"])
def test_forwards_agents_verb_verbatim(monkeypatch, verb):
    calls = []
    import steward.cli

    monkeypatch.setattr(steward.cli, "main", lambda argv: calls.append(argv) or 0)
    from culture.cli import _maybe_forward_to_steward

    rc = _maybe_forward_to_steward(["agents", verb, "--scope", "siblings"])
    assert rc == 0
    assert calls == [[verb, "--scope", "siblings"]]


def test_forwards_skills_announce_update_remapped(monkeypatch):
    calls = []
    import steward.cli

    monkeypatch.setattr(steward.cli, "main", lambda argv: calls.append(argv) or 0)
    from culture.cli import _maybe_forward_to_steward

    rc = _maybe_forward_to_steward(["skills", "announce-update", "communicate"])
    assert rc == 0
    assert calls == [["announce-skill-update", "communicate"]]


def test_native_verbs_are_not_forwarded():
    from culture.cli import _maybe_forward_to_steward

    assert _maybe_forward_to_steward(["agents", "start", "spark-claude"]) is None
    assert _maybe_forward_to_steward(["skills", "install", "claude"]) is None
    assert _maybe_forward_to_steward(["agents"]) is None


def test_agents_doctor_help_reaches_steward():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "agents", "doctor", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "doctor" in result.stdout.lower()


def test_explain_agents_documents_forwarded_verbs():
    from culture.cli import introspect

    out, rc = introspect.explain("agents")
    assert rc == 0
    # The forwarded alignment verbs must be visible in the noun's explain text.
    for verb in ("doctor", "show", "overview"):
        assert verb in out, f"explain agents omits forwarded verb {verb!r}: {out}"


def test_explain_skills_documents_announce_update():
    from culture.cli import introspect

    out, rc = introspect.explain("skills")
    assert rc == 0
    assert "announce-update" in out
