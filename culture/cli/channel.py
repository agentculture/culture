"""Channel subcommands: culture channel {list,read,message,who}."""

from __future__ import annotations

import argparse
import asyncio
import sys

from ._helpers import _CONFIG_HELP, DEFAULT_CONFIG, get_observer

NAME = "channel"


def register(subparsers: argparse._SubParsersAction) -> None:
    channel_parser = subparsers.add_parser("channel", help="Channel messaging")
    channel_sub = channel_parser.add_subparsers(dest="channel_command")

    # -- list -----------------------------------------------------------------
    list_parser = channel_sub.add_parser("list", help="List active channels")
    list_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- read -----------------------------------------------------------------
    read_parser = channel_sub.add_parser("read", help="Read recent channel messages")
    read_parser.add_argument("target", help="Channel name (e.g. #general)")
    read_parser.add_argument("--limit", "-n", type=int, default=50, help="Number of messages")
    read_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- message --------------------------------------------------------------
    message_parser = channel_sub.add_parser("message", help="Send a message to a channel")
    message_parser.add_argument("target", help="Channel (e.g. #general)")
    message_parser.add_argument("text", help="Message text to send")
    message_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- who ------------------------------------------------------------------
    who_parser = channel_sub.add_parser("who", help="List channel members")
    who_parser.add_argument("target", help="Channel or nick target")
    who_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)


def dispatch(args: argparse.Namespace) -> None:
    if not args.channel_command:
        print("Usage: culture channel {list|read|message|who}", file=sys.stderr)
        sys.exit(1)

    handlers = {
        "list": _cmd_list,
        "read": _cmd_read,
        "message": _cmd_message,
        "who": _cmd_who,
    }
    handler = handlers.get(args.channel_command)
    if handler:
        handler(args)
    else:
        print(f"Unknown channel command: {args.channel_command}", file=sys.stderr)
        sys.exit(1)


# -----------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> None:
    observer = get_observer(args.config)
    channels = asyncio.run(observer.list_channels())

    if not channels:
        print("No active channels")
        return

    print("Active channels:")
    for ch in channels:
        print(f"  {ch}")


def _cmd_read(args: argparse.Namespace) -> None:
    observer = get_observer(args.config)
    channel = args.target if args.target.startswith("#") else f"#{args.target}"
    messages = asyncio.run(observer.read_channel(channel, limit=args.limit))

    if not messages:
        print(f"No messages in {channel}")
        return

    for msg in messages:
        print(msg)


def _cmd_message(args: argparse.Namespace) -> None:
    observer = get_observer(args.config)
    target = args.target if args.target.startswith("#") else args.target
    asyncio.run(observer.send_message(target, args.text))
    print(f"Sent to {target}")


def _cmd_who(args: argparse.Namespace) -> None:
    observer = get_observer(args.config)
    target = args.target
    nicks = asyncio.run(observer.who(target))

    if not nicks:
        print(f"No users in {target}")
        return

    print(f"Users in {target}:")
    for nick in nicks:
        print(f"  {nick}")
