"""`culture devex` — passthrough to the standalone agex CLI.

Under the hood, ``devex`` is powered by the standalone ``agex-cli``
(``agent_experience`` package). The command name differs for familiarity
with the developer-experience vocabulary; the underlying tool is the
same.

This module is a thin adapter: it supplies a package-specific ``Entry``
callable and wires the three universal verbs
(``explain`` / ``overview`` / ``learn``) through
:mod:`culture_core.cli._passthrough`. When agex-cli migrates to the
agent-first CLI contract (``main(argv) -> int``), the adapter becomes a
one-line ``return main(argv)`` without touching the shared plumbing.
"""

from __future__ import annotations

import argparse
import sys

from culture_core.cli import _passthrough

NAME = "devex"


def _entry(argv: list[str]) -> None:
    """In-process call into the agex typer ``app``.

    Typer's default ``standalone_mode=True`` makes ``app`` call ``sys.exit``
    when it finishes — so this function naturally raises ``SystemExit``,
    which :mod:`culture_core.cli._passthrough` translates into an ``int`` for the
    universal-verb handlers and re-emits via ``sys.exit`` for the direct
    passthrough path.
    """
    try:
        from agent_experience.cli import app
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"agex-cli is not installed: {exc}", file=sys.stderr)
        sys.exit(2)
    app(args=argv)


# The underlying agex library refers to itself as "agex"; we pass that to
# its own explain verb. The culture-facing name is devex.
_passthrough.register_topic(
    "devex",
    _entry,
    explain_argv=["explain", "agex"],
    overview_argv=["overview", "--agent", "claude-code"],
    learn_argv=["learn", "--agent", "claude-code"],
)


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    # prefix_chars=chr(0) means the devex subparser has no recognized flag
    # prefix character, so every token (including --help, --version) is
    # treated as positional and captured in devex_args for typer to handle.
    p = subparsers.add_parser(
        NAME,
        help="Run the agex developer-experience CLI via passthrough",
        add_help=False,
        prefix_chars=chr(0),
    )
    p.add_argument("devex_args", nargs=argparse.REMAINDER, help="Arguments passed to agex")


def dispatch(args: argparse.Namespace) -> None:
    _passthrough.run(_entry, list(getattr(args, "devex_args", []) or []))
