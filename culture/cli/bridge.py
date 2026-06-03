"""``culture bridge`` — start / stop / status the per-nick IRC bridge.

The bridge is the transport half of "CC IS the boss" (v9.0.0-rc.1). A
CC session opens an IPC socket to a bridge daemon which holds the
boss's IRC presence; CC is the brain, the bridge is the wire.

Subcommands:

* ``culture bridge start <nick> [--channels …] [--foreground]`` —
  spawn a bridge daemon for *nick*. By default it detaches into the
  background, writes ``~/.culture/run/bridge-<nick>.pid``, and exits 0
  once the child has been launched (does not wait for the bridge to
  reach the IRC server).
* ``culture bridge stop <nick>`` — SIGTERM the recorded PID and
  remove the PID file. Idempotent: a missing PID file or a dead
  process is reported but not a fatal error.
* ``culture bridge status`` — list every recorded PID file under
  ``~/.culture/run/bridge-*.pid`` with a live / stale label.

Implementation: thin wrapper around
``python -m culture.clients.bridge``. The actual daemon lives in
``culture/clients/bridge/__main__.py``; this CLI just deals with PID
files and background detach.
"""

from __future__ import annotations

import argparse
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

from .shared.constants import DEFAULT_CONFIG

NAME = "bridge"


# Same nick shape AgentIRC enforces server-side. Validated up front so
# a typo doesn't end up as part of a file path in ~/.culture/run/.
_NICK_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser(
        "bridge",
        help="Start / stop / status the per-nick IRC bridge daemon (CC-IS-the-boss transport)",
        description=(
            "Manage culture-bridge processes — the thin IRC-transport "
            "daemon that holds a boss nick's IRC seat while the CC "
            "session is the actual brain."
        ),
    )
    sub = p.add_subparsers(dest="bridge_command")

    s = sub.add_parser("start", help="Start a bridge daemon for <nick>.")
    s.add_argument("nick", help="Boss nick this bridge will hold.")
    s.add_argument("--config", default=DEFAULT_CONFIG, help="Server config path.")
    s.add_argument(
        "--channels",
        nargs="*",
        default=None,
        metavar="CHAN",
        help=(
            "Channels to join. Defaults to the manifest entry's "
            "channels if registered, else empty."
        ),
    )
    s.add_argument(
        "--tag",
        action="append",
        dest="tags",
        default=None,
        metavar="TAG",
        help="Add a tag to the agent (repeatable). Default: 'bridge'.",
    )
    s.add_argument(
        "--foreground",
        action="store_true",
        help=(
            "Run in the foreground (don't detach). Useful for debugging; "
            "blocks the shell until Ctrl-C."
        ),
    )

    st = sub.add_parser("stop", help="Stop the bridge daemon for <nick>.")
    st.add_argument("nick", help="Boss nick whose bridge to stop.")

    sub.add_parser("status", help="List every bridge PID file with a live/stale label.")


# ----------------------------------------------------------------------
# PID-file helpers — same shape culture/cli/agent.py uses for agents.
# ----------------------------------------------------------------------


def _run_dir() -> str:
    """``~/.culture/run/`` — created on demand at mode 0o700."""
    p = os.path.expanduser("~/.culture/run")
    os.makedirs(p, mode=0o700, exist_ok=True)
    return p


def _pid_path(nick: str) -> str:
    return os.path.join(_run_dir(), f"bridge-{nick}.pid")


def _read_pid(pid_path: str) -> int | None:
    """Return the PID written in *pid_path*, or None if missing/invalid."""
    try:
        with open(pid_path, encoding="utf-8") as fh:
            value = fh.read().strip()
        return int(value) if value else None
    except (OSError, ValueError):
        return None


def _is_alive(pid: int) -> bool:
    """Cheap liveness check via signal 0 — POSIX-portable."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# ----------------------------------------------------------------------
# Dispatch
# ----------------------------------------------------------------------


def dispatch(args: argparse.Namespace) -> None:
    subcmd = getattr(args, "bridge_command", None)
    if subcmd == "start":
        _cmd_start(args)
    elif subcmd == "stop":
        _cmd_stop(args)
    elif subcmd == "status":
        _cmd_status(args)
    else:
        # No subcommand → print help shape via raising.
        print("Usage: culture bridge {start|stop|status} ...", file=sys.stderr)
        sys.exit(1)


def _validate_nick(nick: str) -> None:
    if not _NICK_RE.fullmatch(nick):
        print(
            f"Error: invalid nick {nick!r} — " "must match ^[A-Za-z][A-Za-z0-9_-]{0,63}$",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_start(args: argparse.Namespace) -> None:
    nick = args.nick
    _validate_nick(nick)
    pid_path = _pid_path(nick)

    # If a PID file exists, refuse to start a second bridge for the same
    # nick. A stale PID (dead process) is cleaned up silently — same
    # pattern culture/cli/agent.py uses.
    existing_pid = _read_pid(pid_path)
    if existing_pid is not None:
        if _is_alive(existing_pid):
            print(
                f"bridge for {nick!r} is already running (pid {existing_pid})",
                file=sys.stderr,
            )
            sys.exit(1)
        # Stale — clean and proceed.
        try:
            os.unlink(pid_path)
        except OSError:
            pass

    cmd = [sys.executable, "-m", "culture.clients.bridge", "start", nick, "--config", args.config]
    if args.channels is not None:
        cmd.append("--channels")
        cmd.extend(args.channels)
    for tag in args.tags or []:
        cmd.extend(["--tag", tag])

    if args.foreground:
        # Pass through stdio; child takes over the terminal.
        rc = subprocess.run(cmd).returncode
        sys.exit(rc)

    # Background: detach into a new session so the bridge survives a
    # parent shell exit. Logs are discarded — the bridge writes its
    # own daemon-log to ~/.culture/log/<nick>.log via DaemonLog.
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        with open(pid_path, "w", encoding="utf-8") as fh:
            fh.write(str(proc.pid))
        os.chmod(pid_path, 0o600)
    except OSError as exc:
        # Couldn't write the PID file — kill the orphan we just spawned
        # so we don't leave a bridge running with no record of it.
        try:
            proc.terminate()
        except OSError:
            pass
        print(f"Error: could not write PID file {pid_path}: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"bridge for {nick!r} started (pid {proc.pid})")
    print(f"  PID file: {pid_path}")
    print(f"  Stop with: culture bridge stop {nick}")


def _cmd_stop(args: argparse.Namespace) -> None:
    nick = args.nick
    _validate_nick(nick)
    pid_path = _pid_path(nick)
    pid = _read_pid(pid_path)
    if pid is None:
        print(f"no PID file for {nick!r} ({pid_path})")
        return
    if not _is_alive(pid):
        print(f"bridge for {nick!r} was already dead (stale pid {pid}) — cleaned up")
        try:
            os.unlink(pid_path)
        except OSError:
            pass
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"Error: could not signal pid {pid}: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        os.unlink(pid_path)
    except OSError:
        pass
    print(f"bridge for {nick!r} stopped (pid {pid})")


def _cmd_status(_args: argparse.Namespace) -> None:
    run_dir = Path(os.path.expanduser("~/.culture/run"))
    if not run_dir.is_dir():
        print("no bridges running")
        return

    rows: list[tuple[str, str, int]] = []
    for entry in sorted(run_dir.iterdir()):
        if not entry.name.startswith("bridge-") or not entry.name.endswith(".pid"):
            continue
        nick = entry.name[len("bridge-") : -len(".pid")]
        pid = _read_pid(str(entry))
        if pid is None:
            rows.append((nick, "broken", 0))
        elif _is_alive(pid):
            rows.append((nick, "running", pid))
        else:
            rows.append((nick, "stale", pid))

    if not rows:
        print("no bridges running")
        return

    print(f"{'NICK':30s} {'STATUS':10s} PID")
    print("-" * 50)
    for nick, status, pid in rows:
        pid_repr = "-" if pid == 0 else str(pid)
        print(f"{nick:30s} {status:10s} {pid_repr}")
