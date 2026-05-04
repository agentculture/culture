"""Pin the `culture chat` command tree shape after Phase A3.

Two halves:

1. **Culture-owned verbs** (`start`, `stop`, `status`, `default`,
   `rename`, `archive`, `unarchive`) dispatch to culture's own handlers.
   Their `--help` output is culture's argparse prose, not agentirc's.

2. **Forwarded verbs** (`restart`, `link`, `logs`, `version`, `serve`)
   pass through to `agentirc.cli.dispatch` verbatim. The simplest stable
   assertion: invoking `culture chat <verb>` and `agentirc <verb>`
   produces the same exit code for the same args (and the version
   command prints the same string).
"""

from __future__ import annotations

import subprocess
import sys

import pytest

CULTURE = [sys.executable, "-m", "culture", "chat"]
AGENTIRC = ["agentirc"]

CULTURE_OWNED_VERBS = (
    "start",
    "stop",
    "status",
    "default",
    "rename",
    "archive",
    "unarchive",
)
FORWARDED_VERBS = ("restart", "link", "logs", "version", "serve")


def _run(cmd: list[str]) -> tuple[int, str, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return proc.returncode, proc.stdout, proc.stderr


def test_chat_top_level_help_lists_every_verb() -> None:
    rc, out, _ = _run([*CULTURE, "--help"])
    assert rc == 0
    for verb in (*CULTURE_OWNED_VERBS, *FORWARDED_VERBS):
        assert verb in out, f"verb {verb!r} missing from `culture chat --help`"


@pytest.mark.parametrize("verb", CULTURE_OWNED_VERBS)
def test_culture_owned_verb_help_dispatches_locally(verb: str) -> None:
    """Culture-owned verbs print culture's own argparse help, not agentirc's."""
    rc, out, _ = _run([*CULTURE, verb, "--help"])
    assert rc == 0
    # argparse usage line is the canonical signal that this is python's
    # argparse output (i.e. culture-side), not agentirc-cli's renderer.
    assert "usage:" in out
    assert verb in out


def test_forwarded_version_matches_agentirc() -> None:
    """`culture chat version` and `agentirc version` print the same line."""
    cul_rc, cul_out, _ = _run([*CULTURE, "version"])
    agent_rc, agent_out, _ = _run([*AGENTIRC, "version"])
    assert cul_rc == agent_rc == 0
    assert cul_out.strip() == agent_out.strip(), (
        "culture chat version drifted from agentirc version: " f"{cul_out!r} vs {agent_out!r}"
    )


@pytest.mark.parametrize("verb", FORWARDED_VERBS)
def test_forwarded_verb_is_reachable(verb: str) -> None:
    """Each forwarded verb should be reachable; --help for those that
    accept it should exit 0. agentirc-owned argparse may not accept
    `--help` for every verb, so we only assert the verb is reachable
    (no `Unknown chat command` error)."""
    rc, out, err = _run([*CULTURE, verb, "--help"])
    combined = (out + "\n" + err).lower()
    assert (
        "unknown chat command" not in combined
    ), f"forwarded verb {verb!r} not reaching agentirc.cli.dispatch"
