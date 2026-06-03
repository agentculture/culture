"""Qodo PR #51 #2 — ``python -m culture.clients.bridge`` nick validation.

The CLI wrapper in ``culture/cli/bridge.py`` validates nick shape at
its boundary, but the module entry point in
``culture/clients/bridge/__main__.py`` is also a public entry path —
a user (or a misconfigured CI script) could call
``python -m culture.clients.bridge start <whatever>`` directly. The
``<server>-<agent>`` rule (Rule 428343) must apply at BOTH gates.
"""

from __future__ import annotations

import subprocess
import sys

import pytest


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Invoke ``python -m culture.clients.bridge`` with *args* and
    capture stdout/stderr without spawning a real daemon (we always
    pass an invalid nick so the validator fires before any IRC or
    socket setup runs)."""
    return subprocess.run(
        [sys.executable, "-m", "culture.clients.bridge", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestMainNickValidation:
    @pytest.mark.parametrize(
        "nick",
        [
            "ABC",  # no hyphen
            "single",  # no hyphen
            "x" * 65,  # too long
            # NOTE: leading-hyphen nicks (``-foo``) are rejected by argparse
            # BEFORE reaching our validator — they look like CLI flags. That
            # also produces a non-zero exit + no daemon spawn, which is the
            # actual security property we care about (see
            # test_argparse_rejects_hyphen_prefixed_nick below).
            "trailing-",
            "has space",
            "has;rm",
            "has/slash",
            "has\\backslash",
        ],
    )
    def test_invalid_nick_rejected_with_clear_error(self, nick: str) -> None:
        result = _run(["start", nick])
        assert result.returncode == 1, (
            f"expected exit 1 for {nick!r}, got {result.returncode}; " f"stderr={result.stderr!r}"
        )
        # Error message names the actual nick + the rule.
        assert (
            nick in result.stderr or "invalid nick" in result.stderr.lower()
        ), f"error message did not mention {nick!r} or 'invalid nick': {result.stderr!r}"

    def test_no_command_prints_help_and_exits_nonzero(self) -> None:
        result = _run([])
        assert result.returncode == 1

    def test_empty_nick_rejected(self) -> None:
        # argparse rejects this before our validator (positional missing),
        # but the contract is: NO daemon spawn for missing/invalid nick.
        result = _run(["start"])
        assert result.returncode != 0

    def test_argparse_rejects_hyphen_prefixed_nick(self) -> None:
        """A nick like ``-foo`` is intercepted by argparse as an
        unknown option flag BEFORE our validator runs. Either path
        produces a non-zero exit + no daemon spawn — that's the
        property we actually need."""
        result = _run(["start", "-foo"])
        assert result.returncode != 0
