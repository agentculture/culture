"""``python -m culture.clients.bridge`` — start a bridge daemon.

The bridge is a transport-only process: it holds the IRC connection
for a CC session that is the actual boss brain. There is NO SDK loop
here — the bridge spools inbound DMs / mentions / ROOMINVITEs into
``~/.culture/bridge/inbox-<nick>.jsonl`` for CC to drain on next
``SessionStart`` (or push via the IPC ``whisper`` channel when CC is
connected).

Direct invocation::

    python -m culture.clients.bridge start <nick> \\
        [--config ~/.culture/server.yaml] [--channels "#a" "#b" ...]

The user-facing wrapper is ``culture bridge start <nick>`` in
``culture/cli/bridge.py``; this module is what that wrapper exec's.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from culture.clients.bridge.config import AgentConfig, load_config_or_default
from culture.clients.bridge.daemon import AgentDaemon

DEFAULT_CONFIG = os.path.expanduser("~/.culture/server.yaml")

logger = logging.getLogger("culture.bridge")


def _valid_nick(nick: str) -> bool:
    """Same ``<server>-<agent>`` rule the CLI wrapper enforces (Qodo
    PR #51 #2). Direct ``python -m culture.clients.bridge`` invocation
    must apply the same check so a manually-launched daemon cannot
    register an off-format nick that the dashboard / DM spool / channel
    routing logic does not understand."""
    parts = nick.split("-", 1)
    return len(parts) == 2 and all(parts)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="culture-bridge",
        description="Transport-only IRC bridge for CC-IS-the-boss sessions.",
    )
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="Start the bridge for <nick>.")
    start.add_argument("nick", help="Boss nick this bridge will hold.")
    start.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=f"Server config path (default: {DEFAULT_CONFIG}).",
    )
    start.add_argument(
        "--channels",
        nargs="*",
        default=None,
        metavar="CHAN",
        help=(
            "Channels to join. Defaults to the agent's manifest entry "
            "if registered, else just the agent's own #task channel."
        ),
    )
    start.add_argument(
        "--tag",
        action="append",
        dest="tags",
        default=None,
        metavar="TAG",
        help="Add a tag to the agent (repeatable). Default: 'bridge'.",
    )

    args = parser.parse_args()
    if args.command != "start":
        parser.print_help()
        sys.exit(1)

    # Qodo PR #51 #2: same nick validation as the CLI wrapper. A
    # direct ``python -m culture.clients.bridge`` invocation must NOT
    # be able to register an off-format nick.
    if (
        not args.nick
        or len(args.nick) > 64
        or any(c.isspace() or c in "/\\;" for c in args.nick)
        or not _valid_nick(args.nick)
    ):
        print(
            f"Error: invalid nick {args.nick!r} — "
            "must match <server>-<agent> format (Rule 428343)",
            file=sys.stderr,
        )
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    daemon_config = load_config_or_default(args.config)

    # If the manifest already has this nick, honor its channels/tags;
    # otherwise synthesize a minimal AgentConfig. The bridge is meant
    # to be ad-hoc for ephemeral CC sessions, so a missing manifest
    # entry is fine — the nick lives only for the lifetime of the
    # process.
    existing = next((a for a in daemon_config.agents if a.nick == args.nick), None)
    if existing is not None:
        agent = existing
        if args.channels is not None:
            agent.channels = list(args.channels)
        if args.tags is not None:
            agent.tags = list(args.tags)
    else:
        agent = AgentConfig(
            nick=args.nick,
            channels=list(args.channels) if args.channels is not None else [],
            tags=list(args.tags) if args.tags is not None else ["bridge"],
        )

    try:
        asyncio.run(_run(daemon_config, agent))
    except KeyboardInterrupt:
        pass


async def _run(config, agent) -> None:
    daemon = AgentDaemon(config, agent, skip_claude=True)
    await daemon.start()
    logger.info("Bridge %s started on %s:%d", agent.nick, config.server.host, config.server.port)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # v9.1.7 r2 — Qodo PR #59 #3: also wait on the transport's
    # fatal_exit event so a 432/433 from the IRCd triggers a clean
    # daemon shutdown (PID file removed, IPC socket closed,
    # ``culture bridge status`` reflects the failure). Pre-r2 the
    # transport stopped its read loop but the daemon kept idling on
    # stop_event — operator saw "running" status with no IRC
    # connection. Wait on whichever fires first.
    fatal_exit = daemon.transport.fatal_exit
    stop_task = asyncio.create_task(stop_event.wait())
    fatal_task = asyncio.create_task(fatal_exit.wait())
    try:
        done, pending = await asyncio.wait(
            {stop_task, fatal_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (stop_task, fatal_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stop_task, fatal_task, return_exceptions=True)

    if fatal_exit.is_set():
        logger.error(
            "Bridge %s exiting due to fatal IRC registration error "
            "(see preceding error lines for the IRCd's reason and "
            "actionable fix).",
            agent.nick,
        )
    else:
        logger.info("Shutting down bridge %s", agent.nick)
    await daemon.stop()
    if fatal_exit.is_set():
        sys.exit(1)


if __name__ == "__main__":
    main()
