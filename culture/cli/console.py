"""`culture console` — passthrough to the standalone irc-lens CLI.

irc-lens (https://github.com/agentculture/irc-lens) is the
agent-driven web console for AgentIRC: a localhost aiohttp + HTMX +
SSE app implementing the same console as a browser-driveable surface.
Culture embeds it as a first-class namespace so the culture CLI exposes
the lens with culture-aware ergonomics:

    culture console <server_name>     -> resolves to host/port/nick
    culture console serve --host ...  -> pure passthrough
    culture console explain           -> irc-lens explain (passthrough)
    culture console stop              -> stop the locally-running console

The full design lives in
``docs/superpowers/specs/2026-05-05-culture-console-design.md``. The
port-conflict UX (pidfile + same/different-target detection + ``stop``
verb) is documented in ``docs/reference/cli/console.md``.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from culture import pidfile
from culture.cli import _passthrough
from culture.cli.shared.console_helpers import resolve_console_nick as _resolve_console_nick
from culture.cli.shared.console_helpers import resolve_server as _resolve_server

NAME = "console"

# Top-level subcommands of irc-lens, verified by `irc-lens --help`.
# Anything in this set means the user typed an irc-lens command directly,
# so the shim must NOT rewrite — pure passthrough.
# `help` is included so a bare `culture console help` is treated as a
# request for help rather than a server name (common typo for `--help`).
_IRC_LENS_VERBS = frozenset({"learn", "explain", "overview", "serve", "cli", "help"})

# Culture-owned verbs (handled before passthrough). `stop` is reserved —
# it shadows any culture server literally named "stop".
_CULTURE_VERBS = frozenset({"stop"})

# Pidfile/portfile/sidecar key under ~/.culture/pids/. Single global slot
# because irc-lens binds a single web port per host.
_PID_NAME = "console"
_DEFAULT_WEB_PORT = 8765
_STOP_GRACE_SECONDS = 5.0


# --- sidecar JSON helpers -------------------------------------------------


def _sidecar_path() -> Path:
    return Path(pidfile.PID_DIR) / f"{_PID_NAME}.json"


def _read_sidecar() -> dict | None:
    path = _sidecar_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_sidecar(data: dict) -> None:
    pid_dir = Path(pidfile.PID_DIR)
    pid_dir.mkdir(parents=True, exist_ok=True)
    _sidecar_path().write_text(json.dumps(data, indent=2))


def _remove_sidecar() -> None:
    try:
        _sidecar_path().unlink()
    except FileNotFoundError:
        pass


def _cleanup_state() -> None:
    pidfile.remove_pid(_PID_NAME)
    pidfile.remove_port(_PID_NAME)
    _remove_sidecar()


# --- argv parsing ---------------------------------------------------------


def _parse_serve_argv(argv: list[str]) -> tuple[int, dict[str, Any]]:
    """Extract web_port and target identity from a serve argv.

    Tolerant of unknown flags — irc-lens may grow new ones. We only care
    about the four flags that identify the target.
    """
    web_port = _DEFAULT_WEB_PORT
    nick: str | None = None
    host = "127.0.0.1"
    irc_port = 6667
    i = 1  # skip the leading 'serve'
    while i < len(argv):
        tok = argv[i]
        nxt = argv[i + 1] if i + 1 < len(argv) else None
        if tok == "--web-port" and nxt is not None:
            try:
                web_port = int(nxt)
            except ValueError:
                pass
            i += 2
        elif tok == "--nick" and nxt is not None:
            nick = nxt
            i += 2
        elif tok == "--host" and nxt is not None:
            host = nxt
            i += 2
        elif tok == "--port" and nxt is not None:
            try:
                irc_port = int(nxt)
            except ValueError:
                pass
            i += 2
        else:
            i += 1
    server_name: str | None = None
    if nick and "-" in nick:
        # Nick format: "<server>-<suffix>" (see CLAUDE.md "Nick Format").
        server_name = nick.split("-", 1)[0]
    return web_port, {
        "server_name": server_name,
        "nick": nick,
        "host": host,
        "irc_port": irc_port,
        "web_port": web_port,
    }


# --- port / fingerprint probes --------------------------------------------


def _port_in_use(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex((host, port)) == 0
    except OSError:
        return False


def _looks_like_irc_lens(host: str, port: int) -> bool:
    """Best-effort fingerprint: GET / and look for irc-lens in the body."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=0.5) as resp:  # noqa: S310
            body = resp.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError, ValueError):
        return False
    return "irc-lens" in body.lower()


# --- conflict hints -------------------------------------------------------


def _same_target_message(server_name: str | None, web_port: int) -> str:
    url = f"http://127.0.0.1:{web_port}/"
    label = f"server {server_name!r}" if server_name else "the same target"
    return f"culture console is already running for {label} at {url}"


def _different_target_hint(
    other_server: str | None,
    other_nick: str | None,
    web_port: int,
    requested_server: str,
) -> str:
    url = f"http://127.0.0.1:{web_port}/"
    if other_server and other_nick:
        other = f"{other_server!r} ({other_nick})"
    elif other_server:
        other = repr(other_server)
    else:
        other = "another target"
    next_port = web_port + 1
    return (
        f"culture console is already running for {other} on {url}\n"
        "What to do:\n"
        f"  - Open the existing console: {url}\n"
        f"  - Stop it and start fresh:   culture console stop && culture console {requested_server}\n"
        f"  - Or run side-by-side:       culture console {requested_server} --web-port {next_port}"
    )


def _foreign_irc_lens_hint(web_port: int) -> str:
    next_port = web_port + 1
    return (
        f"port {web_port} is already serving an irc-lens instance, but it wasn't started by `culture console`.\n"
        "What to do:\n"
        f"  - Find the owner:        ss -tlnp 'sport = :{web_port}'  (or: lsof -iTCP:{web_port} -sTCP:LISTEN)\n"
        "  - Stop that process manually, then retry\n"
        f"  - Or pick another port:  culture console <server> --web-port {next_port}"
    )


# --- pre-flight conflict detection ----------------------------------------


def _check_port_conflict(web_port: int, target: dict[str, Any]) -> None:
    """Pre-flight check before irc-lens binds.

    Exits 0 with a friendly message if the same console is already
    serving the same target. Exits 1 with a 3-bullet hint if a culture
    console is serving a different target on this port. Cleans up stale
    pidfiles. Falls through (returns) for foreign owners — irc-lens's
    own bind error is the right message in that case.
    """
    pid = pidfile.read_pid(_PID_NAME)
    recorded_port = pidfile.read_port(_PID_NAME)
    sidecar = _read_sidecar()

    pidfile_owns_port = (
        pid is not None
        and pidfile.is_process_alive(pid)
        and pidfile.is_culture_process(pid)
        and recorded_port == web_port
    )

    if pidfile_owns_port:
        sidecar = sidecar or {}
        same_target = (
            sidecar.get("server_name") == target.get("server_name")
            and sidecar.get("nick") == target.get("nick")
            and target.get("server_name") is not None
        )
        if same_target:
            print(_same_target_message(target.get("server_name"), web_port), file=sys.stderr)
            sys.exit(0)
        requested = target.get("server_name") or "<server>"
        print(
            _different_target_hint(
                sidecar.get("server_name"),
                sidecar.get("nick"),
                web_port,
                requested,
            ),
            file=sys.stderr,
        )
        sys.exit(1)

    # Pidfile points at a dead/non-culture process — clean up before retry.
    if pid is not None and not (pidfile.is_process_alive(pid) and pidfile.is_culture_process(pid)):
        _cleanup_state()

    # Fingerprint a port that's bound but pidfile-less. If irc-lens shape,
    # tell the user what we see; otherwise let irc-lens emit its own bind
    # error (the right answer for foreign owners).
    if _port_in_use("127.0.0.1", web_port) and _looks_like_irc_lens("127.0.0.1", web_port):
        print(_foreign_irc_lens_hint(web_port), file=sys.stderr)
        sys.exit(1)


# --- pidfile lifecycle ----------------------------------------------------


def _register_state(web_port: int, target: dict[str, Any]) -> None:
    """Record this process as the live culture console.

    Best-effort: filesystem errors here should not block irc-lens from
    starting (the worst case is loss of conflict-detection on a future
    invocation).
    """
    try:
        pidfile.write_pid(_PID_NAME, os.getpid())
        pidfile.write_port(_PID_NAME, web_port)
        _write_sidecar({"pid": os.getpid(), **target})
    except OSError:
        return
    atexit.register(_cleanup_state)


# --- stop verb ------------------------------------------------------------


def _cmd_stop() -> int:
    pid = pidfile.read_pid(_PID_NAME)
    if pid is None:
        print("no culture console running.", file=sys.stderr)
        return 0
    if not pidfile.is_process_alive(pid):
        _cleanup_state()
        print(f"culture console pidfile pointed at dead pid {pid}; cleaned up.", file=sys.stderr)
        return 0
    if not pidfile.is_culture_process(pid):
        print(
            f"refusing to stop pid {pid}: not a culture process. "
            f"Remove {Path(pidfile.PID_DIR) / (_PID_NAME + '.pid')} manually if you're sure.",
            file=sys.stderr,
        )
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _cleanup_state()
        print(f"culture console pid {pid} already gone.", file=sys.stderr)
        return 0
    deadline = time.monotonic() + _STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not pidfile.is_process_alive(pid):
            _cleanup_state()
            print(f"stopped culture console (pid {pid}).", file=sys.stderr)
            return 0
        time.sleep(0.1)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _cleanup_state()
    print(f"force-stopped culture console (pid {pid}).", file=sys.stderr)
    return 0


# --- entry point ----------------------------------------------------------


def _entry(argv: list[str]) -> "int | None":
    """In-process call into ``irc_lens.cli.main(argv)``.

    irc-lens's ``main`` returns an ``int`` on normal completion and
    raises ``SystemExit`` only for argparse-level exits — the same
    contract afi-cli implements. Both paths are handled by
    :mod:`culture.cli._passthrough`.

    Pre-flight: when argv is a ``serve`` invocation, check for a
    same-port culture console already running and short-circuit if
    found. Always register a pidfile so the *next* invocation can do
    the same check.
    """
    if argv and argv[0] == "serve":
        web_port, target = _parse_serve_argv(argv)
        _check_port_conflict(web_port, target)
        _register_state(web_port, target)
    try:
        from irc_lens.cli import main
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"irc-lens is not installed: {exc}", file=sys.stderr)
        sys.exit(2)
    return main(argv)


def _resolve_argv(argv: list[str]) -> list[str]:
    """Translate ``culture console`` argv into ``irc-lens`` argv.

    - Empty argv -> resolve default culture server, build a ``serve`` call.
    - First token is an irc-lens verb or starts with ``-`` -> pure
      passthrough (return argv unchanged).
    - Otherwise -> treat first token as a culture server name; rewrite to
      ``["serve", "--host", h, "--port", p, "--nick", n, *rest]``.

    Raises ``SystemExit`` with a culture-friendly message when the
    server-name path is taken but no culture servers are running.

    Note: ``stop`` is intercepted earlier in :func:`dispatch` and never
    reaches this function.
    """
    if not argv:
        return _build_serve_argv(server_name=None, rest=[])
    # Strip a leading `--` separator (common shell habit to disambiguate
    # passthrough args). Without this, argparse REMAINDER chokes on `--`.
    if argv[0] == "--":
        argv = argv[1:]
        if not argv:
            return _build_serve_argv(server_name=None, rest=[])
    head = argv[0]
    if head == "help":
        # `help` is irc-lens-flavoured shorthand for `--help`.
        return ["--help"]
    if head in _IRC_LENS_VERBS or head.startswith("-"):
        return list(argv)
    return _build_serve_argv(server_name=head, rest=list(argv[1:]))


def _build_serve_argv(server_name: str | None, rest: list[str]) -> list[str]:
    result = _resolve_server(server_name)
    if result is None:
        if server_name is None:
            raise SystemExit("No culture servers running. Start one with: culture server start")
        raise SystemExit(
            f"No culture server named {server_name!r}. "
            f"Run `culture server status` to see what's running."
        )
    name, port = result
    nick = f"{name}-{_resolve_console_nick()}"
    return [
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--nick",
        nick,
        *rest,
    ]


def dispatch_resolved_argv(server_name: str | None) -> None:
    """Used by the legacy ``culture mesh console`` deprecation alias.

    Mirrors the old TUI's invocation surface: just a server name (or
    ``None`` for the default).
    """
    argv = _resolve_argv([server_name] if server_name else [])
    _passthrough.run(_entry, argv)


_passthrough.register_topic(
    "console",
    _entry,
    explain_argv=["explain"],
    overview_argv=["overview"],
    learn_argv=["learn"],
)


# --- CLI group protocol ---------------------------------------------------


def register(subparsers: "argparse._SubParsersAction") -> None:
    # prefix_chars=chr(0): every token (including --help, --version) is
    # treated as positional and captured in console_args for the shim
    # + irc-lens's argparse parser to handle.
    p = subparsers.add_parser(
        NAME,
        help="Open the irc-lens web console (passthrough)",
        add_help=False,
        prefix_chars=chr(0),
    )
    p.add_argument("console_args", nargs=argparse.REMAINDER, help="Arguments passed to irc-lens")


def dispatch(args: argparse.Namespace) -> None:
    raw = list(getattr(args, "console_args", []) or [])
    if raw and raw[0] in _CULTURE_VERBS:
        verb = raw[0]
        if verb == "stop":
            sys.exit(_cmd_stop())
    _passthrough.run(_entry, _resolve_argv(raw))
