"""`culture afi` — passthrough to the standalone agentfront CLI.

agentfront (Agent First Interface) scaffolds and audits agent-first CLIs, MCP
servers, and HTTP sites. Culture embeds it as a first-class namespace so
the culture CLI exposes the same agent-first affordances agentfront enforces.

This module is a thin adapter: it supplies a package-specific ``Entry``
callable and wires the three universal verbs
(``explain`` / ``overview`` / ``learn``) through
:mod:`culture_core.cli._passthrough`. agentfront already implements the agent-first
CLI contract (``main(argv) -> int``), so the entry is a direct
delegation — no typer adapter needed.

``agentfront`` owns the rubric the contract is measured against. See
`agentculture/agentfront#5 <https://github.com/agentculture/agentfront/issues/5>`_
for the tracking issue that adds an ``overview`` verb + rubric bundle so
agentfront models every check it enforces.
"""

from __future__ import annotations

import argparse
import sys

from culture_core.cli import _passthrough

NAME = "afi"


def _entry(argv: list[str]) -> "int | None":
    """In-process call into ``agentfront.cli.main(argv)``.

    agentfront's ``main`` returns an ``int`` on normal completion and raises
    ``SystemExit`` only for argparse-level exits (``--help``, ``--version``,
    unknown flag). Both paths are handled by :mod:`culture_core.cli._passthrough`.
    """
    try:
        from agentfront.cli import main
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"agentfront is not installed: {exc}", file=sys.stderr)
        sys.exit(2)
    return main(argv)


_passthrough.register_topic(
    "afi",
    _entry,
    explain_argv=["explain"],
    overview_argv=["overview"],
    learn_argv=["learn"],
)


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    # prefix_chars=chr(0): every token (including --help, --version) is
    # treated as positional and captured in afi_args for the underlying
    # agentfront argparse parser to handle.
    p = subparsers.add_parser(
        NAME,
        help="Run the agentfront agent-first CLI via passthrough",
        add_help=False,
        prefix_chars=chr(0),
    )
    p.add_argument("afi_args", nargs=argparse.REMAINDER, help="Arguments passed to agentfront")


def dispatch(args: argparse.Namespace) -> None:
    _passthrough.run(_entry, list(getattr(args, "afi_args", []) or []))
