"""Unified CLI entry point for culture.

Commands are organized into noun-based groups:
    culture agent    {create,join,start,stop,status,rename,assign,sleep,wake,learn,message,read,archive,unarchive,delete}
    culture chat     {start,stop,status,default,rename,archive,unarchive,restart,link,logs,version,serve}
    culture mesh     {overview,setup,update,console}
    culture channel  {list,read,message,who}

    Deprecated (9.x; removed in 10.0):
    culture server   — alias for `culture chat` with a stderr warning
    culture bot      {create,start,stop,list,inspect,archive,unarchive}
    culture skills   {install}
    culture devex    {...developer-experience passthrough (powered by agex-cli)...}
    culture afi      {...agent-first interface passthrough (powered by afi-cli)...}

Universal verbs (available at the root):
    culture explain [topic]    full description of topic (default: culture)
    culture overview [topic]   shallow summary
    culture learn [topic]      agent-facing onboarding prompt
"""

from __future__ import annotations

import argparse
import logging
import sys

from culture import __version__
from culture.cli import afi, agent, bot, channel, chat, devex, introspect, mesh, server, skills

GROUPS = [agent, chat, server, mesh, channel, bot, skills, devex, afi, introspect]


def _names_of(group) -> set[str]:
    names = getattr(group, "NAMES", None)
    if names is not None:
        return set(names)
    return {group.NAME}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="culture",
        description="culture — AI agent IRC mesh",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")
    for group in GROUPS:
        group.register(sub)
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        for group in GROUPS:
            if args.command in _names_of(group):
                group.dispatch(args)
                return
        parser.print_help()
        sys.exit(1)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
