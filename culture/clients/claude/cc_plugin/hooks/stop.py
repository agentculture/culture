#!/usr/bin/env python3
"""Stop hook (Phase 4.5) — end-of-turn queue drain.

When CC's assistant turn would end, this hook checks the bridge IPC
queue for queued ``inbound_dm`` / ``inbound_mention`` / ``inbound_roominvite``
events. If non-empty it returns ``{"decision": "block", "reason": <...>}``
so CC opens another assistant turn with those events as context. This
is the Phase 0.4 spike pattern.

**Idempotency:** when the event's ``stop_hook_active`` field is true, we
return no decision (an empty body) so we don't infinite-loop on the
follow-up turn.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:  # pragma: no cover — import guard
    from culture.clients.claude.cc_plugin import _bridge_client
    from culture.clients.claude.cc_plugin._nick_resolver import resolve_project_nick
except ImportError:  # pragma: no cover
    _bridge_client = None  # type: ignore[assignment]

    def resolve_project_nick(cwd: str) -> str:  # type: ignore[misc]
        return os.environ.get("CULTURE_NICK") or os.environ.get("CULTURE_BOSS_NICK", "local-boss")


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


def _drain_queue(nick: str) -> list[dict[str, Any]]:
    """Ask the bridge for queued inbound events. Errors → empty list."""
    if _bridge_client is None:
        return []
    try:
        resp = _bridge_client.request(nick, "inbox_drain", timeout=2.0)
    except Exception:  # noqa: BLE001 — bridge may be down mid-turn
        return []
    if not resp.get("ok", True):
        return []
    data = resp.get("data") or {}
    entries = data.get("entries") or []
    if isinstance(entries, list):
        return entries
    return list(resp.get("_whispers") or [])


def _format_reason(entries: list[dict[str, Any]]) -> str:
    """Compose the ``reason`` string CC sees as the next turn's context."""
    lines = [
        "<system-reminder>",
        "culture-bridge: events arrived during your turn. Read them, then "
        "decide whether to act:",
    ]
    for entry in entries[:50]:
        kind = entry.get("kind", "inbound")
        sender = entry.get("sender", "?")
        target = entry.get("target", "")
        text = (entry.get("text") or entry.get("message") or "").strip()
        lines.append(f"  [{kind}] {sender} -> {target}: {text}")
    if len(entries) > 50:
        lines.append(f"  ... (+{len(entries) - 50} more — call `mesh inbox`)")
    lines.append("</system-reminder>")
    return "\n".join(lines)


def main() -> int:
    event = _read_stdin_json()
    # Idempotency: if this Stop already came from a Stop-block continuation,
    # don't block again — CC would otherwise infinite-loop on a stale queue
    # that hasn't been drained yet from the assistant's perspective.
    if event.get("stop_hook_active") is True:
        return 0

    cwd = event.get("cwd") or os.getcwd()
    nick = os.environ.get("CULTURE_NICK") or resolve_project_nick(cwd)
    entries = _drain_queue(nick)
    if not entries:
        return 0
    output = {
        "decision": "block",
        "reason": _format_reason(entries),
    }
    sys.stdout.write(json.dumps(output))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
