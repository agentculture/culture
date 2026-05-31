"""``culture watcher`` subcommand — deterministic mesh-state watchdog.

Verbs:
    start    Run the watcher poll loop in the foreground.
    once     Single pass through every pattern; useful for cron.
    status   Print last firings + cooldowns from watcher-state.json.
    test     Force-fire a synthetic alert through every configured sink.

Design: the watcher reads the local state files (daemon-log + audit
+ perm-queue), evaluates a set of deterministic patterns, and ships
alerts to IRC (always), email (opt-in via SMTP env-var), and webhooks
(opt-in). See ``culture/watcher/__init__.py`` for the module map.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time

NAME = "watcher"


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "watcher",
        help="Deterministic mesh-state watcher (silent-death, crash burst, token spike, ...)",
    )
    sub = p.add_subparsers(dest="watcher_cmd", required=True)

    start_p = sub.add_parser("start", help="Run the watcher loop in the foreground")
    start_p.add_argument("--config", default=None)
    start_p.add_argument("--state", default=None)
    start_p.add_argument("--verbose", "-v", action="store_true")

    once_p = sub.add_parser("once", help="Single pass over every pattern, then exit")
    once_p.add_argument("--config", default=None)
    once_p.add_argument("--state", default=None)
    once_p.add_argument("--verbose", "-v", action="store_true")

    status_p = sub.add_parser("status", help="Show recent watcher firings + cooldowns")
    status_p.add_argument("--state", default=None)

    test_p = sub.add_parser(
        "test", help="Force-fire a synthetic alert (silent_death) through every sink"
    )
    test_p.add_argument("--config", default=None)
    test_p.add_argument("--target", default="local-test-nick")


def dispatch(args: argparse.Namespace) -> None:
    cmd = getattr(args, "watcher_cmd", None)
    if cmd == "start":
        _cmd_start(args, run_forever=True)
    elif cmd == "once":
        _cmd_start(args, run_forever=False)
    elif cmd == "status":
        _cmd_status(args)
    elif cmd == "test":
        _cmd_test(args)
    else:
        print(f"Unknown watcher command: {cmd}", file=sys.stderr)
        sys.exit(1)


# --- Helpers ---------------------------------------------------------------


def _build_service(args: argparse.Namespace):
    from culture.watcher.alerts import AlertRouter
    from culture.watcher.service import (
        WatcherService,
        default_state_path,
        load_config,
    )
    from culture.watcher.state import WatcherState

    cfg, raw = load_config(args.config)
    state = WatcherState(args.state or default_state_path())
    router = AlertRouter.from_config_dict(raw)

    send_irc = None
    persistent = None
    if router.sinks.irc.enabled:
        try:
            from culture.cli.shared.constants import DEFAULT_CONFIG
            from culture.cli.shared.ipc import get_observer
            from culture.observer import PersistentObserver

            stub = get_observer(DEFAULT_CONFIG)
            persistent = PersistentObserver(stub.host, stub.port, stub.server_name)
        except Exception as exc:  # noqa: BLE001
            logging.warning("watcher: IRC alerts disabled (no mesh): %s", exc)
            persistent = None

        async def send_irc(target: str, text: str) -> None:  # noqa: F811
            if persistent is None:
                return
            await persistent.send_message(target, text)

    service = WatcherService(config=cfg, state=state, router=router, send_irc=send_irc)
    return service, persistent


def _cmd_start(args: argparse.Namespace, *, run_forever: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    service, persistent = _build_service(args)
    try:
        if run_forever:
            asyncio.run(service.run_forever())
        else:
            asyncio.run(service.run_once())
    except KeyboardInterrupt:
        pass
    finally:
        if persistent is not None:
            try:
                asyncio.run(persistent.close())
            except Exception:  # noqa: BLE001
                pass


def _cmd_status(args: argparse.Namespace) -> None:
    from culture.watcher.service import default_state_path
    from culture.watcher.state import WatcherState

    state = WatcherState(args.state or default_state_path())
    if not state.firings:
        print("watcher: no firings recorded")
        return
    now = time.time()
    print(f"{'PATTERN:TARGET':<60} {'AGE':>10}")
    for key, ts in sorted(state.firings.items(), key=lambda kv: kv[1], reverse=True):
        age = now - ts
        if age < 60:
            label = f"{int(age)}s"
        elif age < 3600:
            label = f"{int(age/60)}m"
        else:
            label = f"{age/3600:.1f}h"
        print(f"{key:<60} {label:>10}")


def _cmd_test(args: argparse.Namespace) -> None:
    """Synthesize a single PatternEvent and dispatch it.

    Bypasses cooldown so you always see your sinks light up. Useful
    when wiring SMTP or a Slack webhook for the first time.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    service, persistent = _build_service(args)
    from culture.watcher.patterns import PatternEvent

    ev = PatternEvent(
        pattern="silent_death",
        severity="high",
        target=args.target,
        summary=f"TEST: synthetic silent-death for {args.target}",
        detail="this is a test alert from `culture watcher test`",
    )
    service.state.firings.pop(ev.key, None)  # clear cooldown for this test
    try:
        shipped = asyncio.run(service.dispatch([ev]))
        print(f"shipped {shipped} alert(s)")
    finally:
        if persistent is not None:
            try:
                asyncio.run(persistent.close())
            except Exception:  # noqa: BLE001
                pass
