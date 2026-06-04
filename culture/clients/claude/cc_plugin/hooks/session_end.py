#!/usr/bin/env python3
"""SessionEnd hook (Phase 4.9) — worker lifecycle teardown.

When CC closes, we:

    1. Walk the manifest and ``culture boss close <name>`` each worker
       owned by this session (Rule 7 — workers follow CC lifecycle).
    2. Emit ``cc_session_end`` to the bridge so it can record the
       transition, then it performs its CHANARCHIVE shutdown.

Best-effort: any failure is logged but does not block CC's actual
exit. (CC won't wait on a hung hook indefinitely either way.)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:  # pragma: no cover — import guard
    from culture.clients.claude.cc_plugin import _bridge_client, _python_resolver
    from culture.clients.claude.cc_plugin._nick_resolver import resolve_project_nick
except ImportError:  # pragma: no cover
    _bridge_client = None  # type: ignore[assignment]
    _python_resolver = None  # type: ignore[assignment]

    def resolve_project_nick(cwd: str) -> str:  # type: ignore[misc]
        return os.environ.get("CULTURE_NICK") or os.environ.get("CULTURE_BOSS_NICK", "local-boss")


def _culture_argv_prefix() -> list[str]:
    """See ``hooks/session_start.py::_culture_argv_prefix``."""
    if _python_resolver is None:  # pragma: no cover — degraded path
        return [sys.executable, "-m", "culture"]
    try:
        return _python_resolver.culture_python()
    except RuntimeError:
        # SessionEnd is best-effort; if CULTURE_PYTHON is misconfigured
        # we still try the bare interpreter rather than aborting the
        # teardown. The session is closing anyway — a worker that
        # can't be cleanly closed leaks at most until next launch.
        return [sys.executable, "-m", "culture"]


def _read_stdin_json() -> dict[str, Any]:
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


def _stop_owned_workers(nick: str) -> None:
    if _bridge_client is None:
        return
    try:
        resp = _bridge_client.request(nick, "list_owned_agents", timeout=2.0)
    except Exception:  # noqa: BLE001
        return
    data = resp.get("data") or {}
    workers = data.get("agents") or []
    for entry in workers:
        suffix = entry.get("suffix") or ""
        if not suffix:
            continue
        cmd = [*_culture_argv_prefix(), "boss", "close", suffix]
        env = dict(os.environ)
        env["CULTURE_NICK"] = nick
        try:
            subprocess.run(  # noqa: S603
                cmd,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def _notify_session_end(nick: str) -> None:
    if _bridge_client is None:
        return
    try:
        _bridge_client.request(nick, "cc_session_end", timeout=2.0)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    event = _read_stdin_json()
    cwd = event.get("cwd") or os.getcwd()
    nick = os.environ.get("CULTURE_NICK") or resolve_project_nick(cwd)

    _stop_owned_workers(nick)
    _notify_session_end(nick)
    return 0


if __name__ == "__main__":
    sys.exit(main())
