"""Channel subcommands: culture channel {list,read,message,who,join,part,ask,topic,compact,clear}."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .shared.constants import _CONFIG_HELP, DEFAULT_CONFIG
from .shared.ipc import agent_socket_path, get_observer, ipc_request

NAME = "channel"

_ALL_CMDS = "list|read|message|who|join|part|ask|topic|compact|clear"
_CHANNEL_HELP = "Channel (e.g. #general)"
_ERR_EMPTY_CHANNEL = "Error: channel name cannot be empty"


def _valid_nick(nick: str) -> bool:
    """Check nick matches the required <server>-<agent> format."""
    parts = nick.split("-", 1)
    return len(parts) == 2 and all(parts)


_ISSUE_TRACKER_URL = "https://github.com/agentculture/culture/issues"


def _warn_observer_fallback(operation: str) -> None:
    """Warn on stderr when an observer-fallback command silently went anonymous.

    Called only by the three commands that do fall back to the observer
    connection (`message`, `list`, `read`). `topic` and the `_require_ipc`
    commands exit on failure instead, so this helper is not used there —
    a misleading "falling back" notice would contradict the actual error.

    Issue #302: a daemon/CLI socket-path mismatch on macOS hid behind the
    silent peek fallback for two releases. The warning names the issue
    tracker so the next reproducer takes seconds to file.
    """
    nick = os.environ.get("CULTURE_NICK")
    if not nick or not _valid_nick(nick):
        return  # Human use without CULTURE_NICK — observer is the intended path.
    sock = agent_socket_path(nick)
    print(
        f"Warning: agent daemon IPC for {nick} failed ({sock}).\n"
        f"  Falling back to observer connection — `{operation}` will not run\n"
        f"  through the agent daemon and the action will not appear under {nick}.\n"
        f"  Verify the daemon is running:    culture agent status {nick}\n"
        f"  If it is running, this is a bug. Please open an issue:\n"
        f"    {_ISSUE_TRACKER_URL}",
        file=sys.stderr,
    )


def _try_ipc(msg_type: str, **kwargs) -> dict | None:
    """Try to route a command through the agent daemon's IPC socket.

    Returns the response dict if CULTURE_NICK is set and the daemon is
    reachable, otherwise None (caller should fall back to observer or
    surface its own error — see `_warn_observer_fallback` for the
    observer-fallback path).
    """
    nick = os.environ.get("CULTURE_NICK")
    if not nick or not _valid_nick(nick):
        return None
    sock = agent_socket_path(nick)
    return asyncio.run(ipc_request(sock, msg_type, **kwargs))


def _require_ipc(msg_type: str, **kwargs) -> dict:
    """Route a command through IPC, erroring if CULTURE_NICK is unset or daemon unreachable."""
    nick = os.environ.get("CULTURE_NICK")
    if not nick or not _valid_nick(nick):
        print(
            "Error: CULTURE_NICK must be set to a valid <server>-<agent> nick.\n"
            "  Example: export CULTURE_NICK=spark-claude",
            file=sys.stderr,
        )
        sys.exit(1)
    sock = agent_socket_path(nick)
    resp = asyncio.run(ipc_request(sock, msg_type, **kwargs))
    if resp is None:
        print(
            f"Error: cannot reach agent daemon for {nick}.\n"
            f"  Is the agent running? Check: culture agent status {nick}",
            file=sys.stderr,
        )
        sys.exit(1)
    return resp


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
    message_parser.add_argument("target", help=_CHANNEL_HELP)
    message_parser.add_argument("text", help="Message text to send")
    message_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)
    message_parser.add_argument(
        "--create",
        action="store_true",
        help=(
            "Allow sending to a channel that does not yet exist; the peek "
            "client will create it on the fly. Without this flag a typo "
            "channel name is rejected (see #331)."
        ),
    )

    # -- who ------------------------------------------------------------------
    who_parser = channel_sub.add_parser("who", help="List channel members")
    who_parser.add_argument("target", help="Channel or nick target")
    who_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- join -----------------------------------------------------------------
    join_parser = channel_sub.add_parser("join", help="Join a channel")
    join_parser.add_argument("target", help="Channel to join (e.g. #ops)")

    # -- part -----------------------------------------------------------------
    part_parser = channel_sub.add_parser("part", help="Leave a channel")
    part_parser.add_argument("target", help="Channel to leave (e.g. #ops)")

    # -- ask ------------------------------------------------------------------
    ask_parser = channel_sub.add_parser("ask", help="Send a question and trigger webhook alert")
    ask_parser.add_argument("target", help=_CHANNEL_HELP)
    ask_parser.add_argument("text", help="Question text")
    ask_parser.add_argument("--timeout", type=int, default=30, help="Timeout in seconds")

    # -- topic ----------------------------------------------------------------
    topic_parser = channel_sub.add_parser("topic", help="Get or set channel topic")
    topic_parser.add_argument("target", help=_CHANNEL_HELP)
    topic_parser.add_argument("text", nargs="?", default=None, help="New topic (omit to read)")
    topic_parser.add_argument("--config", default=DEFAULT_CONFIG, help=_CONFIG_HELP)

    # -- compact --------------------------------------------------------------
    channel_sub.add_parser("compact", help="Compact the agent's context window")

    # -- clear ----------------------------------------------------------------
    channel_sub.add_parser("clear", help="Clear the agent's context window")


def _is_connection_error(msg: str) -> bool:
    """Return True if the exception message indicates a connection failure."""
    return (
        "Timed out" in msg or "Connection refused" in msg or "Connect call failed" in msg or not msg
    )


def dispatch(args: argparse.Namespace) -> None:
    if not args.channel_command:
        print(f"Usage: culture channel {{{_ALL_CMDS}}}", file=sys.stderr)
        sys.exit(1)

    handlers = {
        "list": _cmd_list,
        "read": _cmd_read,
        "message": _cmd_message,
        "who": _cmd_who,
        "join": _cmd_join,
        "part": _cmd_part,
        "ask": _cmd_ask,
        "topic": _cmd_topic,
        "compact": _cmd_compact,
        "clear": _cmd_clear,
    }
    handler = handlers.get(args.channel_command)
    if not handler:
        print(f"Unknown channel command: {args.channel_command}", file=sys.stderr)
        sys.exit(1)
    try:
        handler(args)
    except (TimeoutError, OSError) as exc:
        if _is_connection_error(str(exc)):
            print(
                "Error: cannot connect to IRC server. Is the server running?\n"
                "  Start it with: culture server start",
                file=sys.stderr,
            )
            sys.exit(1)
        raise


# -----------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------


def _cmd_list(args: argparse.Namespace) -> None:
    resp = _try_ipc("irc_channels")
    if resp and resp.get("ok"):
        channels = resp.get("data", {}).get("channels", [])
        if not channels:
            print("No active channels")
            return
        print("Active channels:")
        for ch in channels:
            print(f"  {ch}")
        return

    _warn_observer_fallback("channel list")
    observer = get_observer(args.config)
    channels = asyncio.run(observer.list_channels())

    if not channels:
        print("No active channels")
        return

    print("Active channels:")
    for ch in channels:
        print(f"  {ch}")


def _cmd_read(args: argparse.Namespace) -> None:
    if not args.target.strip():
        print(_ERR_EMPTY_CHANNEL, file=sys.stderr)
        sys.exit(1)
    channel = args.target if args.target.startswith("#") else f"#{args.target}"

    resp = _try_ipc("irc_read", channel=channel, limit=args.limit)
    if resp and resp.get("ok"):
        messages = resp.get("data", {}).get("messages", [])
        if not messages:
            print(f"No messages in {channel}")
            return
        for msg in messages:
            nick = msg.get("nick", "???")
            text = msg.get("text", "")
            print(f"<{nick}> {text}")
        return

    _warn_observer_fallback("channel read")
    observer = get_observer(args.config)
    messages = asyncio.run(observer.read_channel(channel, limit=args.limit))

    if not messages:
        print(f"No messages in {channel}")
        return

    for msg in messages:
        print(msg)


def _interpret_escapes(text: str) -> str:
    """Convert shell-literal ``\\n`` / ``\\t`` / ``\\\\`` sequences to real chars.

    Walks the string left-to-right so a preceding backslash escapes the next
    character — ``\\\\n`` stays as the two chars ``\\`` + ``n``, while ``\\n``
    becomes a real newline. Supported escapes: ``\\n`` → newline, ``\\t`` →
    tab, ``\\\\`` → single backslash. Any other ``\\x`` pair is passed through
    unchanged so we don't surprise users with ``\\x..`` / ``\\u....`` style
    interpretation that ``codecs.decode(..., "unicode_escape")`` would do.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "n":
                out.append("\n")
                i += 2
                continue
            if nxt == "t":
                out.append("\t")
                i += 2
                continue
            if nxt == "\\":
                out.append("\\")
                i += 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _channel_exists(target: str, observer) -> bool:
    """Return True iff ``target`` appears in the server-wide active channel list.

    Always uses the observer's ``list_channels()`` (a fresh peek LIST
    query) rather than the daemon's ``irc_channels`` IPC. The IPC
    response only carries the *joined* channels of the calling agent's
    transport, so an existing channel the daemon hasn't joined would
    appear non-existent and the guard would reject a perfectly valid
    send. Server-wide LIST is the source of truth for "does this channel
    exist on the server" (#341 review). The extra TCP roundtrip is fine
    — the guard only fires on non-``--create`` message sends, not on
    every send.
    """
    channels = asyncio.run(observer.list_channels())
    return target in channels


def _cmd_message(args: argparse.Namespace) -> None:
    if not args.target.strip():
        print(_ERR_EMPTY_CHANNEL, file=sys.stderr)
        sys.exit(1)
    if not args.text.strip():
        print("Error: message text cannot be empty", file=sys.stderr)
        sys.exit(1)
    target = args.target if args.target.startswith("#") else f"#{args.target}"
    text = _interpret_escapes(args.text)

    # After escape interpretation, reject input that has no non-empty line —
    # otherwise we'd print "Sent to ..." while nothing actually goes out.
    if not any(line.strip() for line in text.split("\n")):
        print(
            "Error: message text has no non-empty line after escape interpretation", file=sys.stderr
        )
        sys.exit(1)

    # Reject typo channel sends (#331). A typo previously auto-created a
    # dead channel that nobody else ever joined, while the CLI confidently
    # printed "Sent to #...". Pass --create to opt back into the old
    # behavior for bootstrap workflows.
    if not getattr(args, "create", False):
        observer = get_observer(args.config)
        if not _channel_exists(target, observer):
            print(
                f"Error: channel {target!r} does not exist on the server.\n"
                f"  Check 'culture channel list' for active channels, or pass\n"
                f"  '--create' to bootstrap {target!r} via this send.",
                file=sys.stderr,
            )
            sys.exit(1)

    resp = _try_ipc("irc_send", channel=target, message=text)
    if resp and resp.get("ok"):
        print(f"Sent to {target}")
        return

    _warn_observer_fallback("channel message")
    observer = get_observer(args.config)
    asyncio.run(observer.send_message(target, text))
    print(f"Sent to {target}")


def _cmd_who(args: argparse.Namespace) -> None:
    if not args.target.strip():
        print(_ERR_EMPTY_CHANNEL, file=sys.stderr)
        sys.exit(1)
    target = args.target

    # WHO always uses the observer — the daemon IPC handler fires the query
    # but returns results asynchronously via IRC numerics, so the CLI can't
    # collect the nick list through IPC.
    observer = get_observer(args.config)
    nicks = asyncio.run(observer.who(target))

    if not nicks:
        print(f"No users in {target}")
        return

    print(f"Users in {target}:")
    for nick in nicks:
        print(f"  {nick}")


def _cmd_join(args: argparse.Namespace) -> None:
    target = args.target if args.target.startswith("#") else f"#{args.target}"
    resp = _require_ipc("irc_join", channel=target)
    if resp.get("ok"):
        print(f"Joined {target}")
    else:
        print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def _cmd_part(args: argparse.Namespace) -> None:
    target = args.target if args.target.startswith("#") else f"#{args.target}"
    resp = _require_ipc("irc_part", channel=target)
    if resp.get("ok"):
        print(f"Left {target}")
    else:
        print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def _cmd_ask(args: argparse.Namespace) -> None:
    if not args.target.strip():
        print(_ERR_EMPTY_CHANNEL, file=sys.stderr)
        sys.exit(1)
    if not args.text.strip():
        print("Error: question text cannot be empty", file=sys.stderr)
        sys.exit(1)
    target = args.target if args.target.startswith("#") else f"#{args.target}"
    resp = _require_ipc("irc_ask", channel=target, message=args.text, timeout=args.timeout)
    if resp.get("ok"):
        print(json.dumps(resp, indent=2))
    else:
        print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def _cmd_topic(args: argparse.Namespace) -> None:
    if not args.target.strip():
        print(_ERR_EMPTY_CHANNEL, file=sys.stderr)
        sys.exit(1)
    target = args.target if args.target.startswith("#") else f"#{args.target}"

    if args.text is not None:
        _topic_set(target, args.text)
    else:
        _topic_read(target)


def _topic_set(target: str, text: str) -> None:
    """Set channel topic via agent daemon."""
    resp = _require_ipc("irc_topic", channel=target, topic=text)
    if resp.get("ok"):
        print(f"Topic set for {target}")
    else:
        print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def _topic_read(target: str) -> None:
    """Read channel topic via agent daemon IPC."""
    resp = _try_ipc("irc_topic", channel=target)
    if not resp or not resp.get("ok"):
        print(
            "Error: topic query requires a running agent daemon (CULTURE_NICK).",
            file=sys.stderr,
        )
        sys.exit(1)
    data = resp.get("data") or {}
    if "topic" not in data:
        print(f"Topic query sent for {target} (result arrives asynchronously)")
        return
    topic = data["topic"]
    print(f"Topic for {target}: {topic}" if topic else f"No topic set for {target}")


def _cmd_compact(args: argparse.Namespace) -> None:
    resp = _require_ipc("compact")
    if resp.get("ok"):
        print("Context window compacted")
    else:
        print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)


def _cmd_clear(args: argparse.Namespace) -> None:
    resp = _require_ipc("clear")
    if resp.get("ok"):
        print("Context window cleared")
    else:
        print(f"Error: {resp.get('error', 'unknown error')}", file=sys.stderr)
        sys.exit(1)
