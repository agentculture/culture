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
import signal
import subprocess
import sys
import time
from pathlib import Path

from culture.pidfile import is_culture_process, is_process_alive

from .shared.constants import DEFAULT_CONFIG

NAME = "bridge"


def _valid_nick(nick: str) -> bool:
    """Enforce the canonical Culture nick format: ``<server>-<agent>``.

    Mirrors ``culture/cli/channel.py::_valid_nick`` and the
    server-side AgentIRC enforcement (Rule 428343 / Qodo PR #51 #1).
    Splits on the FIRST hyphen — the agent half may itself contain
    hyphens (``local-st4ck-boss`` parses as
    ``server=local``, ``agent=st4ck-boss``).
    """
    parts = nick.split("-", 1)
    return len(parts) == 2 and all(parts)


# How long ``stop`` waits for SIGTERM to take effect before either
# escalating (POSIX) or giving up (Windows). 10 × 0.1s = 1s — matches
# the cadence used by ``culture/cli/shared/process.py`` and is
# generous enough for a normal asyncio loop teardown.
_STOP_WAIT_ITERATIONS = 50  # × 0.1s = 5s, same as agent stop logic


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
    """``~/.culture/run/`` — created on demand at mode 0o700.

    ``os.makedirs(mode=...)`` only applies the mode to NEWLY-created
    directories AND is further masked by the process umask. A
    pre-existing dir created with a more permissive umask, or a
    chmod-relaxed earlier deploy, would silently retain those broader
    permissions. Qodo PR #51 #5: enforce 0o700 with an explicit
    ``chmod`` after creation — same defense-in-depth pattern that
    other runtime-dir helpers in the codebase use.
    """
    p = os.path.expanduser("~/.culture/run")
    os.makedirs(p, mode=0o700, exist_ok=True)
    try:
        os.chmod(p, 0o700)
    except OSError:
        # Non-fatal: the daemon will still work, but a multi-user
        # host now has a wider PID-file exposure than we'd like. The
        # PID file itself is still chmod 0o600 (see ``_cmd_start``)
        # so the actual blast radius is small.
        pass
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


# Backward-compat alias kept because the tests patch ``bridge._is_alive``.
# Qodo PR #51 #3: every signalling path now ALSO verifies via
# ``is_culture_process`` so a PID-reuse window cannot point us at
# someone else's process.
_is_alive = is_process_alive


def _is_our_bridge(pid: int) -> bool:
    """True iff *pid* is alive AND is a culture process.

    The bridge's argv contains ``-m culture.clients.bridge`` so
    ``is_culture_process`` returns True for any live bridge daemon.
    On platforms without ``/proc`` (macOS, Windows), the helper
    returns True by default (can't verify) — we accept that
    operating-system limitation; PID reuse defense is best-effort
    on those platforms and the PID file itself is the operator's
    responsibility there. Linux gets the full check.
    """
    return is_process_alive(pid) and is_culture_process(pid)


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
    """Reject malformed nicks at the CLI boundary.

    Two checks:

    - **Length cap** (64 chars) — defensive against runaway PID-file
      names and keeps the wire shape RFC-2812-friendly.
    - **Canonical format** — ``<server>-<agent>`` per the project's
      identifier rule (Rule 428343 / Qodo PR #51 #1). Bare strings
      like ``ABC`` / ``x`` are rejected here even though the bridge
      daemon would technically accept them — the consequence of
      letting them through is identity-drift across the dashboard,
      manifest, channel routing, and DM spool.
    """
    if not nick or len(nick) > 64 or any(c.isspace() or c in "/\\;" for c in nick):
        print(f"Error: invalid nick {nick!r} — illegal characters or length", file=sys.stderr)
        sys.exit(1)
    if not _valid_nick(nick):
        print(
            f"Error: invalid nick {nick!r} — " "must match <server>-<agent> format (Rule 428343)",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_start(args: argparse.Namespace) -> None:
    nick = args.nick
    _validate_nick(nick)
    pid_path = _pid_path(nick)

    # If a PID file exists, decide what to do with it. Qodo PR #51 #3:
    # a bare liveness check (signal 0) is NOT enough — between the
    # bridge's death and us reading the PID file the OS may have
    # recycled that PID for an unrelated process. We refuse to refuse-
    # the-start unless the live PID is actually a culture process.
    existing_pid = _read_pid(pid_path)
    if existing_pid is not None:
        if _is_our_bridge(existing_pid):
            print(
                f"bridge for {nick!r} is already running (pid {existing_pid})",
                file=sys.stderr,
            )
            sys.exit(1)
        # Either dead OR alive-but-not-ours. Either way, the PID file
        # is stale; clean it and proceed with the fresh launch.
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
    if not is_process_alive(pid):
        print(f"bridge for {nick!r} was already dead (stale pid {pid}) — cleaned up")
        try:
            os.unlink(pid_path)
        except OSError:
            pass
        return

    # Qodo PR #51 #3: verify the PID still owns a culture process
    # BEFORE signalling. The OS may have recycled the PID between
    # the bridge's death and this stop call — sending SIGTERM blindly
    # could kill an unrelated user process.
    if not is_culture_process(pid):
        print(
            f"PID {pid} is not a culture process — refusing to signal "
            "(probably PID reuse after the original bridge exited). "
            "Cleaning up stale PID file."
        )
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

    # Qodo PR #51 #4: WAIT for the process to actually exit before
    # removing the PID file. Otherwise a slow shutdown lets a second
    # bridge start under the same nick (the PID file is gone, so the
    # first-writer guard doesn't fire) and a follow-up stop has no
    # PID to signal. Same pattern culture/cli/shared/process.py uses
    # for the agent stop path.
    for _ in range(_STOP_WAIT_ITERATIONS):
        if not is_process_alive(pid):
            try:
                os.unlink(pid_path)
            except OSError:
                pass
            print(f"bridge for {nick!r} stopped (pid {pid})")
            return
        time.sleep(0.1)

    # Process did not exit within the wait window. Don't remove the PID
    # file — leave it for the operator to investigate, and exit
    # non-zero so a script wrapping `culture bridge stop` notices.
    print(
        f"bridge for {nick!r} did not exit within "
        f"{_STOP_WAIT_ITERATIONS * 0.1:.1f}s after SIGTERM (pid {pid}); "
        "PID file kept for investigation.",
        file=sys.stderr,
    )
    sys.exit(1)


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
        elif not is_process_alive(pid):
            rows.append((nick, "stale", pid))
        elif not is_culture_process(pid):
            # Alive but the PID has been recycled to something else;
            # surface separately so the operator notices.
            rows.append((nick, "reused", pid))
        else:
            rows.append((nick, "running", pid))

    if not rows:
        print("no bridges running")
        return

    print(f"{'NICK':30s} {'STATUS':10s} PID")
    print("-" * 50)
    for nick, status, pid in rows:
        pid_repr = "-" if pid == 0 else str(pid)
        print(f"{nick:30s} {status:10s} {pid_repr}")
