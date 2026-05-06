"""Unified CLI entry point for culture.

Commands are organized into noun-based groups:
    culture agent    {create,join,start,stop,status,rename,assign,sleep,wake,learn,message,read,archive,unarchive,delete}
    culture server   {start,stop,status,default,rename,archive,unarchive,restart,link,logs,version,serve}
    culture console  {...irc-lens verbs and flags...}    # passthrough; reactive web console
    culture mesh     {overview,setup,update,console}     # `console` here is deprecated; use `culture console`
    culture channel  {list,read,message,who}
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
from culture.cli import (
    afi,
    agent,
    bot,
    channel,
    console,
    devex,
    introspect,
    mesh,
    server,
    skills,
)

GROUPS = [agent, server, mesh, channel, bot, skills, devex, afi, console, introspect]


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


def _maybe_forward_to_agentirc(argv: list[str]) -> int | None:
    """Bypass argparse for ``culture server <forwarded-verb> ...`` calls.

    Returns the exit code to propagate, or ``None`` if argparse should
    handle the invocation. argparse's ``REMAINDER`` parser cannot capture
    ``--help`` reliably (it leaks to the root parser as an unrecognized
    argument), so the forwarded surface is short-circuited here before
    argparse runs.
    """
    if len(argv) < 2 or argv[0] != "server":
        return None
    if argv[1] not in server._AGENTIRC_FORWARDED_VERBS:
        return None
    from agentirc.cli import dispatch as _agentirc_dispatch

    return _agentirc_dispatch(argv[1:])


def main() -> None:
    forwarded = _maybe_forward_to_agentirc(sys.argv[1:])
    if forwarded is not None:
        sys.exit(forwarded)

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
