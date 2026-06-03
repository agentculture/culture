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


@pytest.fixture(autouse=True)
def _pin_server_name(monkeypatch):
    """Pin ``_server_name`` to ``local`` so every test in this file
    runs with a known prefix regardless of whether the developer's
    ``~/.culture/server.yaml`` exists or what it contains.

    Without this, ``test_long_bare_candidate_clipped_then_prefixed``
    et al. would resolve the prefix from the real user-scope yaml on
    a dev box and ``culture`` on a clean CI runner — failing
    intermittently. (Qodo PR #54 #4 highlighted the underlying
    inconsistency.)"""
    monkeypatch.setattr(_nick_resolver, "_server_name", lambda: "local")


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
        """A long env value is clipped THEN prefixed with the server
        name so the total still fits in ``_MAX_LEN`` and is valid
        ``<server>-<agent>`` shape (Rule 428343)."""
        monkeypatch.setenv("CULTURE_BOSS_NICK", "abcdefghijklmnopqrstuvwxyz")
        out = resolve_project_nick(str(isolated_cwd))
        assert len(out) <= 14
        # ``local-`` (6 chars) + 8 char agent budget = 14 total.
        assert out == "local-abcdefgh"

    def test_too_short_env_falls_through_to_d(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CULTURE_BOSS_NICK", "ab")
        cwd = tmp_path / "longer-name"
        cwd.mkdir()
        # ``longer-name`` already has a hyphen, so it's qualified as-is.
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
        # Falls through to (d) cwd basename → qualified with ``local-``
        # prefix and clipped to ``_MAX_LEN``. ``isolated_cwd``'s name
        # ``test_git_unavailable_falls_through`` is hyphen-free after
        # sanitization (underscores survive but no hyphen) so the
        # prefix path applies.
        # The autouse ``_pin_server_name`` fixture forces the prefix to
        # ``local`` regardless of the dataclass default, so the
        # observable output here uses ``local`` even after Qodo PR #54
        # #4 swapped the constant to ``culture``.
        pinned_server = "local"
        agent_budget = _nick_resolver._MAX_LEN - len(pinned_server) - 1
        expected_agent = isolated_cwd.name.lower()[:agent_budget]
        assert resolve_project_nick(str(isolated_cwd)) == f"{pinned_server}-{expected_agent}"


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


class TestQualifyServerAgent:
    """v9.1.2: every resolver return must be ``<server>-<agent>``
    (Rule 428343). Bare names produced by sanitization are now
    qualified with the IRC server's name as a prefix; already-qualified
    names pass through unchanged."""

    def test_bare_candidate_gets_server_prefix(self):
        assert _nick_resolver._qualify("culture") == "local-culture"

    def test_already_qualified_candidate_passes_through(self):
        assert _nick_resolver._qualify("local-fork") == "local-fork"
        assert _nick_resolver._qualify("local-st4ck-boss") == "local-st4ck-boss"

    def test_long_bare_candidate_clipped_then_prefixed(self):
        # Agent budget = 14 - len("local-") = 8.
        assert _nick_resolver._qualify("abcdefghijklmnop") == "local-abcdefgh"
        assert len(_nick_resolver._qualify("abcdefghijklmnop")) == 14

    def test_default_server_name_matches_dataclass_default(self):
        """Qodo PR #54 #4: ``_DEFAULT_SERVER_NAME`` must match
        ``ServerConfig.name`` in ``culture/agentirc/config.py``
        (``"culture"``). A previous draft used ``"local"`` — the value
        that happens to be in the maintainer's dev yaml — which would
        silently fork the resolver's identity from the dataclass
        default on a fresh deployment."""
        assert _nick_resolver._DEFAULT_SERVER_NAME == "culture"


class TestServerNameReader:
    """Qodo PR #54 #2: ``_server_name`` must strip inline YAML
    comments. A line like ``name: spark  # human-friendly`` would
    otherwise produce ``spark  # human-friendly`` as the prefix and
    ``_qualify`` would build a nick containing ``#`` + spaces that the
    bridge CLI rejects with ``invalid nick`` — reintroducing the very
    silent-failure mode this whole PR is closing."""

    @pytest.fixture(autouse=True)
    def _unpin_server_name(self, monkeypatch):
        """Defeat the autouse ``_pin_server_name`` fixture for this
        class — we're exercising the real ``_server_name`` reader."""
        import importlib

        importlib.reload(_nick_resolver)

    def test_strips_inline_comment(self, tmp_path, monkeypatch):
        culture_home = tmp_path / ".culture"
        culture_home.mkdir()
        (culture_home / "server.yaml").write_text(
            "server:\n  name: spark  # human-friendly tag for the irc daemon\n"
        )
        monkeypatch.setattr(
            os.path,
            "expanduser",
            lambda p: p.replace("~", str(tmp_path), 1) if p.startswith("~") else p,
        )
        assert _nick_resolver._server_name() == "spark"

    def test_handles_quoted_value_with_comment(self, tmp_path, monkeypatch):
        culture_home = tmp_path / ".culture"
        culture_home.mkdir()
        (culture_home / "server.yaml").write_text(
            'server:\n  name: "spark"  # also handles quoted forms\n'
        )
        monkeypatch.setattr(
            os.path,
            "expanduser",
            lambda p: p.replace("~", str(tmp_path), 1) if p.startswith("~") else p,
        )
        assert _nick_resolver._server_name() == "spark"

    def test_missing_yaml_uses_dataclass_default(self, tmp_path, monkeypatch):
        # ~ resolves to a tmp dir with NO ~/.culture/server.yaml.
        monkeypatch.setattr(
            os.path,
            "expanduser",
            lambda p: p.replace("~", str(tmp_path), 1) if p.startswith("~") else p,
        )
        assert _nick_resolver._server_name() == "culture"

    def test_resolver_output_always_passes_bridge_validation(self, isolated_cwd, monkeypatch):
        """The output of every priority tier must be a valid
        ``<server>-<agent>`` nick — anything else triggers the v9.1.1
        bridge CLI validator's ``invalid nick`` exit-1.

        This is the load-bearing assertion for the fix. Pre-v9.1.2 the
        resolver would return ``culture`` for this repo and the bridge
        spawn would silently fail.
        """

        def _is_valid(nick: str) -> bool:
            parts = nick.split("-", 1)
            return len(parts) == 2 and all(parts)

        # (a) env var
        monkeypatch.setenv("CULTURE_BOSS_NICK", "culture")
        assert _is_valid(resolve_project_nick(str(isolated_cwd)))
        monkeypatch.delenv("CULTURE_BOSS_NICK", raising=False)

        # (b) culture.yaml
        (isolated_cwd / "culture.yaml").write_text("nick: culture\n")
        assert _is_valid(resolve_project_nick(str(isolated_cwd)))
        (isolated_cwd / "culture.yaml").unlink()

        # (d) cwd basename
        bare = isolated_cwd.parent / "culture"
        bare.mkdir(exist_ok=True)
        assert _is_valid(resolve_project_nick(str(bare)))
