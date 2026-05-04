"""Deprecation alias: ``culture server <verb>`` → ``culture chat <verb>``.

The canonical noun for the IRC mesh is :mod:`culture.cli.chat` (Phase A3
of the agentirc extraction, culture 9.0.0). This module is a thin
forwarder so existing scripts, skills, and agent prompts that say
``culture server`` keep working through the 9.x line. Removed in 10.0.

The alias prints a one-line warning to stderr at dispatch time, then
hands the parsed args off to :func:`culture.cli.chat.dispatch_verb`
unchanged — every verb works identically, only the noun in the help
text differs.
"""

from __future__ import annotations

import argparse
import sys

from culture.cli import chat

NAME = "server"

_DEPRECATION_WARNING = (
    "warning: 'culture server' is renamed to 'culture chat'; "
    "update your scripts/skills (will be removed in culture 10.0.0)"
)


def register(subparsers: argparse._SubParsersAction) -> None:
    chat.register_verbs(
        subparsers,
        parser_name="server",
        parser_help="(deprecated alias for 'culture chat')",
        dest_attr="server_command",
    )


def dispatch(args: argparse.Namespace) -> None:
    print(_DEPRECATION_WARNING, file=sys.stderr)
    chat.dispatch_verb(
        getattr(args, "server_command", None),
        args,
        noun="server",
    )
