"""Tests for `culture console` passthrough and `console` universal topic.

Mirrors tests/test_cli_devex.py / test_cli_afi.py. These shell out via
`python -m culture` to exercise the registered argparse group end-to-end.
"""

from __future__ import annotations

import subprocess
import sys


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "culture", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_culture_console_help_shows_irc_lens_help():
    result = _run("console", "--help")
    assert result.returncode == 0, result.stderr
    out = (result.stdout + result.stderr).lower()
    assert "irc-lens" in out
    assert "usage:" in out


def test_culture_console_version_runs():
    result = _run("console", "--version")
    assert result.returncode == 0, result.stderr
    # argparse `version` action prints `<prog> <version>` by default.
    assert result.stdout.strip().startswith("irc-lens ")


def test_culture_console_explain_runs():
    result = _run("console", "explain")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_console_learn_runs():
    result = _run("console", "learn")
    assert result.returncode == 0, result.stderr
    assert "irc-lens" in result.stdout


def test_culture_explain_console_via_universal_verb():
    result = _run("explain", "console")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_overview_console_via_universal_verb():
    result = _run("overview", "console")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_learn_console_via_universal_verb():
    result = _run("learn", "console")
    assert result.returncode == 0, result.stderr
    assert "irc-lens" in result.stdout
