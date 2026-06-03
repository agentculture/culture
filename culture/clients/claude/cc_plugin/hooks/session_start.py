#!/usr/bin/env python3
"""SessionStart hook (Phase 4.3 + 4.9 + 4.11).

Fired by Claude Code on startup, resume, clear, and compact. The hook:

    1. Resolves the project-named boss nick for this cwd (Phase 4.2).
    2. Starts the bridge daemon if not already running.
    3. Spawns any owned workers the manifest references (Phase 4.9).
    4. Emits ``cc_session_start(nick=...)`` to the bridge so the
       bridge marks ``cc_connected=True``.
    5. Drains the offline spool for that nick.
    6. Reads ``~/.culture/mission/<nick>.md`` if present (Phase 4.11).
    7. Returns ``hookSpecificOutput.additionalContext`` containing the
       mesh roster snapshot, spool entries, and mission context as a
       system-reminder-shaped string.

The hook is idempotent across all four firing reasons — re-running it
produces the same on-disk state and the same additionalContext shape
(modulo the inevitable timestamp differences in spool entries).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from typing import Any

# Insert the repo root onto sys.path so the in-repo cc_plugin module is
# importable even when CC's hook subprocess starts with an empty
# PYTHONPATH. The script lives at
# ``<repo>/culture/clients/claude/cc_plugin/hooks/session_start.py`` —
# four parents up is the repo root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Best-effort imports — if the user installed the plugin without the
# ``culture`` package on PYTHONPATH, fall back to the minimal stand-ins
# so SessionStart never hard-fails CC startup.
try:  # pragma: no cover — import guard
    from culture.clients.claude.cc_plugin import _bridge_client
    from culture.clients.claude.cc_plugin._nick_resolver import resolve_project_nick
except ImportError:  # pragma: no cover — fallback path

    def resolve_project_nick(cwd: str) -> str:  # type: ignore[misc]
        return os.environ.get("CULTURE_BOSS_NICK", "local-boss") or "local-boss"

    _bridge_client = None  # type: ignore[assignment]


def _read_stdin_json() -> dict[str, Any]:
    """Read the hook event from stdin, tolerantly. CC always sends one
    JSON object, but if stdin is empty (manual invocation, tests) we
    fall back to an empty dict."""
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _nick_well_formed(nick: str) -> bool:
    """Mirror the ``<server>-<agent>`` validation ``culture bridge``
    applies at its CLI boundary (Rule 428343 / Qodo PR #51 #1). Done
    here as defense-in-depth so the hook does not Popen a child that
    will exit 1 in the dark — see ``_ensure_bridge_running`` for the
    failure-surfacing flow."""
    parts = nick.split("-", 1)
    return len(parts) == 2 and all(parts)


def _ensure_bridge_running(nick: str, repo_root: str) -> str | None:
    """Best-effort: launch ``culture bridge start <nick>`` if no bridge
    socket exists. Returns an error string when the spawn was definitely
    rejected (so additionalContext can surface it honestly); returns
    ``None`` on success or "spawning, will retry asynchronously".

    v9.1.0: routes through ``culture bridge start`` (PR #51) instead of
    ``culture agent start``. v9.1.2: validates the nick up front AND
    waits briefly for the IPC socket to appear, so a fire-and-forget
    Popen no longer hides bridge-spawn failures behind a system-reminder
    that lies about the session being on the mesh.
    """
    if _bridge_client is None:
        return "bridge client module unavailable (PYTHONPATH miss)"
    if _bridge_client.bridge_running(nick):
        return None
    if not _nick_well_formed(nick):
        return (
            f"refusing to spawn bridge: nick {nick!r} does not match "
            "<server>-<agent> format (Rule 428343). The plugin's "
            "_nick_resolver returned a bare project name; either set "
            "CULTURE_BOSS_NICK explicitly or ensure ~/.culture/server.yaml "
            "is readable."
        )
    cmd = [sys.executable, "-m", "culture", "bridge", "start", nick]
    try:
        proc = subprocess.Popen(  # noqa: S603 — fixed command shape
            cmd,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return f"bridge spawn failed: {exc}"

    # Poll for the IPC socket to appear OR the child to exit
    # non-zero. 30 × 100ms = 3s — comfortably more than the daemon's
    # connect time on a healthy host. We do NOT wait for full IRC
    # registration; subsequent IPC verbs retry once the socket exists.
    for _ in range(30):
        if _bridge_client.bridge_running(nick):
            return None
        rc = proc.poll()
        if rc is not None and rc != 0:
            err_bytes = proc.stderr.read() if proc.stderr else b""
            err = err_bytes.decode(errors="replace").strip() or f"exit {rc}"
            return f"bridge spawn exited {rc}: {err}"
        time.sleep(0.1)

    # 3s elapsed and the socket still isn't up but the child is alive —
    # treat as "still warming up", do not surface as a failure. The
    # bridge_client retry will succeed once registration completes.
    return None


def _spawn_owned_workers(nick: str) -> None:
    """Phase 4.9: walk the manifest, ``culture boss spawn`` each worker
    that is not already running. Best-effort, non-blocking — failures
    are logged into the additionalContext but don't break startup."""
    if _bridge_client is None:
        return
    try:
        resp = _bridge_client.request(nick, "list_owned_agents", timeout=2.0)
    except Exception:  # noqa: BLE001 — bridge may not be up yet
        return
    data = resp.get("data") or {}
    workers = data.get("agents") or []
    for entry in workers:
        suffix = entry.get("suffix") or ""
        if not suffix or entry.get("running"):
            continue
        cmd = [sys.executable, "-m", "culture", "boss", "spawn", suffix]
        env = dict(os.environ)
        env["CULTURE_NICK"] = nick
        try:
            subprocess.Popen(  # noqa: S603
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            pass


def _drain_spool(nick: str) -> list[dict[str, Any]]:
    """Ask the bridge to drain the offline DM spool. The bridge returns
    a list of inbound events; we surface them as additionalContext.
    Returns an empty list on any error so a flaky bridge doesn't kill
    SessionStart."""
    if _bridge_client is None:
        return []
    try:
        resp = _bridge_client.request(nick, "inbox_drain", timeout=3.0)
    except Exception:  # noqa: BLE001
        return []
    if not resp.get("ok", True):
        return []
    data = resp.get("data") or {}
    entries = data.get("entries") or []
    if isinstance(entries, list):
        return entries
    # Whispers fallback — bridge may push via whisper instead of
    # carrying entries in ``data``.
    return list(resp.get("_whispers") or [])


def _emit_session_start(nick: str, runtime_model: str) -> None:
    """Tell the bridge a CC session just opened. Carries the model so
    the bridge can update its daemon-log ``model_resolved`` record
    (v8.18.6 invariant — Phase 4.10)."""
    if _bridge_client is None:
        return
    payload: dict[str, Any] = {"nick": nick, "ts": time.time()}
    if runtime_model:
        payload["model"] = runtime_model
    try:
        _bridge_client.request(nick, "cc_session_start", timeout=2.0, **payload)
    except Exception:  # noqa: BLE001 — best effort
        pass


def _read_mission(nick: str) -> str:
    """Phase 4.11 — read ``~/.culture/mission/<nick>.md`` if present.

    The mission file is the persistent context the bridge writes from
    inbound spool drains. We surface its contents (up to 32 KiB to
    match the rolling-window invariant) verbatim as additionalContext.
    """
    culture_home = os.environ.get("CULTURE_HOME", "").strip()
    if not culture_home:
        culture_home = os.path.join(os.path.expanduser("~"), ".culture")
    safe = nick.replace("/", "_").replace("..", "_")
    path = os.path.join(culture_home, "mission", f"{safe}.md")
    if not os.path.exists(path):
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read(32 * 1024)
    except OSError:
        return ""


def _fetch_roster(nick: str) -> str:
    """Best-effort: snapshot the mesh roster via ``mesh who *``."""
    if _bridge_client is None:
        return ""
    try:
        resp = _bridge_client.request(nick, "irc_who", timeout=2.0, target="*")
    except Exception:  # noqa: BLE001
        return ""
    data = resp.get("data") or {}
    nicks = data.get("nicks")
    if isinstance(nicks, list):
        return ", ".join(str(n) for n in nicks)
    return str(data)


def _format_additional_context(
    nick: str,
    mission: str,
    roster: str,
    spool: list[dict[str, Any]],
    bridge_error: str | None = None,
) -> str:
    """Compose the additionalContext system-reminder block."""
    if bridge_error:
        # Be honest: if the bridge did not come up, do NOT pretend the
        # session is "on the mesh" — that lie cost an entire iteration
        # of debugging when the v9.1.2 nick collision shipped.
        lines = [
            "<system-reminder>",
            f"culture-bridge: BRIDGE SPAWN FAILED for nick `{nick}`.",
            f"Reason: {bridge_error}",
            (
                "This session is NOT on the mesh. Mesh tools (`mesh_*`) "
                "will fail until the bridge is up. Fix the cause above, "
                "then `culture bridge start <valid-nick>` manually and "
                "restart this Claude Code session."
            ),
            "</system-reminder>",
        ]
        return "\n".join(lines)
    lines = [
        "<system-reminder>",
        f"culture-bridge: this CC session is `{nick}` on the mesh.",
        (
            "Override the boss nick via `culture boss init --name X` or by "
            "exporting CULTURE_BOSS_NICK in the session env."
        ),
    ]
    if roster:
        lines.append(f"Roster: {roster}")
    if spool:
        lines.append("Pending inbound events drained from the offline spool:")
        for entry in spool[:50]:
            kind = entry.get("kind", "inbound")
            sender = entry.get("sender", "?")
            target = entry.get("target", "")
            text = (entry.get("text") or entry.get("message") or "").strip()
            lines.append(f"  [{kind}] {sender} -> {target}: {text}")
        if len(spool) > 50:
            lines.append(f"  ... (+{len(spool) - 50} more in `mesh inbox`)")
    if mission:
        lines.append("Mission notes (from ~/.culture/mission/<nick>.md):")
        lines.append(mission)
    lines.append("</system-reminder>")
    return "\n".join(lines)


def main() -> int:
    """Entry point — see module docstring."""
    event = _read_stdin_json()
    cwd = event.get("cwd") or os.getcwd()
    nick = resolve_project_nick(cwd)

    # Hand the resolved nick down to child tools (mesh ...) via env.
    os.environ["CULTURE_NICK"] = nick

    bridge_error = _ensure_bridge_running(nick, _REPO_ROOT)
    if bridge_error:
        # Skip every downstream step that depends on the bridge being
        # alive — those would just stack more failures on top of the
        # first one. The additionalContext block tells the user what
        # to fix.
        spool: list[dict[str, Any]] = []
        mission = ""
        roster = ""
    else:
        runtime_model = os.environ.get("CLAUDE_MODEL", "").strip()
        _emit_session_start(nick, runtime_model)
        _spawn_owned_workers(nick)
        spool = _drain_spool(nick)
        mission = _read_mission(nick)
        roster = _fetch_roster(nick)

    additional_context = _format_additional_context(
        nick, mission, roster, spool, bridge_error=bridge_error
    )
    output = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    }
    sys.stdout.write(json.dumps(output))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
