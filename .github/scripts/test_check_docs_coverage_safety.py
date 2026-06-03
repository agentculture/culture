"""Regression: Qodo PR #50 round-3 #2 — git-ref option injection.

The docs-coverage script reads ``GITHUB_BASE_REF`` from the environment
and passes it directly to ``git rev-parse`` / ``git fetch``. Without
either an allowlist OR a ``--`` end-of-options separator, a ref like
``--upload-pack=evil`` would be interpreted by git as a flag — which
could rewrite the fetch URL, exfiltrate data, or simply break CI in
an attacker-controlled way.

These tests run alongside the script itself (kept under
``.github/scripts/`` deliberately — pytest collects ``test_*.py`` from
anywhere on its path, but the test file is logically tied to the
script and easier to maintain in the same directory). They verify:

  1. The shape allowlist (``_is_safe_ref``) accepts realistic branch
     names and rejects option-shaped values.
  2. ``_resolve_base`` falls back when ``GITHUB_BASE_REF`` is hostile,
     never passes the bad value to git.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).with_name("check-docs-coverage.py")


def _load_script_module():
    """Import the hyphenated script as a Python module for testing."""
    spec = importlib.util.spec_from_file_location("_check_docs_coverage", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def script():
    return _load_script_module()


class TestSafeRefAllowlist:
    @pytest.mark.parametrize(
        "ref",
        [
            "main",
            "develop",
            "release/2026-06",
            "feat/mesh-rearch-2026-06-03",
            "v9.0.0-rc.1",
            "renovate/dependency-foo-1.2.3",
            "user.name/branch",
        ],
    )
    def test_real_branch_names_accepted(self, script, ref: str) -> None:
        assert script._is_safe_ref(ref) is True

    @pytest.mark.parametrize(
        "ref",
        [
            None,
            "",
            "-foo",
            "--upload-pack=evil",
            "-o",
            "--exec=rm -rf /",
            "-c",
            "--config=core.x=y",
            "-X",
        ],
    )
    def test_option_shaped_refs_rejected(self, script, ref) -> None:
        """A ref starting with ``-`` MUST be rejected — that's the
        defining property git uses to decide ``option vs ref``."""
        assert script._is_safe_ref(ref) is False

    @pytest.mark.parametrize(
        "ref",
        [
            "ref with space",
            "ref;rm -rf /",
            "ref$(whoami)",
            "ref`whoami`",
            "ref\nmain",
            "ref\x00main",
            "..",
            "@{",
            "ref|main",
        ],
    )
    def test_command_injection_shapes_rejected(self, script, ref: str) -> None:
        """Defense-in-depth: shell-metacharacter / control-char refs
        are rejected even though ``subprocess.run`` does NOT invoke
        a shell."""
        assert script._is_safe_ref(ref) is False


class TestResolveBaseFallsBackOnHostileEnv:
    """When ``GITHUB_BASE_REF`` is hostile, the script must IGNORE it
    and fall through to dev-fallback candidates — never invoking git
    with the bad value as a positional argument."""

    def test_hostile_base_ref_does_not_reach_git(
        self, script, monkeypatch, tmp_path, capsys
    ) -> None:
        # Set a malicious GITHUB_BASE_REF.
        monkeypatch.setenv("GITHUB_BASE_REF", "--upload-pack=evil")

        # Capture every git invocation to verify the bad ref never reached it.
        captured_calls: list[list[str]] = []
        original_run = subprocess.run

        def _capture(args, *a, **kw):  # type: ignore[no-untyped-def]
            if args and args[0] == "git":
                captured_calls.append(list(args))
            # Pretend rev-parse succeeds for origin/main / main / HEAD~1
            # so the fallback path returns cleanly without needing a
            # real git repo here.
            if args[:2] == ["git", "rev-parse"]:
                # Allow only legit fallback candidates.
                # args layout: ["git","rev-parse","--verify","--","<ref>"]
                ref = args[-1]
                if ref in ("origin/main", "main", "HEAD~1"):

                    class _R:
                        returncode = 0
                        stdout = b""
                        stderr = b""

                    return _R()
                raise subprocess.CalledProcessError(1, args, b"", b"unknown revision")
            return original_run(args, *a, **kw)

        monkeypatch.setattr(subprocess, "run", _capture)

        result = script._resolve_base()
        # Returned one of the fallback candidates, NOT the hostile ref.
        assert result in ("origin/main", "main", "HEAD~1")
        # The hostile ref never appeared as a positional arg to git.
        for call in captured_calls:
            assert "--upload-pack=evil" not in call, f"hostile ref reached git: {call!r}"
        # And the rejection was logged so an operator can see it.
        captured = capsys.readouterr()
        assert "rejecting GITHUB_BASE_REF" in captured.err

    def test_safe_base_ref_is_passed_through(self, script, monkeypatch) -> None:
        """Sanity: a normal branch name DOES reach git (so the
        fallback path is only used when the env is hostile)."""
        monkeypatch.setenv("GITHUB_BASE_REF", "main")

        captured: list[list[str]] = []

        def _capture(args, *a, **kw):  # type: ignore[no-untyped-def]
            if args and args[0] == "git":
                captured.append(list(args))

            class _R:
                returncode = 0
                stdout = b""
                stderr = b""

            return _R()

        monkeypatch.setattr(subprocess, "run", _capture)
        result = script._resolve_base()
        assert result == "origin/main"  # Step 1a happy path
        # rev-parse was called with the safe ref AND with the ``--``
        # separator before it.
        found = False
        for call in captured:
            if call[:3] == ["git", "rev-parse", "--verify"] and "--" in call:
                idx = call.index("--")
                assert call[idx + 1] == "origin/main"
                found = True
                break
        assert found, f"no rev-parse call found in: {captured!r}"
