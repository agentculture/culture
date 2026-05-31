"""Entry point for ``python -m culture.watcher`` — bootstraps the service."""

import argparse
import asyncio
import logging
import sys

from culture.watcher.alerts import AlertRouter
from culture.watcher.service import (
    WatcherService,
    default_state_path,
    load_config,
)
from culture.watcher.state import WatcherState


def main() -> int:
    parser = argparse.ArgumentParser(prog="culture-watcher")
    parser.add_argument("--config", default=None, help="Path to watcher.yaml")
    parser.add_argument("--state", default=None, help="Path to watcher-state.json")
    parser.add_argument("--once", action="store_true", help="Run a single pass and exit")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    cfg, raw = load_config(args.config)
    state = WatcherState(args.state or default_state_path())
    router = AlertRouter.from_config_dict(raw)

    # The watcher uses a persistent observer to fan out IRC alerts.
    # Construct lazily — when IRC is disabled, save the connection.
    send_irc = None
    if router.sinks.irc.enabled:
        from culture.cli.shared.constants import DEFAULT_CONFIG
        from culture.cli.shared.ipc import get_observer
        from culture.observer import PersistentObserver

        # Reuse the dashboard's PersistentObserver class; it's fine for
        # this process too.
        try:
            stub = get_observer(DEFAULT_CONFIG)
            persistent = PersistentObserver(stub.host, stub.port, stub.server_name)
        except Exception as exc:  # noqa: BLE001
            logging.warning("watcher: could not construct IRC observer: %s", exc)
            persistent = None

        async def send_irc(target: str, text: str) -> None:  # noqa: F811 — re-assign in closure
            if persistent is None:
                return
            await persistent.send_message(target, text)

    service = WatcherService(
        config=cfg,
        state=state,
        router=router,
        send_irc=send_irc,
    )
    try:
        if args.once:
            asyncio.run(service.run_once())
            return 0
        asyncio.run(service.run_forever())
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
