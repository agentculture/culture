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
import asyncio
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


def test_message_to_unknown_agent_attempts_send_not_local_config_error(monkeypatch, capsys):
    """`culture agent message` must not short-circuit on local-config absence.

    Issue #333 item 11: the IRC server (with federation) is the source of
    truth for who is reachable on the mesh. The previous behavior errored
    out when the target nick wasn't in the local ``server.yaml`` even
    though that agent might be present on a federated peer. The new
    behavior delegates the existence check to the server: send goes
    through, and the IRC ``401 NOSUCHNICK`` numeric propagates if the
    nick truly isn't there.
    """
    sent: list[tuple[str, str]] = []

    class _FakeObserver:
        async def send_message(self, target: str, text: str) -> None:
            # `async def` is required because the real send_message is
            # awaited by the caller via `asyncio.run(...)`. The trivial
            # `await asyncio.sleep(0)` keeps the body honest about being
            # async (and silences SonarCloud's python:S7503).
            await asyncio.sleep(0)
            sent.append((target, text))

    monkeypatch.setattr(
        agent,
        "load_config_or_default",
        lambda _path: _FakeConfig(agents=[]),
    )
    monkeypatch.setattr(agent, "get_observer", lambda _path: _FakeObserver())
    args = _ns(target="spark-federated-peer", text="hi", config="~/.culture/server.yaml")

    # No early SystemExit — the send goes through despite local config
    # not knowing about the target.
    agent._cmd_message(args)

    assert sent == [("spark-federated-peer", "hi")]
    out = capsys.readouterr().out
    assert "Sent to spark-federated-peer" in out


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
    assert (
        proc.returncode == 2
    ), f"expected argparse-style rc 2, got {proc.returncode}: stderr={proc.stderr!r}"
    assert "error:" in proc.stderr.lower()
