"""Unit tests for `culture.cli.console._resolve_argv`.

Pure-function tests: argv in, argv out. No subprocess, no IRC.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from culture.cli import console


@pytest.fixture
def mock_resolvers():
    """Pin _resolve_server / _resolve_console_nick to deterministic values."""
    with (
        patch.object(console, "_resolve_server", return_value=("spark", 6667)),
        patch.object(console, "_resolve_console_nick", return_value="ada"),
    ):
        yield


def test_irc_lens_verb_passes_through_unchanged(mock_resolvers):
    for verb in ("learn", "explain", "overview", "serve", "cli"):
        assert console._resolve_argv([verb]) == [verb]


def test_leading_flag_passes_through_unchanged(mock_resolvers):
    assert console._resolve_argv(["--help"]) == ["--help"]
    assert console._resolve_argv(["--version"]) == ["--version"]
    assert console._resolve_argv(["-h"]) == ["-h"]


def test_irc_lens_verb_with_tail_passes_through(mock_resolvers):
    argv = ["serve", "--host", "remote.example", "--nick", "lens"]
    assert console._resolve_argv(argv) == argv


def test_empty_argv_builds_serve_with_default_server(mock_resolvers):
    assert console._resolve_argv([]) == [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "6667",
        "--nick",
        "spark-ada",
    ]


def test_server_name_rewrites_to_serve(mock_resolvers):
    assert console._resolve_argv(["spark"]) == [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "6667",
        "--nick",
        "spark-ada",
    ]


def test_server_name_with_extra_flags_appended(mock_resolvers):
    assert console._resolve_argv(["spark", "--open"]) == [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "6667",
        "--nick",
        "spark-ada",
        "--open",
    ]


def test_no_running_servers_raises_systemexit_with_hint():
    with (
        patch.object(console, "_resolve_server", return_value=None),
        patch.object(console, "_resolve_console_nick", return_value="ada"),
    ):
        with pytest.raises(SystemExit) as excinfo:
            console._resolve_argv([])
        assert "No culture servers running" in str(excinfo.value)
        assert "culture server start" in str(excinfo.value)


def test_unknown_named_server_raises_friendly_systemexit():
    """`culture console nope` (no such server) should bail with a hint, not
    silently connect to localhost:6667 and confuse the user with a
    nick-prefix error from the wrong server."""
    with (
        patch.object(console, "_resolve_server", return_value=None),
        patch.object(console, "_resolve_console_nick", return_value="ada"),
    ):
        with pytest.raises(SystemExit) as excinfo:
            console._resolve_argv(["nope"])
        msg = str(excinfo.value)
        assert "'nope'" in msg
        assert "culture server status" in msg


def test_bare_help_token_is_treated_as_help_flag(mock_resolvers):
    """`culture console help` (a common typo) prints help, not 'no such server'."""
    assert console._resolve_argv(["help"]) == ["--help"]


def test_double_dash_separator_is_stripped(mock_resolvers):
    """`culture console -- spark --open` should behave like `culture console spark --open`."""
    assert console._resolve_argv(["--", "spark", "--open"]) == [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "6667",
        "--nick",
        "spark-ada",
        "--open",
    ]


def test_double_dash_alone_falls_back_to_default(mock_resolvers):
    assert console._resolve_argv(["--"]) == [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "6667",
        "--nick",
        "spark-ada",
    ]


def test_user_nick_override_wins_via_argparse_lastwins(mock_resolvers):
    # Documents that --nick after the shim's --nick is the supported
    # override path: argparse's last-wins semantics make this work.
    argv = console._resolve_argv(["spark", "--nick", "override"])
    assert argv[-2:] == ["--nick", "override"]
    assert "spark-ada" in argv  # shim still injects its value first
