"""Sandbox subcommands: ``culture sandbox agent`` (guest-mode sbx-ask)."""

from __future__ import annotations

import argparse
import sys

NAME = "sandbox"


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("sandbox", help="Guest-mode sandbox components")
    sub = p.add_subparsers(dest="sandbox_command")
    agent = sub.add_parser("agent", help="Run the tool-less sbx-ask guest Q&A agent")
    # Declared here (not argparse.REMAINDER): REMAINDER does not capture
    # leading --options inside a subparser, so the documented flags failed.
    agent.add_argument("--config", help="YAML sandbox config (see docs/sandbox-agent.md)")
    agent.add_argument(
        "--bundle-from", metavar="REPO", help="(re)build the knowledge bundle from REPO, then exit"
    )


def dispatch(args: argparse.Namespace) -> None:
    if getattr(args, "sandbox_command", None) != "agent":
        print("Usage: culture sandbox agent [--config FILE] [--bundle-from REPO]", file=sys.stderr)
        sys.exit(1)
    from culture_core.sandbox.agent import main

    argv: list[str] = []
    if args.config:
        argv += ["--config", args.config]
    if args.bundle_from:
        argv += ["--bundle-from", args.bundle_from]
    main(argv)
