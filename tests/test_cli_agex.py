"""Tests for `culture agex` passthrough and `agex` universal topic."""

import subprocess
import sys


def test_culture_agex_version_runs():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "agex", "--version"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    # agex --version prints just the version string (e.g. "0.13.0")
    assert result.stdout.strip()
    assert all(c.isdigit() or c == "." for c in result.stdout.strip())


def test_culture_agex_explain_agex():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "agex", "explain", "agex"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_agex_help_shows_typer_help():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "agex", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    # typer help contains "Usage:" and lists top-level commands
    assert "Usage:" in result.stdout or "usage:" in result.stdout.lower()


def test_culture_explain_agex_via_universal_verb():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "explain", "agex"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()
