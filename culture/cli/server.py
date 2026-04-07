"""Server subcommands: culture server {start,stop,status,default,rename,archive,unarchive}."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
import time

from culture.pidfile import (
    is_culture_process,
    is_process_alive,
    read_default_server,
    read_pid,
    remove_pid,
    write_pid,
)

from .shared.constants import (
    _CONFIG_HELP,
    _SERVER_NAME_HELP,
    BOT_CONFIG_FILE,
    DEFAULT_CONFIG,
    LOG_DIR,
)
from .shared.mesh import parse_link, resolve_links_from_mesh

logger = logging.getLogger("culture")

NAME = "server"

_DEFAULT_SERVER = "culture"


def _resolve_server_name(args: argparse.Namespace) -> str:
    """Resolve the server name from args, default server file, or fallback."""
    if args.name is not None:
        return args.name
    return read_default_server() or _DEFAULT_SERVER


def register(subparsers: argparse._SubParsersAction) -> None:
    server_parser = subparsers.add_parser("server", help="Manage the IRC server")
    server_sub = server_parser.add_subparsers(dest="server_command")

    srv_start = server_sub.add_parser("start", help="Start the IRC server daemon")
    srv_start.add_argument("--name", default=None, help=_SERVER_NAME_HELP)
    srv_start.add_argument("--host", default="0.0.0.0", help="Listen address")
    srv_start.add_argument("--port", type=int, default=6667, help="Listen port")
    srv_start.add_argument(
        "--link",
        type=parse_link,
        action="append",
        default=[],
        help="Link to peer: name:host:port:password",
    )
    srv_start.add_argument(
        "--mesh-config",
        default=None,
        help="Read links from mesh.yaml + OS keyring (no passwords in CLI args)",
    )
    srv_start.add_argument(
        "--webhook-port",
        type=int,
        default=7680,
        help="HTTP port for bot webhooks (default: 7680)",
    )
    srv_start.add_argument(
        "--foreground",
        action="store_true",
        help="Run in foreground (for service managers)",
    )
    srv_start.add_argument(
        "--data-dir",
        default=os.path.expanduser("~/.culture/data"),
        help="Data directory for persistent storage (default: ~/.culture/data)",
    )

    srv_stop = server_sub.add_parser("stop", help="Stop the IRC server daemon")
    srv_stop.add_argument("--name", default=None, help=_SERVER_NAME_HELP)

    srv_status = server_sub.add_parser("status", help="Check server daemon status")
    srv_status.add_argument("--name", default=None, help=_SERVER_NAME_HELP)

    srv_default = server_sub.add_parser("default", help="Set default server")
    srv_default.add_argument("name", help="Server name to set as default")

    srv_rename = server_sub.add_parser(
        "rename", help="Rename the server (updates config and agent nicks)"
    )
    srv_rename.add_argument("new_name", help="New server name")
    srv_rename.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=_CONFIG_HELP,
    )

    srv_archive = server_sub.add_parser(
        "archive", help="Archive the server and all its agents/bots"
    )
    srv_archive.add_argument("--name", default=None, help=_SERVER_NAME_HELP)
    srv_archive.add_argument("--reason", default="", help="Reason for archiving")
    srv_archive.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=_CONFIG_HELP,
    )

    srv_unarchive = server_sub.add_parser(
        "unarchive", help="Restore an archived server and all its agents/bots"
    )
    srv_unarchive.add_argument("--name", default=None, help=_SERVER_NAME_HELP)
    srv_unarchive.add_argument(
        "--config",
        default=DEFAULT_CONFIG,
        help=_CONFIG_HELP,
    )


def dispatch(args: argparse.Namespace) -> None:
    if not args.server_command:
        print(
            "Usage: culture server {start|stop|status|default|rename|archive|unarchive}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Resolve --name for commands that use it (all except default/rename)
    if args.server_command not in ("default", "rename") and hasattr(args, "name"):
        args.name = _resolve_server_name(args)

    if args.server_command == "start":
        _server_start(args)
    elif args.server_command == "stop":
        _server_stop(args)
    elif args.server_command == "status":
        _server_status(args)
    elif args.server_command == "default":
        from pathlib import Path

        from culture.pidfile import PID_DIR, list_servers, write_default_server

        from .shared.constants import DEFAULT_CONFIG

        # Accept the name if: it matches a running server, has a PID file,
        # or matches the configured server name.
        known_running = {s["name"] for s in list_servers()}
        pid_dir = Path(PID_DIR)
        known_pids = set()
        if pid_dir.exists():
            prefix = "server-"
            for p in pid_dir.glob(f"{prefix}*.pid"):
                known_pids.add(p.stem[len(prefix) :])
        known_names = known_running | known_pids

        # Also accept the configured server name
        try:
            from culture.clients.claude.config import load_config_or_default

            config = load_config_or_default(DEFAULT_CONFIG)
            known_names.add(config.server.name)
        except Exception:
            pass

        if args.name not in known_names:
            print(f"Server '{args.name}' not found.", file=sys.stderr)
            if known_names:
                print(
                    f"Known servers: {', '.join(sorted(known_names))}",
                    file=sys.stderr,
                )
            sys.exit(1)
        write_default_server(args.name)
        print(f"Default server set to '{args.name}'")
    elif args.server_command == "rename":
        _server_rename(args)
    elif args.server_command == "archive":
        _server_archive(args)
    elif args.server_command == "unarchive":
        _server_unarchive(args)


# -----------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------


def _server_rename(args: argparse.Namespace) -> None:
    """Rename the server: update config, agent nicks, and PID files."""
    from culture.clients.claude.config import rename_server, sanitize_agent_name
    from culture.pidfile import (
        is_process_alive,
        read_default_server,
        read_pid,
        rename_pid,
        write_default_server,
    )

    try:
        new_name = sanitize_agent_name(args.new_name)
    except ValueError:
        print(f"Invalid server name: {args.new_name!r}", file=sys.stderr)
        sys.exit(1)

    try:
        old_name, renamed = rename_server(args.config, new_name)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)

    if old_name == new_name:
        print(f"Server is already named '{new_name}'")
        return

    rename_pid(f"server-{old_name}", f"server-{new_name}")

    for old_nick, new_nick in renamed:
        rename_pid(f"agent-{old_nick}", f"agent-{new_nick}")

    if read_default_server() == old_name:
        write_default_server(new_name)

    print(f"Server renamed: {old_name} → {new_name}")
    for old_nick, new_nick in renamed:
        print(f"  Agent: {old_nick} → {new_nick}")

    server_pid = read_pid(f"server-{new_name}")
    server_running = server_pid and is_process_alive(server_pid)

    print()
    if server_running:
        print("The server is still running under the old name.")
        print("Restart it for the rename to take effect:")
        print(f"  culture server stop --name {new_name}")
        print(f"  culture server start --name {new_name}")
    if renamed:
        print("Restart agents for the new nicks to take effect:")
        print("  culture agent stop --all && culture agent start --all")


def _wait_for_port(
    host: str,
    port: int,
    pid: int,
    timeout: float = 30,
) -> tuple[bool, str]:
    """Poll *host*:*port* until a TCP connect succeeds or *timeout* expires."""
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_process_alive(pid):
            return False, "failed to start"
        try:
            s = socket.create_connection((check_host, port), timeout=0.5)
            s.close()
        except OSError:
            time.sleep(0.2)
            continue
        time.sleep(0.1)
        if not is_process_alive(pid):
            return False, "failed to start"
        return True, ""
    return False, "started but not yet accepting connections"


def _maybe_set_default_server(name: str) -> None:
    """Set this server as default if none is configured."""
    from culture.pidfile import read_default_server, write_default_server

    if read_default_server() is None:
        write_default_server(name)


def _run_foreground(args: argparse.Namespace, pid_name: str, links: list) -> None:
    """Run the server in the foreground (blocking)."""
    write_pid(pid_name, os.getpid())
    os.makedirs(LOG_DIR, exist_ok=True)
    print(f"Server '{args.name}' starting in foreground (PID {os.getpid()})")
    print(f"  Listening on {args.host}:{args.port}")
    print(f"  Webhook port: {args.webhook_port}")
    _maybe_set_default_server(args.name)
    try:
        asyncio.run(
            _run_server(args.name, args.host, args.port, links, args.webhook_port, args.data_dir)
        )
    finally:
        remove_pid(pid_name)


def _verify_daemon_started(args: argparse.Namespace, pid: int) -> None:
    """Wait for the daemon child to be ready, exit on failure."""
    log_hint = f"{LOG_DIR}/server-{args.name}.log"
    if args.port == 0:
        time.sleep(0.5)
        if not is_process_alive(pid):
            print(f"Server '{args.name}' failed to start", file=sys.stderr)
            print(f"  Check logs: {log_hint}", file=sys.stderr)
            sys.exit(1)
    else:
        ok, err = _wait_for_port(args.host, args.port, pid, timeout=30)
        if not ok:
            print(f"Server '{args.name}' {err}", file=sys.stderr)
            print(f"  Check logs: {log_hint}", file=sys.stderr)
            sys.exit(1)
    print(f"Server '{args.name}' started (PID {pid})")
    print(f"  Listening on {args.host}:{args.port}")
    print(f"  Logs: {log_hint}")
    _maybe_set_default_server(args.name)


def _check_server_archived(args: argparse.Namespace) -> None:
    """Exit if the server is archived."""
    from culture.clients.claude.config import load_config_or_default

    config_path = getattr(args, "config", DEFAULT_CONFIG)
    config = load_config_or_default(config_path)
    if config.server.name == args.name and config.server.archived:
        print(
            f"Server '{args.name}' is archived. Unarchive first:",
            file=sys.stderr,
        )
        print(f"  culture server unarchive --name {args.name}", file=sys.stderr)
        sys.exit(1)


def _check_already_running(pid_name: str, name: str) -> None:
    """Exit if the server is already running."""
    existing = read_pid(pid_name)
    if existing and is_process_alive(existing):
        print(f"Server '{name}' is already running (PID {existing})")
        sys.exit(1)


def _resolve_server_links(args: argparse.Namespace) -> list:
    """Resolve link configs from CLI args or mesh config."""
    if getattr(args, "mesh_config", None):
        return resolve_links_from_mesh(args.mesh_config)
    return args.link


def _daemonize_server(args: argparse.Namespace, pid_name: str, links: list) -> None:
    """Fork and set up the daemon child process for the server."""
    if sys.platform == "win32":
        print("Daemon mode not supported on Windows. Use --foreground.", file=sys.stderr)
        sys.exit(1)

    pid = os.fork()
    if pid > 0:
        _verify_daemon_started(args, pid)
        return

    # Child: detach from parent session
    os.setsid()

    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, f"server-{args.name}.log")
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    os.close(log_fd)

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.close(devnull)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )

    write_pid(pid_name, os.getpid())

    try:
        asyncio.run(
            _run_server(args.name, args.host, args.port, links, args.webhook_port, args.data_dir)
        )
    finally:
        remove_pid(pid_name)
        os._exit(0)


def _server_start(args: argparse.Namespace) -> None:
    _check_server_archived(args)

    pid_name = f"server-{args.name}"
    _check_already_running(pid_name, args.name)

    links = _resolve_server_links(args)

    if getattr(args, "foreground", False):
        _run_foreground(args, pid_name, links)
        return

    _daemonize_server(args, pid_name, links)


async def _run_server(
    name: str,
    host: str,
    port: int,
    links: list | None = None,
    webhook_port: int = 7680,
    data_dir: str = "",
) -> None:
    """Run the IRC server (called in the daemon child process)."""
    from culture.server.config import ServerConfig
    from culture.server.ircd import IRCd

    config = ServerConfig(
        name=name,
        host=host,
        port=port,
        webhook_port=webhook_port,
        links=links or [],
        data_dir=data_dir,
    )
    ircd = IRCd(config)
    await ircd.start()
    logger.info("Server '%s' listening on %s:%d", name, host, port)

    for lc in config.links:
        try:
            await ircd.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
            logger.info("Linking to %s at %s:%d", lc.name, lc.host, lc.port)
        except Exception as e:
            logger.error("Failed to link to %s: %s — will retry", lc.name, e)
            ircd.maybe_retry_link(lc.name)

    stop_event = asyncio.Event()
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: stop_event.set())

    await stop_event.wait()
    logger.info("Server '%s' shutting down", name)
    await ircd.stop()


def _wait_for_graceful_stop(pid: int, timeout_ticks: int = 50) -> bool:
    """Wait for a process to exit gracefully. Return True if it stopped."""
    for _ in range(timeout_ticks):
        if not is_process_alive(pid):
            return True
        time.sleep(0.1)
    return False


def _force_kill(pid: int, name: str) -> None:
    """Force-kill a process that didn't stop gracefully."""
    if sys.platform == "win32":
        print(f"Server '{name}' did not stop gracefully, terminating")
        sig = signal.SIGTERM
    else:
        print(f"Server '{name}' did not stop gracefully, sending SIGKILL")
        sig = signal.SIGKILL
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _server_stop(args: argparse.Namespace) -> None:
    pid_name = f"server-{args.name}"
    pid = read_pid(pid_name)

    if pid is None:
        print(f"No PID file for server '{args.name}'")
        sys.exit(1)

    if not is_process_alive(pid):
        print(f"Server '{args.name}' is not running (stale PID {pid})")
        remove_pid(pid_name)
        return

    if not is_culture_process(pid):
        print(f"PID {pid} is not a culture process — removing stale PID file")
        remove_pid(pid_name)
        return

    print(f"Stopping server '{args.name}' (PID {pid})...")
    os.kill(pid, signal.SIGTERM)

    if _wait_for_graceful_stop(pid):
        print(f"Server '{args.name}' stopped")
        remove_pid(pid_name)
        return

    _force_kill(pid, args.name)
    remove_pid(pid_name)
    print(f"Server '{args.name}' killed")


def _server_status(args: argparse.Namespace) -> None:
    pid_name = f"server-{args.name}"
    pid = read_pid(pid_name)

    if pid is None:
        print(f"Server '{args.name}': not running (no PID file)")
        return

    if is_process_alive(pid):
        print(f"Server '{args.name}': running (PID {pid})")
    else:
        print(f"Server '{args.name}': not running (stale PID {pid})")
        remove_pid(pid_name)


# -----------------------------------------------------------------------
# Archive / Unarchive
# -----------------------------------------------------------------------


def _validate_config_name(config, name: str) -> str:
    """Verify config server name matches the requested name, exit on mismatch."""
    server_name = config.server.name
    if server_name != name:
        print(
            f"Server name mismatch: --name '{name}' but config has '{server_name}'",
            file=sys.stderr,
        )
        sys.exit(1)
    return server_name


def _update_single_bot_archive(
    yaml_path, bot_config, archive: bool, reason: str, today: str
) -> str | None:
    """Update archive state of a single bot. Return the bot name if changed, else None."""
    from culture.bots.config import save_bot_config

    if archive and not bot_config.archived:
        bot_config.archived = True
        bot_config.archived_at = today
        bot_config.archived_reason = reason
        save_bot_config(yaml_path, bot_config)
        return bot_config.name
    if not archive and bot_config.archived:
        bot_config.archived = False
        bot_config.archived_at = ""
        bot_config.archived_reason = ""
        save_bot_config(yaml_path, bot_config)
        return bot_config.name
    return None


def _set_bots_archive_state(agent_nicks: set, *, archive: bool, reason: str = "") -> list[str]:
    """Archive or unarchive bots owned by any of the given agent nicks."""
    from culture.bots.config import BOTS_DIR, load_bot_config

    changed = []
    if not BOTS_DIR.is_dir():
        return changed
    today = time.strftime("%Y-%m-%d")
    for bot_dir in BOTS_DIR.iterdir():
        yaml_path = bot_dir / BOT_CONFIG_FILE
        if not yaml_path.is_file():
            continue
        try:
            bot_config = load_bot_config(yaml_path)
        except (OSError, ValueError) as exc:
            print(f"  Warning: skipping bot '{bot_dir.name}': {exc}", file=sys.stderr)
            continue
        if bot_config.owner not in agent_nicks:
            continue
        name = _update_single_bot_archive(yaml_path, bot_config, archive, reason, today)
        if name:
            changed.append(name)
    return changed


def _server_archive(args: argparse.Namespace) -> None:
    """Archive the server and cascade to all agents and bots."""
    from culture.clients.claude.config import (
        archive_server,
        load_config_or_default,
    )

    config = load_config_or_default(args.config)
    server_name = _validate_config_name(config, args.name)

    if config.server.archived:
        print(f"Server '{server_name}' is already archived")
        return

    # Stop server if running
    pid_name = f"server-{server_name}"
    pid = read_pid(pid_name)
    if pid and is_process_alive(pid):
        print(f"Stopping server '{server_name}'...")
        _server_stop(args)

    # Stop all running agents
    from culture.cli.shared.process import stop_agent

    for agent in config.agents:
        agent_pid = read_pid(f"agent-{agent.nick}")
        if agent_pid and is_process_alive(agent_pid):
            stop_agent(agent.nick)

    # Archive server + agents in config
    archived_nicks = archive_server(args.config, reason=args.reason)

    # Archive bots whose owner matches any agent on this server
    agent_nicks = {a.nick for a in config.agents}
    archived_bots = _set_bots_archive_state(agent_nicks, archive=True, reason=args.reason)

    print(f"Server archived: {server_name}")
    if args.reason:
        print(f"  Reason: {args.reason}")
    if archived_nicks:
        print(f"  Agents: {', '.join(archived_nicks)}")
    if archived_bots:
        print(f"  Bots:   {', '.join(archived_bots)}")
    print(f"\nTo restore: culture server unarchive --name {server_name}")


def _server_unarchive(args: argparse.Namespace) -> None:
    """Restore an archived server and cascade to agents and bots."""
    from culture.clients.claude.config import (
        load_config_or_default,
        unarchive_server,
    )

    config = load_config_or_default(args.config)
    server_name = _validate_config_name(config, args.name)

    if not config.server.archived:
        print(f"Server '{server_name}' is not archived", file=sys.stderr)
        sys.exit(1)

    # Unarchive server + agents
    unarchived_nicks = unarchive_server(args.config)

    # Unarchive bots whose owner matches any agent on this server
    agent_nicks = {a.nick for a in config.agents}
    unarchived_bots = _set_bots_archive_state(agent_nicks, archive=False)

    print(f"Server unarchived: {server_name}")
    if unarchived_nicks:
        print(f"  Agents: {', '.join(unarchived_nicks)}")
    if unarchived_bots:
        print(f"  Bots:   {', '.join(unarchived_bots)}")
    print(f"\nStart with: culture server start --name {server_name}")
