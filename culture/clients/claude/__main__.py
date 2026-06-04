"""CLI entry point for the culture daemon.

Usage:
    culture agent start <nick>       Start a single agent by nick
    culture agent start --all        Start all agents from config
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from culture.clients.claude.config import load_config
from culture.clients.claude.daemon import AgentDaemon

logger = logging.getLogger("culture")

DEFAULT_CONFIG = os.path.expanduser("~/.culture/agents.yaml")


def main() -> None:
    parser = argparse.ArgumentParser(prog="culture", description="culture agent daemon")
    sub = parser.add_subparsers(dest="command")

    start_parser = sub.add_parser("start", help="Start agent daemon(s)")
    start_parser.add_argument("nick", nargs="?", help="Agent nick to start")
    start_parser.add_argument("--all", action="store_true", help="Start all agents")
    start_parser.add_argument("--config", default=DEFAULT_CONFIG, help="Config file path")

    args = parser.parse_args()

    if args.command != "start":
        parser.print_help()
        sys.exit(1)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    config = load_config(args.config)

    if args.all:
        agents = config.agents
    elif args.nick:
        agent = config.get_agent(args.nick)
        if not agent:
            logger.error("Agent '%s' not found in config", args.nick)
            sys.exit(1)
        agents = [agent]
    else:
        start_parser.print_help()
        sys.exit(1)

    if not agents:
        logger.error("No agents configured")
        sys.exit(1)

    if len(agents) == 1:
        asyncio.run(_run_single(config, agents[0]))
    else:
        _run_multi(config, agents)


async def _run_single(config, agent) -> None:
    daemon = AgentDaemon(config, agent)
    await daemon.start()
    logger.info("Agent %s started", agent.nick)
    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    # v9.1.7 r2 — Qodo PR #59 #3: also wait on transport.fatal_exit
    # so a 432/433 from the IRCd triggers a clean daemon shutdown
    # rather than leaving the worker idling with a closed socket.
    fatal_exit = daemon.transport.fatal_exit
    stop_task = asyncio.create_task(stop_event.wait())
    fatal_task = asyncio.create_task(fatal_exit.wait())
    try:
        await asyncio.wait({stop_task, fatal_task}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (stop_task, fatal_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(stop_task, fatal_task, return_exceptions=True)

    if fatal_exit.is_set():
        logger.error(
            "Agent %s exiting due to fatal IRC registration error "
            "(see preceding error lines for the IRCd's reason).",
            agent.nick,
        )
    else:
        logger.info("Shutting down %s", agent.nick)
    await daemon.stop()
    if fatal_exit.is_set():
        sys.exit(1)


def _run_multi(config, agents) -> None:
    for agent in agents:
        pid = os.fork()
        if pid == 0:
            # Child: detach from parent session
            os.setsid()
            asyncio.run(_run_single(config, agent))
            sys.exit(0)
        else:
            logger.info("Started %s (pid %d)", agent.nick, pid)
    # Parent exits — children continue independently


if __name__ == "__main__":
    main()
