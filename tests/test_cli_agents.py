"""Tests for the unified `culture agents` noun (replaces singular `culture agent`)."""

from __future__ import annotations

import argparse
import subprocess
import sys


def _top_choices() -> set[str]:
    from culture.cli import _build_parser

    parser = _build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return set(sub.choices)


def test_agents_noun_is_registered():
    assert "agents" in _top_choices()


def test_singular_agent_noun_is_removed():
    assert "agent" not in _top_choices()


def test_culture_agent_singular_is_rejected_at_runtime():
    result = subprocess.run(
        [sys.executable, "-m", "culture", "agent", "status"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr.lower()


def test_culture_agents_status_parses():
    # `status` needs no daemon to parse; --help exits 0 after printing usage.
    result = subprocess.run(
        [sys.executable, "-m", "culture", "agents", "status", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
