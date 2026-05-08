"""`culture console` — passthrough to the standalone irc-lens CLI.

irc-lens (https://github.com/agentculture/irc-lens) is the
agent-driven web console for AgentIRC: a localhost aiohttp + HTMX +
SSE app implementing the same console as a browser-driveable surface.
Culture embeds it as a first-class namespace so the culture CLI exposes
the lens with culture-aware ergonomics:

    culture console <server_name>     -> resolves to host/port/nick
    culture console serve --host ...  -> pure passthrough
    culture console explain           -> irc-lens explain (passthrough)
    culture console stop [--web-port] -> stop a locally-running console

The full design lives in
``docs/superpowers/specs/2026-05-05-culture-console-design.md``. The
port-conflict UX (per-port pidfile + same/different-target detection
+ ``stop`` verb) is documented in ``docs/reference/cli/console.md``.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

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

# `stop` is the one culture-owned verb — handled before passthrough.
# It shadows any culture server literally named "stop".

# Per-port slot keys under ~/.culture/pids/. A `culture console` on port
# 8765 owns `console-8765.{pid,port,json}`; a side-by-side run on 8766
# owns `console-8766.*`. This lets multiple consoles coexist without
# stepping on each other's metadata.
_PID_PREFIX = "console-"
_DEFAULT_WEB_PORT = 8765
_STOP_GRACE_SECONDS = 5.0
_TARGET_KEYS = ("server_name", "nick", "host", "irc_port")


def _pid_slot(web_port: int) -> str:
    return f"{_PID_PREFIX}{web_port}"


# --- sidecar JSON helpers -------------------------------------------------


def _sidecar_path(web_port: int) -> Path:
    return Path(pidfile.PID_DIR) / f"{_pid_slot(web_port)}.json"


def _read_sidecar(web_port: int) -> dict | None:
    path = _sidecar_path(web_port)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _write_sidecar(web_port: int, data: dict) -> None:
    pid_dir = Path(pidfile.PID_DIR)
    pid_dir.mkdir(parents=True, exist_ok=True)
    _sidecar_path(web_port).write_text(json.dumps(data, indent=2))


def _remove_sidecar(web_port: int) -> None:
    try:
        _sidecar_path(web_port).unlink()
    except FileNotFoundError:
        pass


def _cleanup_state(web_port: int) -> None:
    slot = _pid_slot(web_port)
    pidfile.remove_pid(slot)
    pidfile.remove_port(slot)
    _remove_sidecar(web_port)


# --- argv parsing ---------------------------------------------------------


def _normalise_argv(argv: list[str]) -> list[str]:
    """Split ``--name=val`` tokens into ``--name`` ``val`` so the simple
    flag-pair scanner below sees both forms. argparse and irc-lens accept
    both, but the shim's hand-rolled loop only matches the two-token form.
    """
    out: list[str] = []
    for tok in argv:
        if tok.startswith("--") and len(tok) > 2 and "=" in tok:
            key, val = tok.split("=", 1)
            out.append(key)
            out.append(val)
        else:
            out.append(tok)
    return out


def _coerce_int(value: str, fallback: int) -> int:
    try:
        return int(value)
    except ValueError:
        return fallback


def _parse_serve_argv(argv: list[str]) -> tuple[int, dict[str, Any]]:
    """Extract web_port and target identity from a serve argv.

    Tolerant of unknown flags — irc-lens may grow new ones. Only the
    four flags that identify the target are read. ``server_name`` is
    *not* derived from nick here (hyphenated server names break the
    split); the caller (``dispatch``) overrides it from
    ``_build_serve_argv``'s resolved server when known.
    """
    target: dict[str, Any] = {
        "server_name": None,
        "nick": None,
        "host": "127.0.0.1",
        "irc_port": 6667,
        "web_port": _DEFAULT_WEB_PORT,
    }
    handlers: dict[str, Callable[[str], None]] = {
        "--web-port": lambda v: target.__setitem__("web_port", _coerce_int(v, target["web_port"])),
        "--port": lambda v: target.__setitem__("irc_port", _coerce_int(v, target["irc_port"])),
        "--nick": lambda v: target.__setitem__("nick", v),
        "--host": lambda v: target.__setitem__("host", v),
    }
    norm = _normalise_argv(argv)
    i = 1  # skip the leading 'serve'
    while i < len(norm):
        handler = handlers.get(norm[i])
        nxt = norm[i + 1] if i + 1 < len(norm) else None
        if handler is not None and nxt is not None:
            handler(nxt)
            i += 2
        else:
            i += 1
    return target["web_port"], target


# --- port / fingerprint probes --------------------------------------------


def _port_in_use(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.2)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False


def _looks_like_irc_lens(port: int) -> bool:
    # http on loopback only — there is no MITM path to defend against.
    try:
        with urllib.request.urlopen(  # noqa: S310 # nosec B310
            f"http://127.0.0.1:{port}/", timeout=0.5
        ) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
    except (OSError, ValueError):
        # OSError covers urllib.error.URLError and TimeoutError, both of
        # which are OSError subclasses in modern Python.
        return False
    return "irc-lens" in body.lower()


# --- conflict hints -------------------------------------------------------


def _same_target(sidecar: dict, target: dict) -> bool:
    """Two consoles point at the same target iff every identity key matches.

    Treats ``server_name=None`` as a valid value (pure-passthrough
    invocations have no server-name; their nick/host/port still form a
    stable identity).
    """
    return all(sidecar.get(k) == target.get(k) for k in _TARGET_KEYS)


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
    elif other_nick:
        other = f"nick {other_nick!r}"
    else:
        other = "another target"
    next_port = web_port + 1
    return (
        f"culture console is already running for {other} on {url}\n"
        "What to do:\n"
        f"  - Open the existing console: {url}\n"
        f"  - Stop it and start fresh:   culture console stop --web-port {web_port} && culture console {requested_server}\n"
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

    Decision tree (in order):

    1. **Port not bound** -> clean any stale slot for this port and
       return. Nothing to detect.
    2. **Slot owns the bound port and matches our target tuple** ->
       exit 0 with "already running" (the user re-ran the same thing).
    3. **Slot owns the bound port but a different target** -> exit 1
       with a 3-bullet hint.
    4. **Bound port is irc-lens but no slot points at it** -> exit 1
       with the foreign-irc-lens hint.
    5. **Bound port is something else** -> return; let irc-lens emit
       its own bind error (the right message for arbitrary owners).
    """
    if not _port_in_use(web_port):
        # Port is free. Any existing slot for this port is stale —
        # clean it up best-effort and let irc-lens proceed.
        slot = _pid_slot(web_port)
        if pidfile.read_pid(slot) is not None:
            _cleanup_state(web_port)
        return

    slot = _pid_slot(web_port)
    pid = pidfile.read_pid(slot)
    recorded_port = pidfile.read_port(slot)
    sidecar = _read_sidecar(web_port)

    pidfile_owns_port = (
        pid is not None
        and pidfile.is_process_alive(pid)
        and pidfile.is_culture_process(pid)
        and recorded_port == web_port
    )

    if pidfile_owns_port:
        sidecar = sidecar or {}
        if _same_target(sidecar, target):
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

    # Slot is stale (dead PID or non-culture cmdline). Drop it before
    # we report on the actual owner.
    if pid is not None:
        _cleanup_state(web_port)

    if _looks_like_irc_lens(web_port):
        print(_foreign_irc_lens_hint(web_port), file=sys.stderr)
        sys.exit(1)


# --- pidfile lifecycle ----------------------------------------------------


def _register_state(web_port: int, target: dict[str, Any]) -> None:
    """Record this process as the live culture console for ``web_port``.

    Best-effort: filesystem errors here should not block irc-lens from
    starting (the worst case is loss of conflict-detection on a future
    invocation). Cleanup is the caller's responsibility — see the
    ``try/finally`` in :func:`_run_serve` — *not* an ``atexit`` hook,
    which would fire after pytest's ``monkeypatch`` undo and delete
    the developer's real ``~/.culture/pids/`` files.
    """
    slot = _pid_slot(web_port)
    try:
        pidfile.write_pid(slot, os.getpid())
        pidfile.write_port(slot, web_port)
        _write_sidecar(web_port, {"pid": os.getpid(), **target})
    except OSError:
        pass


# --- stop verb ------------------------------------------------------------


def _parse_stop_argv(argv: list[str]) -> int:
    """Extract ``--web-port N`` (or ``--web-port=N``) from a ``stop`` argv.

    ``argv[0]`` is the literal ``"stop"`` token; the rest may carry
    flags. Defaults to 8765 to match the irc-lens default.
    """
    norm = _normalise_argv(argv[1:])
    i = 0
    while i < len(norm):
        if norm[i] == "--web-port" and i + 1 < len(norm):
            try:
                return int(norm[i + 1])
            except ValueError:
                break
        i += 1
    return _DEFAULT_WEB_PORT


def _cmd_stop(args: argparse.Namespace) -> int:
    raw = list(getattr(args, "console_args", []) or [])
    web_port = _parse_stop_argv(raw)
    slot = _pid_slot(web_port)
    pid = pidfile.read_pid(slot)
    if pid is None:
        print(f"no culture console running on port {web_port}.", file=sys.stderr)
        return 0
    if not pidfile.is_process_alive(pid):
        _cleanup_state(web_port)
        print(
            f"culture console pidfile for port {web_port} pointed at dead pid {pid}; cleaned up.",
            file=sys.stderr,
        )
        return 0
    if not pidfile.is_culture_process(pid):
        print(
            f"refusing to stop pid {pid}: not a culture process. "
            f"Remove {Path(pidfile.PID_DIR) / (slot + '.pid')} manually if you're sure.",
            file=sys.stderr,
        )
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _cleanup_state(web_port)
        print(f"culture console pid {pid} already gone.", file=sys.stderr)
        return 0
    deadline = time.monotonic() + _STOP_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not pidfile.is_process_alive(pid):
            _cleanup_state(web_port)
            print(f"stopped culture console (pid {pid}, port {web_port}).", file=sys.stderr)
            return 0
        time.sleep(0.1)
    # Re-validate identity before SIGKILL — PID reuse during the grace
    # window could otherwise let SIGKILL hit a recycled, unrelated
    # process. Mirrors the fail-closed pattern in
    # ``culture/cli/shared/process.py``.
    if pidfile.is_process_alive(pid) and pidfile.is_culture_process(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    _cleanup_state(web_port)
    print(f"force-stopped culture console (pid {pid}, port {web_port}).", file=sys.stderr)
    return 0


# --- entry point ----------------------------------------------------------


def _invoke_irc_lens(argv: list[str]) -> "int | None":
    try:
        from irc_lens.cli import main
    except ImportError as exc:  # pragma: no cover — declared dep
        print(f"irc-lens is not installed: {exc}", file=sys.stderr)
        sys.exit(2)
    return main(argv)


def _argv_has_flag(argv: list[str], flag: str) -> bool:
    """Return True iff ``flag`` (or ``flag=value``) appears in ``argv``.

    Used to detect a user-supplied ``--config`` so the auto-init below
    only runs when the user is relying on irc-lens's default path.
    """
    for tok in argv:
        if tok == flag or tok.startswith(f"{flag}="):
            return True
    return False


def _ensure_default_irc_lens_config() -> None:
    """Auto-initialize irc-lens's default config if it's missing.

    irc-lens 0.5.x's ``serve`` requires an explicit config file; on a
    fresh machine the user otherwise hits a cryptic
    ``no config at ~/.config/irc-lens/config.yaml`` error. This bridge
    makes ``culture console <server>`` and bare-passthrough
    ``culture console serve …`` (without ``--config``) succeed on
    first-run by writing a starter dev-mode config to the default path.
    The user keeps full control: if they pass ``--config``, this
    function is bypassed entirely (the caller checks the flag first).
    """
    try:
        from irc_lens.config import default_config_path
    except ImportError:  # pragma: no cover — declared dep
        return
    path = default_config_path()
    if path.exists():
        return
    # Delegate to irc-lens's own init so the schema stays owned upstream.
    _invoke_irc_lens(["config", "init", "--path", str(path)])


def _run_serve(argv: list[str], server_name: str | None) -> "int | None":
    """Wrap ``_invoke_irc_lens`` with conflict detection + state cleanup.

    The ``try/finally`` binds cleanup to this call (rather than an
    ``atexit`` hook), so test harnesses that patch ``pidfile.PID_DIR``
    to a tmpdir get correct teardown without risk to the developer's
    real ``~/.culture/pids/`` files on pytest exit.
    """
    web_port, target = _parse_serve_argv(argv)
    if server_name is not None:
        target["server_name"] = server_name
    _check_port_conflict(web_port, target)
    if not _argv_has_flag(argv, "--config"):
        _ensure_default_irc_lens_config()
    _register_state(web_port, target)
    try:
        return _invoke_irc_lens(argv)
    finally:
        _cleanup_state(web_port)


def _entry(argv: list[str]) -> "int | None":
    """Bare passthrough entry — used by universal verbs (``explain`` /
    ``overview`` / ``learn``) that don't bind a port.

    The ``serve`` path goes through :func:`_run_serve` instead, via the
    closure built in :func:`dispatch`.
    """
    return _invoke_irc_lens(argv)


def _make_serve_entry(server_name: str | None) -> Callable[[list[str]], "int | None"]:
    def entry(argv: list[str]) -> "int | None":
        return _run_serve(argv, server_name)

    return entry


def _resolve_argv(argv: list[str]) -> tuple[list[str], str | None]:
    """Translate ``culture console`` argv into ``(irc-lens argv, server_name)``.

    The second element of the return is the resolved culture server
    name when the shim rewrote a positional into a ``serve`` call, or
    ``None`` when the user invoked an irc-lens verb directly. The
    caller threads ``server_name`` to ``_run_serve`` so target
    comparison doesn't have to derive it from a hyphen-split nick (a
    server literally named ``my-server`` has the nick
    ``my-server-<user>``, and ``split('-', 1)[0]`` would mistakenly
    yield ``my``).

    Note: ``stop`` is intercepted earlier in :func:`dispatch` and never
    reaches this function.
    """
    if not argv:
        return _build_serve_argv(server_name=None, rest=[])
    if argv[0] == "--":
        # Common shell habit to disambiguate passthrough args; argparse
        # REMAINDER chokes on a leading `--`.
        argv = argv[1:]
        if not argv:
            return _build_serve_argv(server_name=None, rest=[])
    head = argv[0]
    if head == "help":
        # `help` is irc-lens-flavoured shorthand for `--help`.
        return ["--help"], None
    if head in _IRC_LENS_VERBS or head.startswith("-"):
        return list(argv), None
    return _build_serve_argv(server_name=head, rest=list(argv[1:]))


def _build_serve_argv(server_name: str | None, rest: list[str]) -> tuple[list[str], str | None]:
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
    return (
        [
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--nick",
            nick,
            *rest,
        ],
        name,
    )


def dispatch_resolved_argv(server_name: str | None) -> None:
    """Used by the legacy ``culture mesh console`` deprecation alias.

    Mirrors the old TUI's invocation surface: just a server name (or
    ``None`` for the default).
    """
    argv, resolved = _resolve_argv([server_name] if server_name else [])
    _passthrough.run(_make_serve_entry(resolved), argv)


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
    verb = next(iter(raw), None)
    if verb == "stop":
        sys.exit(_cmd_stop(args))
    argv, server_name = _resolve_argv(raw)
    if argv and argv[0] == "serve":
        _passthrough.run(_make_serve_entry(server_name), argv)
        return
    _passthrough.run(_entry, argv)
