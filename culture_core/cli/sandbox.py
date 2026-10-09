"""Sandbox subcommands: ``culture sandbox agent`` (guest-mode sbx-ask)."""

from __future__ import annotations

import argparse
import sys

NAME = "sandbox"


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("sandbox", help="Guest-mode sandbox components")
    sub = p.add_subparsers(dest="sandbox_command")
    agent = sub.add_parser(
        "agent", help="Run the tool-less sbx-ask guest Q&A agent", add_help=False
    )
    agent.add_argument("argv", nargs=argparse.REMAINDER)


def dispatch(args: argparse.Namespace) -> None:
    if getattr(args, "sandbox_command", None) != "agent":
        print("Usage: culture sandbox agent [--config FILE] [--bundle-from REPO]", file=sys.stderr)
        sys.exit(1)
    from culture_core.sandbox.agent import main

    main(list(args.argv))
