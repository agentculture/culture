"""Tests for `culture afi` passthrough and `afi` universal topic."""

import subprocess
import sys


def test_culture_afi_version_runs():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "afi", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # agentfront --version prints "agentfront X.Y.Z"
    assert result.stdout.strip().startswith("agentfront ")


def test_culture_afi_explain_runs():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "afi", "explain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_afi_learn_runs():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "afi", "learn"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_afi_overview_runs():
    # agentfront 0.20+ exposes `overview`; our pin guarantees it.
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "afi", "overview"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_afi_help_shows_argparse_help():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "afi", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    # afi uses argparse; help text contains the usage banner
    assert "usage:" in result.stdout.lower()


def test_culture_explain_afi_via_universal_verb():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "explain", "afi"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_learn_afi_via_universal_verb():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "learn", "afi"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_overview_afi_via_universal_verb():
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "overview", "afi"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def test_culture_explain_lists_afi_as_registered():
    # When afi registers handlers, `culture explain` should no longer show it
    # as "(coming soon)".
    result = subprocess.run(
        [sys.executable, "-m", "culture_core", "explain"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = [ln for ln in result.stdout.splitlines() if "`culture afi`" in ln]
    assert lines, "afi line missing from `culture explain` namespaces list"
    # The line must not carry the "(coming soon)" marker
    assert "coming soon" not in lines[0], lines[0]
