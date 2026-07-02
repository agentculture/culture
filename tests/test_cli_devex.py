"""Tests for `culture devex` passthrough and `devex` universal topic."""

import subprocess
import sys


def test_culture_devex_version_runs():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "devex", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # agex --version prints just the version string (e.g. "0.13.0")
    assert result.stdout.strip()
    assert all(c.isdigit() or c == "." for c in result.stdout.strip())


def test_culture_devex_explain_agex():
    # The underlying agex library still refers to itself as "agex"; the
    # passthrough forwards arguments verbatim to the library.
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "devex", "explain", "agex"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_devex_help_shows_typer_help():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "devex", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    # typer help contains "Usage:" and lists top-level commands
    assert "Usage:" in result.stdout or "usage:" in result.stdout.lower()


def test_culture_explain_devex_via_universal_verb():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "explain", "devex"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
