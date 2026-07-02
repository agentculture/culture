"""Server subcommands: culture server {start,stop,status,default,rename,archive,unarchive}.

Renamed back from ``culture chat`` in culture 10.0.0 — the surface
manages a server (lifecycle + agentirc passthrough), not a chat. The 7
verbs above stay culture-owned; everything else (``restart``, ``link``,
``logs``, ``version``, ``serve``, plus any new verb agentirc adds in
future versions) falls through verbatim to ``agentirc.cli.dispatch`` so
it lands under ``culture server <verb>`` for free.

The 7 culture-owned verbs are NOT forwarded because:

- ``start`` embeds ``agentirc.ircd.IRCd`` in-process and installs
  culture's system-bot bridge (``culture_core.bots.install_system_bridge``)
  before ``ircd.start()`` so agentirc's ``load_system_bots`` discovers
  culture's welcome bot. agentirc 9.7+ ``IRCd.start()`` owns the
  ``BotManager`` lifecycle; forwarding to ``agentirc start`` would skip
  the bridge — culture's system bots would never load.
- ``stop`` and ``status`` operate on culture's own PID file
  (``~/.culture/pids/server-<name>.pid``), which ``start`` writes.
  Forwarding either to agentirc would read a different file and never
  match what culture wrote.
- ``default``, ``rename``, ``archive``, ``unarchive`` operate on
  culture's local config (``~/.culture/server.yaml``). Agentirc has no
  notion of them.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import socket
import sys
import time

from culture_core.cli._errors import (
    EXIT_DAEMON_PERMANENT,
    EXIT_USER_ERROR,
    CultureError,
    classify_daemon_exit,
)
from culture_core.cli._passthrough import _translate_exit
from culture_core.pidfile import (
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

# Verbs that fall through to agentirc.cli.dispatch verbatim. Each is
# registered as a thin subparser with REMAINDER so all flags pass
# through unchanged. New verbs added in future agentirc versions can be
# added here — no behavior change beyond appearing under
# ``culture server <verb>``.
_AGENTIRC_FORWARDED_VERBS = ("restart", "link", "logs", "version", "serve")


def _resolve_server_name(args: argparse.Namespace) -> str:
    """Resolve the server name from args, default server file, or fallback."""
    if args.name is not None:
        return args.name
    return read_default_server() or _DEFAULT_SERVER


def register(subparsers: argparse._SubParsersAction) -> None:
    server_parser = subparsers.add_parser(
        "server", help="Manage the IRC server (lifecycle + agentirc passthrough)"
    )
    server_sub = server_parser.add_subparsers(dest="server_command")

    # Forwarded verbs first — REMAINDER captures every flag verbatim
    # so we can replay them through agentirc.cli.dispatch unchanged.
    for verb in _AGENTIRC_FORWARDED_VERBS:
        forwarded = server_sub.add_parser(
            verb, help=f"(forwarded to agentirc {verb})", add_help=False
        )
        forwarded.add_argument("argv", nargs=argparse.REMAINDER)

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


def _cmd_default(args: argparse.Namespace) -> None:
    """Set the default server name."""
    from pathlib import Path

    from culture_core.config import load_config_or_default
    from culture_core.pidfile import PID_DIR, list_servers, write_default_server

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
        config = load_config_or_default(DEFAULT_CONFIG)
        known_names.add(config.server.name)
    except Exception:
        pass

    if args.name not in known_names:
        known = sorted(known_names)
        if known:
            raise CultureError(
                EXIT_USER_ERROR,
                f"Server '{args.name}' not found (known servers: {', '.join(known)})",
                f"pick a known server, e.g. 'culture server default {known[0]}'",
            )
        raise CultureError(
            EXIT_USER_ERROR,
            f"Server '{args.name}' not found",
            f"start a server first: culture server start --name {args.name}",
        )
    write_default_server(args.name)
    print(f"Default server set to '{args.name}'")


def dispatch(args: argparse.Namespace) -> None:
    verb = getattr(args, "server_command", None)
    if not verb:
        print(
            "Usage: culture server "
            "{start|stop|status|default|rename|archive|unarchive"
            "|restart|link|logs|version|serve}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Forward agentirc-only verbs verbatim. agentirc.cli.dispatch returns
    # an int exit code; sys.exit propagates it so callers see the same
    # rc they would from invoking ``agentirc <verb>`` directly.
    if verb in _AGENTIRC_FORWARDED_VERBS:
        from agentirc.cli import dispatch as _agentirc_dispatch

        forwarded_argv = getattr(args, "argv", None) or []
        sys.exit(_agentirc_dispatch([verb, *forwarded_argv]))

    # Resolve --name for commands that use it (all except default/rename)
    if verb not in ("default", "rename") and hasattr(args, "name"):
        args.name = _resolve_server_name(args)

    handlers = {
        "start": _server_start,
        "stop": _server_stop,
        "status": _server_status,
        "default": _cmd_default,
        "rename": _server_rename,
        "archive": _server_archive,
        "unarchive": _server_unarchive,
    }
    handler = handlers.get(verb)
    if handler is None:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Unknown server command: {verb}",
            "run 'culture server --help' to list the available verbs",
        )
    handler(args)


# -----------------------------------------------------------------------
# Handlers
# -----------------------------------------------------------------------


def _server_rename(args: argparse.Namespace) -> None:
    """Rename the server: update config, agent nicks, and PID files."""
    from culture_core.config import rename_manifest_server, sanitize_agent_name
    from culture_core.pidfile import (
        is_process_alive,
        read_default_server,
        read_pid,
        rename_pid,
        write_default_server,
    )

    try:
        new_name = sanitize_agent_name(args.new_name)
    except ValueError:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Invalid server name: {args.new_name!r}",
            "use a name containing letters, digits, or hyphens, "
            "e.g. 'culture server rename spark2'",
        ) from None

    try:
        old_name, renamed = rename_manifest_server(args.config, new_name)
    except ValueError as exc:
        raise CultureError(
            EXIT_USER_ERROR,
            str(exc),
            f"check the config path ({args.config}) and the current server name "
            "with 'culture server status'",
        ) from exc

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
        print("  culture agents stop --all && culture agents start --all")


def _daemon_child_exit_code(pid: int) -> int | None:
    """Reap-and-detect: return *pid*'s exit code if it has exited, else None.

    Only meaningful from the direct parent of the forked daemon child. A
    child that dies before the parent reaps it becomes a zombie, and
    ``os.kill(pid, 0)`` (``is_process_alive``) reports zombies as alive —
    so the verification loops must use ``os.waitpid(pid, os.WNOHANG)``
    instead. Returns None while the child is still running, and also when
    *pid* is not our child (``ChildProcessError``) so callers can fall back
    to ``is_process_alive``.
    """
    try:
        wpid, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return None
    if wpid == 0:
        return None
    if os.WIFSIGNALED(status):
        return 128 + os.WTERMSIG(status)
    return os.WEXITSTATUS(status)


def _describe_daemon_exit(code: int) -> str:
    """Human-readable failure text for a daemon child that exited early."""
    if code == EXIT_DAEMON_PERMANENT:
        return f"exited with code {code} (permanent error — will not be restarted)"
    return f"exited with code {code}"


def _probe_daemon_failure(pid: int) -> str | None:
    """Return a failure description if the daemon child is gone, else None.

    Prefers the reap-and-detect probe (which sees zombies and knows the
    exit status); falls back to ``is_process_alive`` when *pid* is not our
    child — e.g. it was already reaped elsewhere.
    """
    code = _daemon_child_exit_code(pid)
    if code is not None:
        return _describe_daemon_exit(code)
    if not is_process_alive(pid):
        return "failed to start"
    return None


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
        failure = _probe_daemon_failure(pid)
        if failure:
            return False, failure
        try:
            s = socket.create_connection((check_host, port), timeout=0.5)
            s.close()
        except OSError:
            time.sleep(0.2)
            continue
        time.sleep(0.1)
        failure = _probe_daemon_failure(pid)
        if failure:
            return False, failure
        return True, ""
    return False, "started but not yet accepting connections"


def _maybe_set_default_server(name: str) -> None:
    """Set this server as default if none is configured."""
    from culture_core.pidfile import read_default_server, write_default_server

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
        failure = _probe_daemon_failure(pid)
        if failure:
            print(f"Server '{args.name}' {failure}", file=sys.stderr)
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
    """Raise if the server is archived."""
    from culture_core.config import load_config_or_default

    config_path = getattr(args, "config", DEFAULT_CONFIG)
    config = load_config_or_default(config_path)
    if config.server.name == args.name and config.server.archived:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Server '{args.name}' is archived",
            f"unarchive first: culture server unarchive --name {args.name}",
        )


def _check_already_running(pid_name: str, name: str) -> None:
    """Raise if the server is already running."""
    existing = read_pid(pid_name)
    if existing and is_process_alive(existing):
        raise CultureError(
            EXIT_USER_ERROR,
            f"Server '{name}' is already running (PID {existing})",
            f"stop it first with 'culture server stop --name {name}' " "if you want to restart it",
        )


def _resolve_server_links(args: argparse.Namespace) -> list:
    """Resolve link configs from CLI args or mesh config."""
    if getattr(args, "mesh_config", None):
        return resolve_links_from_mesh(args.mesh_config)
    return args.link


def _daemonize_server(args: argparse.Namespace, pid_name: str, links: list) -> None:
    """Fork and set up the daemon child process for the server."""
    if sys.platform == "win32":
        raise CultureError(
            EXIT_USER_ERROR,
            "Daemon mode not supported on Windows",
            f"run in the foreground instead: culture server start --name {args.name} --foreground",
        )

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

    # Use an explicit FileHandler: logging.StreamHandler on sys.stderr
    # inherits stderr's buffering from interpreter startup. After dup2'ing
    # fd 2 to a log file, those writes can buffer indefinitely and make
    # the daemon's runtime log appear frozen.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path)],
        force=True,
    )

    write_pid(pid_name, os.getpid())

    # The daemon child must report its actual outcome to the OS so service
    # managers (systemd, monit, supervisord) can detect crashes and restart.
    # Wrapping `asyncio.run(...)` in a `finally: os._exit(0)` masks every
    # failure as success — pre-A3 bug surfaced by Qodo on PR #320.
    # Crashes follow the exit contract (#15): permanent config/user errors
    # exit EXIT_DAEMON_PERMANENT so the service manager parks the unit;
    # transient failures exit EXIT_DAEMON_TRANSIENT and self-heal via restart.
    exit_code = 0
    try:
        asyncio.run(
            _run_server(args.name, args.host, args.port, links, args.webhook_port, args.data_dir)
        )
    except KeyboardInterrupt:
        # Clean shutdown path — Ctrl-C from foreground / SIGINT.
        pass
    except SystemExit as exc:  # NOSONAR S5754 — child must report sys.exit codes to the OS
        # sys.exit() inside the child must not fall through to os._exit(0):
        # mirror _passthrough._translate_exit's rc policy.
        exit_code, _ = _translate_exit(exc.code)
        if exit_code:
            logger.exception("daemon child called sys.exit(%r); exiting %d", exc.code, exit_code)
    except Exception as exc:  # noqa: BLE001 - daemon must convert crash to non-zero exit
        exit_code = classify_daemon_exit(exc)
        logger.exception(
            "daemon child crashed; exiting with status %d (%s)",
            exit_code,
            (
                "permanent — service manager should not restart"
                if exit_code == EXIT_DAEMON_PERMANENT
                else "transient — service manager may restart"
            ),
        )
    finally:
        remove_pid(pid_name)
        os._exit(exit_code)


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
    from agentirc.config import ServerConfig
    from agentirc.ircd import IRCd

    import culture_core.bots

    # Bridge culture's system-bot loader into agentirc before the IRCd starts.
    # agentirc 9.7+ `IRCd.start()` owns the BotManager lifecycle — it builds the
    # manager, loads bots, runs `load_system_bots()` (which imports
    # `agentirc.bots.system`), and binds `webhook_port`. agentirc ships no
    # `agentirc.bots.system`, so this registers `culture_core.bots.system` under that
    # name; idempotent (importing culture_core.bots already installed it). See #445.
    culture_core.bots.install_system_bridge()

    config = ServerConfig(
        name=name,
        host=host,
        port=port,
        webhook_port=webhook_port,
        links=links or [],
        data_dir=data_dir,
    )
    ircd = IRCd(config)
    # `IRCd.start()` drives `BotManager.start()` itself (loads bots + system
    # bots + binds the webhook listener once). Do NOT build a second BotManager
    # here — that would re-run `start()` and double-bind `webhook_port` (#445).
    await ircd.start()

    try:
        logger.info("Server '%s' listening on %s:%d", name, host, port)

        for lc in config.links:
            try:
                await ircd.connect_to_peer(lc.host, lc.port, lc.password, lc.trust)
                logger.info("Linking to %s at %s:%d", lc.name, lc.host, lc.port)
            except Exception:
                logger.exception("Failed to link to %s — will retry", lc.name)
                ircd.maybe_retry_link(lc.name)

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop_event.set)
            except RuntimeError:
                # NotImplementedError is a RuntimeError subclass, so this
                # catch covers both Windows (NotImplementedError) and
                # threads-without-event-loop (RuntimeError).
                signal.signal(sig, lambda *_: stop_event.set())

        await stop_event.wait()
    finally:
        logger.info("Server '%s' shutting down", name)
        bm = getattr(ircd, "bot_manager", None)
        if bm is not None:
            try:
                await bm.stop()
            except Exception:
                logger.exception("bot_manager.stop failed")
        try:
            await ircd.stop()
        except Exception:
            # Best-effort shutdown: a teardown failure here would otherwise
            # mask the original exception that broke us out of the body
            # (or turn a clean shutdown into a crash). Log and move on.
            logger.exception("ircd.stop failed")


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
        # NOSONAR S4828: PID was validated by is_process_alive +
        # is_culture_process in _server_stop before reaching this
        # graceful-stop fallback; we are signaling our own daemon.
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def _server_stop(args: argparse.Namespace) -> None:
    pid_name = f"server-{args.name}"
    pid = read_pid(pid_name)

    if pid is None:
        raise CultureError(
            EXIT_USER_ERROR,
            f"No PID file for server '{args.name}' — it does not appear to be running",
            f"check 'culture server status --name {args.name}', "
            f"or start it with 'culture server start --name {args.name}'",
        )

    if not is_process_alive(pid):
        print(f"Server '{args.name}' is not running (stale PID {pid})")
        remove_pid(pid_name)
        return

    if not is_culture_process(pid):
        print(f"PID {pid} is not a culture process — removing stale PID file")
        remove_pid(pid_name)
        return

    print(f"Stopping server '{args.name}' (PID {pid})...")
    # NOSONAR S4828: PID validated above by read_pid + is_process_alive +
    # is_culture_process — we only signal our own daemon, never an
    # arbitrary system process.
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
    """Verify config server name matches the requested name, raise on mismatch."""
    server_name = config.server.name
    if server_name != name:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Server name mismatch: --name '{name}' but config has '{server_name}'",
            f"rerun with --name {server_name}, or pass the config that defines "
            f"'{name}' via --config",
        )
    return server_name


def _update_single_bot_archive(
    yaml_path, bot_config, archive: bool, reason: str, today: str
) -> str | None:
    """Update archive state of a single bot. Return the bot name if changed, else None."""
    from culture_core.bots.config import save_bot_config

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
    from culture_core.bots.config import BOTS_DIR, load_bot_config

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
    from culture_core.config import archive_manifest_server, load_config_or_default

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
    from culture_core.cli.shared.process import stop_agent

    for agent in config.agents:
        agent_pid = read_pid(f"agent-{agent.nick}")
        if agent_pid and is_process_alive(agent_pid):
            stop_agent(agent.nick)

    # Archive server + agents in config
    archived_nicks = archive_manifest_server(args.config, reason=args.reason)

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
    from culture_core.config import load_config_or_default, unarchive_manifest_server

    config = load_config_or_default(args.config)
    server_name = _validate_config_name(config, args.name)

    if not config.server.archived:
        raise CultureError(
            EXIT_USER_ERROR,
            f"Server '{server_name}' is not archived",
            f"nothing to restore — run 'culture server status --name {server_name}' "
            "to check its state",
        )

    # Unarchive server + agents
    unarchived_nicks = unarchive_manifest_server(args.config)

    # Unarchive bots whose owner matches any agent on this server
    agent_nicks = {a.nick for a in config.agents}
    unarchived_bots = _set_bots_archive_state(agent_nicks, archive=False)

    print(f"Server unarchived: {server_name}")
    if unarchived_nicks:
        print(f"  Agents: {', '.join(unarchived_nicks)}")
    if unarchived_bots:
        print(f"  Bots:   {', '.join(unarchived_bots)}")
    print(f"\nStart with: culture server start --name {server_name}")
