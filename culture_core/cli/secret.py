"""`culture secret` — passthrough to the standalone grant secrets manager.

grant (agentculture/grant, PyPI ``grant``) is the mesh's secrets manager and
replaces shushu. ``culture secret <verb>`` is exactly ``grant <verb>``: the
arguments are forwarded verbatim to ``grant.cli.main(argv)`` through
:mod:`culture_core.cli._passthrough`, so output, refusals (for example
``get`` on a hidden secret) and exit codes are grant's own.

grant keeps a per-OS-user store under ``$HOME/.local/share/grant``.
"""

from __future__ import annotations

import argparse
import sys

from culture_core.cli import _passthrough

NAME = "secret"


def _entry(argv: list[str]) -> "int | None":
    """In-process call into ``grant.cli.main(argv)``."""
    try:
        from grant.cli import main
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"grant is not installed: {exc}", file=sys.stderr)
        sys.exit(2)
    return main(argv)


_passthrough.register_topic(
    "secret",
    _entry,
    # grant's `explain` needs a topic (a verb or concept); its `learn` is the
    # whole-tool self-description, which is what `culture explain secret` wants.
    explain_argv=["learn"],
    overview_argv=["overview"],
    learn_argv=["learn"],
)


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    # prefix_chars=chr(0): every token (including --help, --version) is
    # captured positionally and handled by grant's own argparse parser.
    p = subparsers.add_parser(
        NAME,
        help="Run the grant secrets manager via passthrough",
        add_help=False,
        prefix_chars=chr(0),
    )
    p.add_argument("secret_args", nargs=argparse.REMAINDER, help="Arguments passed to grant")


def dispatch(args: argparse.Namespace) -> None:
    _passthrough.run(_entry, list(getattr(args, "secret_args", []) or []))
