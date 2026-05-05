"""Tests for the shared console helpers extracted from culture.cli.mesh."""

from __future__ import annotations

from unittest.mock import patch

from culture.cli.shared.console_helpers import resolve_console_nick, resolve_server


def test_resolve_server_returns_none_when_no_servers():
    with patch("culture.cli.shared.console_helpers.list_servers", return_value=[]):
        assert resolve_server(None) is None


def test_resolve_server_named_with_known_port():
    with patch("culture.cli.shared.console_helpers.read_port", return_value=7000):
        assert resolve_server("spark") == ("spark", 7000)


def test_resolve_server_unknown_name_returns_none():
    """An explicitly-named server that isn't running returns None.

    This is a behavior fix from the original mesh.py helper, which
    silently synthesized port 6667 — leading to confusing "wrong nick
    prefix" errors against an unrelated server. Returning None lets
    the caller surface a friendly "no such server" message.
    """
    with patch("culture.cli.shared.console_helpers.read_port", return_value=None):
        assert resolve_server("nope") is None


def test_resolve_server_single_server_picks_it():
    with patch(
        "culture.cli.shared.console_helpers.list_servers",
        return_value=[{"name": "only", "port": 6700}],
    ):
        assert resolve_server(None) == ("only", 6700)


def test_resolve_server_default_match_wins():
    servers = [
        {"name": "a", "port": 6700},
        {"name": "b", "port": 6701},
    ]
    with (
        patch("culture.cli.shared.console_helpers.list_servers", return_value=servers),
        patch("culture.cli.shared.console_helpers.read_default_server", return_value="b"),
    ):
        assert resolve_server(None) == ("b", 6701)


def test_resolve_server_multi_no_default_emits_hint(capsys):
    """When there's no default and multiple servers, pick first + warn."""
    servers = [
        {"name": "a", "port": 6700},
        {"name": "b", "port": 6701},
    ]
    with (
        patch("culture.cli.shared.console_helpers.list_servers", return_value=servers),
        patch("culture.cli.shared.console_helpers.read_default_server", return_value=None),
    ):
        assert resolve_server(None) == ("a", 6700)
    captured = capsys.readouterr()
    assert "no default" in captured.err
    assert "'a'" in captured.err
    assert "b" in captured.err
    assert "culture server default" in captured.err


def test_resolve_console_nick_uses_user_env_when_git_fails():
    fake_run = type("R", (), {"returncode": 1, "stdout": ""})()
    with (
        patch("culture.cli.shared.console_helpers.subprocess.run", return_value=fake_run),
        patch.dict("os.environ", {"USER": "ada"}, clear=False),
    ):
        assert resolve_console_nick() == "ada"


def test_resolve_console_nick_sanitizes_git_name():
    fake_run = type("R", (), {"returncode": 0, "stdout": "Ada Lovelace!\n"})()
    with patch("culture.cli.shared.console_helpers.subprocess.run", return_value=fake_run):
        assert resolve_console_nick() == "ada-lovelace"
