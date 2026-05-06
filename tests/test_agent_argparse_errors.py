"""Tests for argparse-style error formatting on culture agent verbs (#333).

Two micro-fixes:

* **Item 12** — `culture agent sleep` (and `wake`, etc.) routed
  missing-arg errors through `print(..., file=stderr) + sys.exit(1)`
  while every other CLI verb used argparse's standard
  ``<prog>: error: ...`` formatter on stderr with rc 2. Now they all
  match.
* **Item 11** — `culture agent message` to an unknown nick framed the
  failure as if local config were the source of truth ("Agent 'X' not
  found in config"), which is wrong on a federated mesh. The new
  message points at `culture channel who #general` for a live view.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from culture.cli import agent


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


class _FakeConfig:
    """Minimal stand-in for the loaded culture config."""

    def __init__(self, agents: list):
        self.agents = list(agents)

    def get_agent(self, _nick):
        return None  # tests only need this for the DM path


# ---------------------------------------------------------------------------
# Item 12 — agent sleep / wake argparse error formatting
# ---------------------------------------------------------------------------


def test_sleep_no_args_uses_argparse_error_format(capsys):
    config = _FakeConfig(agents=[])
    args = _ns(nick=None, all=False)

    with pytest.raises(SystemExit) as ei:
        agent._resolve_ipc_targets(config, args, "sleep")

    assert ei.value.code == 2, "argparse uses rc 2 for usage errors"
    err = capsys.readouterr().err
    assert "culture agent sleep" in err
    assert "error:" in err.lower()
    assert "required" in err.lower()


def test_sleep_both_nick_and_all_uses_argparse_error_format(capsys):
    config = _FakeConfig(agents=[])
    args = _ns(nick="spark-claude", all=True)

    with pytest.raises(SystemExit) as ei:
        agent._resolve_ipc_targets(config, args, "sleep")

    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "culture agent sleep" in err
    assert "cannot specify both" in err


def test_sleep_missing_nick_writes_to_stderr_not_stdout(capsys):
    """Regression for #333 item 12: usage errors must not go to stdout."""
    config = _FakeConfig(agents=[])
    args = _ns(nick=None, all=False)

    with pytest.raises(SystemExit):
        agent._resolve_ipc_targets(config, args, "sleep")

    captured = capsys.readouterr()
    assert captured.out == "", "usage errors must not pollute stdout"
    assert captured.err, "usage errors must reach stderr"


def test_sleep_unknown_nick_uses_argparse_error_format(capsys):
    config = _FakeConfig(agents=[])
    args = _ns(nick="spark-ghost", all=False)

    with pytest.raises(SystemExit) as ei:
        agent._resolve_ipc_targets(config, args, "sleep")

    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "culture agent sleep" in err
    assert "spark-ghost" in err
    assert "not found" in err


# ---------------------------------------------------------------------------
# Item 11 — agent message DM error reframe
# ---------------------------------------------------------------------------


def test_message_to_unknown_agent_points_at_live_mesh(monkeypatch, capsys):
    """The DM error for an unknown nick must hint at `culture channel who`,
    not stake its claim on the local config alone (#333 item 11)."""

    monkeypatch.setattr(
        agent,
        "load_config_or_default",
        lambda _path: _FakeConfig(agents=[]),
    )
    args = _ns(target="spark-nonexistent", text="hi", config="~/.culture/server.yaml")

    with pytest.raises(SystemExit) as ei:
        agent._cmd_message(args)

    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "spark-nonexistent" in err
    # The new framing must explicitly mention the live source of truth.
    assert "culture channel who" in err
    # And must not pretend local config is the only source of truth.
    assert "not found in config" not in err.lower() or "stale" in err.lower()


# ---------------------------------------------------------------------------
# CLI integration smoke tests for the sleep behavior change
# ---------------------------------------------------------------------------


def test_sleep_no_args_exits_2_through_real_cli():
    """End-to-end: invoking `culture agent sleep` with no args returns rc 2."""
    proc = subprocess.run(
        [sys.executable, "-m", "culture", "agent", "sleep"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 2, (
        f"expected argparse-style rc 2, got {proc.returncode}: " f"stderr={proc.stderr!r}"
    )
    assert "error:" in proc.stderr.lower()
