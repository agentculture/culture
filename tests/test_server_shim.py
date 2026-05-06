"""Pin the `culture server` command tree shape.

Two halves:

1. **Culture-owned verbs** (`start`, `stop`, `status`, `default`,
   `rename`, `archive`, `unarchive`) dispatch to culture's own handlers.
   Their `--help` output is culture's argparse prose, not agentirc's.

2. **Forwarded verbs** (`restart`, `link`, `logs`, `version`, `serve`)
   pass through to `agentirc.cli.dispatch` verbatim. The simplest stable
   assertion: invoking `culture server <verb>` and `agentirc <verb>`
   produces the same exit code for the same args (and the version
   command prints the same string).

`culture chat` was removed in 10.0.0 — it should not be a recognized
subcommand anymore.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

CULTURE = [sys.executable, "-m", "culture", "server"]
# Invoke via `python -m agentirc` rather than the `agentirc` console
# script so the test works in environments where entry-point scripts
# aren't on PATH (CI containers, Windows, isolated venvs).
AGENTIRC = [sys.executable, "-m", "agentirc"]

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


def test_server_top_level_help_lists_every_verb() -> None:
    rc, out, _ = _run([*CULTURE, "--help"])
    assert rc == 0
    for verb in (*CULTURE_OWNED_VERBS, *FORWARDED_VERBS):
        assert verb in out, f"verb {verb!r} missing from `culture server --help`"


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
    """`culture server version` and `agentirc version` print the same line."""
    cul_rc, cul_out, _ = _run([*CULTURE, "version"])
    agent_rc, agent_out, _ = _run([*AGENTIRC, "version"])
    assert cul_rc == agent_rc == 0
    assert cul_out.strip() == agent_out.strip(), (
        "culture server version drifted from agentirc version: " f"{cul_out!r} vs {agent_out!r}"
    )


@pytest.mark.parametrize("verb", FORWARDED_VERBS)
def test_forwarded_verb_help_passes_through(verb: str) -> None:
    """`culture server <forwarded-verb> --help` must dispatch to agentirc
    and exit 0 with agentirc's help banner — not error out at culture's
    root parser. Regression for #332: argparse's REMAINDER subparser was
    leaking `--help` back to the root, producing
    `culture: error: unrecognized arguments: --help`.
    """
    rc, out, err = _run([*CULTURE, verb, "--help"])
    combined = (out + "\n" + err).lower()
    assert (
        "unknown server command" not in combined
    ), f"forwarded verb {verb!r} not reaching agentirc.cli.dispatch"
    assert rc == 0, f"culture server {verb} --help exited {rc}: stderr={err!r}"
    assert (
        "unrecognized arguments" not in err.lower()
    ), f"culture server {verb} --help leaked --help to root parser: {err!r}"
    assert (
        f"agentirc {verb}" in out
    ), f"culture server {verb} --help output did not look like agentirc help: {out!r}"


def test_culture_chat_is_removed() -> None:
    """`culture chat` was removed in 10.0.0 — argparse should reject it."""
    proc = subprocess.run(
        [sys.executable, "-m", "culture", "chat", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    # argparse exits 2 on unrecognized subcommand.
    assert proc.returncode != 0
    assert "invalid choice" in proc.stderr.lower() or "chat" in proc.stderr.lower()
