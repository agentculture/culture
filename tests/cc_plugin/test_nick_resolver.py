"""Tests for ``culture.clients.claude.cc_plugin._nick_resolver``.

Walk every priority tier (a)–(e). Per NT-15 in the rearchitecture plan:

    (a) explicit env var ``CULTURE_BOSS_NICK``
    (b) ``<cwd>/culture.yaml`` ``nick:`` field
    (c) git remote-origin basename
    (d) cwd basename
    (e) legacy fallback ``local-boss`` with a warning logged
"""

from __future__ import annotations

import logging
import os
import subprocess

import pytest

from culture.clients.claude.cc_plugin import _nick_resolver
from culture.clients.claude.cc_plugin._nick_resolver import resolve_project_nick


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """A cwd with no env override, no culture.yaml, and no git remote."""
    monkeypatch.delenv("CULTURE_BOSS_NICK", raising=False)
    return tmp_path


class TestPriorityA:
    def test_env_var_wins(self, isolated_cwd, monkeypatch):
        monkeypatch.setenv("CULTURE_BOSS_NICK", "mesh-design")
        assert resolve_project_nick(str(isolated_cwd)) == "mesh-design"

    def test_env_var_sanitized(self, isolated_cwd, monkeypatch):
        # Forward-slash + uppercase + trailing whitespace get cleaned up.
        monkeypatch.setenv("CULTURE_BOSS_NICK", " Fork/Rearch ")
        assert resolve_project_nick(str(isolated_cwd)) == "fork-rearch"

    def test_env_var_clipped_to_max_len(self, isolated_cwd, monkeypatch):
        monkeypatch.setenv("CULTURE_BOSS_NICK", "abcdefghijklmnopqrstuvwxyz")
        out = resolve_project_nick(str(isolated_cwd))
        assert len(out) <= 14
        assert out == "abcdefghijklmn"

    def test_too_short_env_falls_through_to_d(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_BOSS_NICK", "ab")
        cwd = tmp_path / "longer-name"
        cwd.mkdir()
        assert resolve_project_nick(str(cwd)) == "longer-name"


class TestPriorityB:
    def test_yaml_nick_field(self, isolated_cwd):
        (isolated_cwd / "culture.yaml").write_text("nick: payment-debug\nbackend: claude\n")
        assert resolve_project_nick(str(isolated_cwd)) == "payment-debug"

    def test_yaml_with_quoted_nick(self, isolated_cwd):
        (isolated_cwd / "culture.yaml").write_text('nick: "fork-rearch"\n')
        assert resolve_project_nick(str(isolated_cwd)) == "fork-rearch"

    def test_yaml_overrides_git(self, isolated_cwd, monkeypatch):
        """Priority (b) > (c)."""
        (isolated_cwd / "culture.yaml").write_text("nick: yaml-wins\n")

        # Pretend git would resolve to a different name.
        def fake_run(*args, **kwargs):
            class R:
                returncode = 0
                stdout = "git@github.com:foo/git-wins.git\n"

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert resolve_project_nick(str(isolated_cwd)) == "yaml-wins"


class TestPriorityC:
    def test_git_remote_basename(self, isolated_cwd, monkeypatch):
        def fake_run(*args, **kwargs):
            class R:
                returncode = 0
                stdout = "git@github.com:foo/fork-rearch.git\n"

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert resolve_project_nick(str(isolated_cwd)) == "fork-rearch"

    def test_git_https_url(self, isolated_cwd, monkeypatch):
        def fake_run(*args, **kwargs):
            class R:
                returncode = 0
                stdout = "https://github.com/foo/payment-debug.git\n"

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert resolve_project_nick(str(isolated_cwd)) == "payment-debug"

    def test_git_unavailable_falls_through(self, isolated_cwd, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("git not installed")

        monkeypatch.setattr(subprocess, "run", fake_run)
        # Falls through to (d) cwd basename.
        assert (
            resolve_project_nick(str(isolated_cwd))
            == isolated_cwd.name.lower()[: _nick_resolver._MAX_LEN]
        )


class TestPriorityD:
    def test_cwd_basename(self, tmp_path, monkeypatch):
        """No env, no yaml, no git remote → cwd basename wins."""
        monkeypatch.delenv("CULTURE_BOSS_NICK", raising=False)
        cwd = tmp_path / "fork-rearch"
        cwd.mkdir()

        def fake_run(*args, **kwargs):
            class R:
                returncode = 1
                stdout = ""

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert resolve_project_nick(str(cwd)) == "fork-rearch"


class TestPriorityE:
    def test_legacy_fallback_when_nothing_resolves(self, tmp_path, monkeypatch, caplog):
        """Empty env, no yaml, no git, cwd basename too short → legacy
        fallback ``local-boss`` with a warning logged."""
        monkeypatch.delenv("CULTURE_BOSS_NICK", raising=False)
        cwd = tmp_path / "ab"  # 2 chars — below MIN_LEN
        cwd.mkdir()

        def fake_run(*args, **kwargs):
            class R:
                returncode = 1
                stdout = ""

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)
        caplog.set_level(logging.WARNING)
        result = resolve_project_nick(str(cwd))
        assert result == "local-boss"
        assert any("local-boss" in r.message for r in caplog.records)


class TestSanitization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Fork-Rearch", "fork-rearch"),
            ("foo bar baz", "foo-bar-baz"),
            ("with.dots", "with-dots"),
            ("with/slash", "with-slash"),
        ],
    )
    def test_sanitize_lowercases_and_replaces_invalid(self, raw, expected):
        assert _nick_resolver._sanitize(raw) == expected

    def test_sanitize_empty_returns_empty(self):
        assert _nick_resolver._sanitize("") == ""
        assert _nick_resolver._sanitize("!!!") == ""

    def test_sanitize_clips_to_max(self):
        assert _nick_resolver._sanitize("a" * 100) == "a" * 14
