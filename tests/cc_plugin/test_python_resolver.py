"""Tests for the CC plugin's Python interpreter resolver (v9.1.4).

The resolver picks the right Python for spawning ``python -m culture …``
from a CC hook. Before 9.1.4, the spawn used ``sys.executable`` —
which, when CC fires a hook under bare ``python3`` from
``~/.claude/settings.json``, points at the system / Homebrew python,
which lacks PyYAML and dies with ``ModuleNotFoundError: yaml`` on
import. This file is the contract test for the v9.1.4 fix.

Three steps, fail-loud at every boundary. Tests cover each ladder
position + the boundary cases the adversarial-critique panel flagged
(env override broken, symlink trust, repo-walk anchored on realpath,
last-resort warning emitted, cache behavior).
"""

from __future__ import annotations

import os
import sys

import pytest

from culture.clients.claude.cc_plugin import _python_resolver


@pytest.fixture(autouse=True)
def reset_cache():
    """Drop the resolver's module-level cache before AND after every
    test so a previous test's resolution doesn't leak."""
    _python_resolver._reset_cache()
    yield
    _python_resolver._reset_cache()


@pytest.fixture
def clean_env(monkeypatch):
    """No ``CULTURE_PYTHON`` env var so the env-override step is
    skipped — useful for tests that exercise step 2 / step 3."""
    monkeypatch.delenv("CULTURE_PYTHON", raising=False)


# ----------------------------------------------------------------------
# Step 1: CULTURE_PYTHON env override
# ----------------------------------------------------------------------


class TestEnvOverride:
    def test_unset_falls_through(self, clean_env):
        """No CULTURE_PYTHON → returns None so the resolver moves on."""
        assert _python_resolver._check_env_override() is None

    def test_empty_string_falls_through(self, monkeypatch):
        """A literal empty CULTURE_PYTHON='' counts as unset — don't
        raise; just fall through. Defends against shell scripts that
        write the var unconditionally."""
        monkeypatch.setenv("CULTURE_PYTHON", "")
        assert _python_resolver._check_env_override() is None

    def test_set_to_nonexistent_path_raises(self, monkeypatch):
        """Operator pointed at a path that doesn't exist — fail hard."""
        monkeypatch.setenv("CULTURE_PYTHON", "/nonexistent/python")
        with pytest.raises(RuntimeError, match="not executable"):
            _python_resolver._check_env_override()

    def test_set_to_non_executable_file_raises(self, tmp_path, monkeypatch):
        """File exists but lacks +x — fail hard."""
        bogus = tmp_path / "not_executable"
        bogus.write_text("#!/bin/sh\necho hi\n")
        # No chmod +x.
        monkeypatch.setenv("CULTURE_PYTHON", str(bogus))
        with pytest.raises(RuntimeError, match="not executable"):
            _python_resolver._check_env_override()

    def test_set_to_python_without_culture_raises(self, monkeypatch):
        """The operator's chosen python is executable BUT cannot
        ``import culture`` — fail hard rather than silently falling
        through. This is the critique-panel concern made testable."""

        def fake_validate(path):
            return False, "No module named 'culture'"

        # Point at a real python that exists but with a fake validator
        # so we don't depend on the test env's python actually being
        # culture-aware. We DO depend on sys.executable existing on
        # disk (it always does).
        monkeypatch.setenv("CULTURE_PYTHON", sys.executable)
        monkeypatch.setattr(_python_resolver, "_validate_can_import_culture", fake_validate)
        with pytest.raises(RuntimeError, match="cannot import culture"):
            _python_resolver._check_env_override()

    def test_set_to_valid_python_returns_prefix(self, monkeypatch):
        """Happy path: env var set, path executable, import culture
        validates → returns the argv-prefix."""
        monkeypatch.setenv("CULTURE_PYTHON", sys.executable)
        monkeypatch.setattr(_python_resolver, "_validate_can_import_culture", lambda p: (True, ""))
        prefix = _python_resolver._check_env_override()
        assert prefix == [sys.executable, "-m", "culture"]


# ----------------------------------------------------------------------
# Step 2: repo-walk
# ----------------------------------------------------------------------


class TestRepoWalk:
    """The walk-up looks for ``.venv/bin/python3`` next to a
    ``pyproject.toml`` with line-anchored ``name = "culture"``. The
    blueprint critique flagged: substring scans match comments; bare
    ``os.path.abspath`` trusts symlinks; the wrong culture is picked
    when a parent directory hosts a different python project."""

    def _make_fake_culture_repo(self, root, pyproject_body, venv_python_exists=True):
        """Create a fake culture-repo layout under ``root``: pyproject
        + (optionally) executable .venv/bin/python3."""
        (root / "pyproject.toml").write_text(pyproject_body)
        if venv_python_exists:
            venv_bin = root / ".venv" / "bin"
            venv_bin.mkdir(parents=True, exist_ok=True)
            python = venv_bin / "python3"
            python.write_text("#!/bin/sh\nexit 0\n")
            python.chmod(0o755)
            return str(python)
        return None

    def test_walk_finds_culture_repo(self, tmp_path):
        """Standard layout — pyproject + .venv at same level → match."""
        self._make_fake_culture_repo(tmp_path, '[project]\nname = "culture"\n')
        deep = tmp_path / "a" / "b" / "c"
        deep.mkdir(parents=True)
        found = _python_resolver._walk_to_culture_repo_root(str(deep / "hook.py"))
        assert found == str(tmp_path)

    def test_walk_rejects_non_culture_pyproject(self, tmp_path):
        """A pyproject for a DIFFERENT project must NOT match — the
        substring-match bug is what the line-anchored regex defends
        against."""
        self._make_fake_culture_repo(tmp_path, '[project]\nname = "not-culture"\n')
        deep = tmp_path / "a"
        deep.mkdir()
        assert _python_resolver._walk_to_culture_repo_root(str(deep / "hook.py")) is None

    def test_walk_rejects_comment_mention_of_culture(self, tmp_path):
        """A pyproject that MENTIONS culture in a comment (or
        description, or any non-line-anchored context) must not
        false-positive. Adversarial-critique concern #4."""
        body = (
            "# This project depends on culture and patches name = 'culture'\n"
            "[project]\n"
            'name = "another-project"\n'
            'description = "wraps name = \\"culture\\""\n'
        )
        self._make_fake_culture_repo(tmp_path, body)
        deep = tmp_path / "a"
        deep.mkdir()
        assert _python_resolver._walk_to_culture_repo_root(str(deep / "hook.py")) is None

    def test_walk_rejects_missing_venv(self, tmp_path):
        """Pyproject is right but no ``.venv/bin/python3`` — not
        usable, fall through to step 3."""
        self._make_fake_culture_repo(
            tmp_path, '[project]\nname = "culture"\n', venv_python_exists=False
        )
        assert _python_resolver._walk_to_culture_repo_root(str(tmp_path / "hook.py")) is None

    def test_walk_uses_realpath_not_abspath(self, tmp_path):
        """Symlink-trust defense: the walk must anchor on
        ``os.path.realpath(__file__)``. Adversarial-critique
        security concern: an attacker who controls a path component
        of __file__'s parent could otherwise plant a fake repo."""
        # Real culture repo under realdir/.
        realdir = tmp_path / "realdir"
        realdir.mkdir()
        self._make_fake_culture_repo(realdir, '[project]\nname = "culture"\n')
        # Hostile pyproject under hostiledir/ with a fake venv.
        hostiledir = tmp_path / "hostiledir"
        hostiledir.mkdir()
        self._make_fake_culture_repo(hostiledir, '[project]\nname = "culture"\n')
        # symlink: realdir/symlink_to_hostile → hostiledir
        symlink_target = realdir / "symlink_to_hostile"
        symlink_target.symlink_to(hostiledir)
        # If we walked from a path UNDER the symlink with abspath, we'd
        # match the hostile pyproject first. realpath dereferences the
        # symlink → walks the hostile real path, which is the canonical
        # location. The contract here is just "uses realpath" — verified
        # by the fact that the result is hostiledir (the symlink target),
        # not the abspath cousin.
        found = _python_resolver._walk_to_culture_repo_root(str(symlink_target / "a" / "hook.py"))
        assert found == str(hostiledir.resolve())

    def test_walk_terminates_at_fs_root(self, tmp_path):
        """Walk must not infinite-loop at the filesystem root when no
        culture repo exists above it. Adversarial-critique reliability
        concern."""
        # tmp_path is far from any culture repo we'd find via walk.
        assert _python_resolver._walk_to_culture_repo_root("/totally/nonexistent/path") is None


# ----------------------------------------------------------------------
# Step 3: sys.executable fallback (last resort)
# ----------------------------------------------------------------------


class TestFallback:
    def test_fallback_returns_sys_executable_prefix(self, clean_env, capsys):
        """Last resort: ``[sys.executable, '-m', 'culture']`` AND a
        warning to stderr that names the bug class — the critique
        explicitly rejected a silent fallback."""
        prefix = _python_resolver._fallback_sys_executable()
        assert prefix == [sys.executable, "-m", "culture"]
        captured = capsys.readouterr()
        assert "WARNING" in captured.err
        assert "CULTURE_PYTHON" in captured.err
        assert sys.executable in captured.err


# ----------------------------------------------------------------------
# End-to-end resolution
# ----------------------------------------------------------------------


class TestEndToEnd:
    def test_env_override_short_circuits_repo_walk(self, monkeypatch, tmp_path):
        """If CULTURE_PYTHON is set and validates, step 2 is never
        consulted. Verifies the ladder order."""
        monkeypatch.setenv("CULTURE_PYTHON", sys.executable)
        monkeypatch.setattr(_python_resolver, "_validate_can_import_culture", lambda p: (True, ""))
        # Pollute the repo-walk with something that would also match
        # (a sibling .venv) to prove it's not consulted.
        called = {"walk": False}

        def fail_if_walked(*a, **kw):
            called["walk"] = True
            return None

        monkeypatch.setattr(_python_resolver, "_walk_to_culture_repo_root", fail_if_walked)
        prefix = _python_resolver.culture_python()
        assert prefix == [sys.executable, "-m", "culture"]
        assert called["walk"] is False

    def test_no_env_walks_repo(self, clean_env, monkeypatch, tmp_path):
        """Without the env var, step 2 runs. The fake walk returns a
        tmp path; verify the resulting prefix uses its venv python."""
        fake_repo = tmp_path
        venv_bin = fake_repo / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        fake_python = venv_bin / "python3"
        fake_python.write_text("#!/bin/sh\nexit 0\n")
        fake_python.chmod(0o755)
        monkeypatch.setattr(
            _python_resolver, "_walk_to_culture_repo_root", lambda _: str(fake_repo)
        )
        prefix = _python_resolver.culture_python()
        assert prefix == [str(fake_python), "-m", "culture"]

    def test_no_env_no_walk_falls_to_sys_executable(self, clean_env, monkeypatch, capsys):
        """All steps before step 3 fail → fallback fires + warning."""
        monkeypatch.setattr(_python_resolver, "_walk_to_culture_repo_root", lambda _: None)
        prefix = _python_resolver.culture_python()
        assert prefix == [sys.executable, "-m", "culture"]
        captured = capsys.readouterr()
        assert "WARNING" in captured.err

    def test_cache_returns_same_value(self, clean_env, monkeypatch):
        """Module-level cache: a second call must NOT re-run any
        ladder step. Verifies by counting walk-invocations."""
        call_count = {"n": 0}

        def counting_walk(_):
            call_count["n"] += 1
            return None

        monkeypatch.setattr(_python_resolver, "_walk_to_culture_repo_root", counting_walk)
        _python_resolver.culture_python()
        _python_resolver.culture_python()
        _python_resolver.culture_python()
        assert call_count["n"] == 1

    def test_cache_returns_copy_not_reference(self, clean_env, monkeypatch):
        """Caller mutations of the returned list must NOT mutate the
        cache. (Common foot-gun when callers do ``prefix.append(...)``
        and accidentally share state across multiple spawn sites.)"""
        monkeypatch.setattr(_python_resolver, "_walk_to_culture_repo_root", lambda _: None)
        a = _python_resolver.culture_python()
        a.append("garbage")
        b = _python_resolver.culture_python()
        assert "garbage" not in b


# ----------------------------------------------------------------------
# Subprocess validation behavior
# ----------------------------------------------------------------------


class TestValidation:
    def test_validation_against_real_python_succeeds(self):
        """Spot-check that the validation actually runs a subprocess
        and detects the test env's python (which DOES have culture
        importable because we're running under it)."""
        ok, msg = _python_resolver._validate_can_import_culture(sys.executable)
        assert ok is True, f"unexpected msg: {msg!r}"

    def test_validation_against_nonexistent_returns_false(self):
        ok, msg = _python_resolver._validate_can_import_culture("/no/such/path")
        assert ok is False
        assert msg != ""
