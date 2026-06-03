#!/usr/bin/env python3
"""PreToolUse hook (Phase 4.7 / AD-1) — perm-request interrupt.

When a worker raises a perm request, the bridge queues it as a
``perm_request`` event. This hook fires before every CC tool call and
drains the queue. If a perm request is pending, the hook blocks the
tool call with ``{"decision": "block", "reason": <perm details>}`` so
CC sees the request, decides, and uses ``mesh approve`` / ``mesh deny``
on the next turn. This is the AD-1 interrupt-priority pattern.

**CRITICAL — recursion avoidance.** If the tool being gated is itself a
``mesh ...`` verb (i.e. CC's own approve/deny call), we MUST pass it
through without checking the queue. Otherwise: CC approves request X
→ PreToolUse fires for ``mesh approve`` → sees request X still queued
→ blocks → CC re-approves → infinite loop. The check is by tool-name
prefix ``mesh `` plus the explicit ``{mesh approve, mesh deny}`` set
so we don't accidentally let through a future-named ``meshxyz`` tool.
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


# Explicit allowlist for the mesh verb family — anything starting with
# ``mesh `` is also allowed (covers future verbs added in Phase 5+).
_MESH_TOOL_ALLOWLIST = {
    "mesh approve",
    "mesh deny",
    "mesh send",
    "mesh dm",
    "mesh inbox",
    "mesh who",
    "mesh status",
    "mesh agents",
    "mesh pending",
    "mesh invite",
    "mesh team-channel-create",
    "mesh grant",
}


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


def _is_mesh_tool(tool_name: str) -> bool:
    """Recursion-avoidance predicate — see module docstring."""
    if not tool_name:
        return False
    if tool_name in _MESH_TOOL_ALLOWLIST:
        return True
    return tool_name.startswith("mesh ")


def _drain_perm_requests(nick: str) -> list[dict[str, Any]]:
    """Ask the bridge for queued perm requests only (NOT inbox events —
    those go end-of-turn via the Stop hook). Errors → empty list."""
    if _bridge_client is None:
        return []
    try:
        resp = _bridge_client.request(nick, "list_perm_queue", timeout=2.0)
    except Exception:  # noqa: BLE001
        return []
    if not resp.get("ok", True):
        return []
    data = resp.get("data") or {}
    entries = data.get("entries") or []
    if isinstance(entries, list):
        return entries
    return []


def _format_reason(entries: list[dict[str, Any]]) -> str:
    lines = [
        "<system-reminder>",
        "culture-bridge: worker permission requests are pending — decide before "
        "running your next tool:",
    ]
    for entry in entries[:10]:
        rid = entry.get("id", "?")
        worker = entry.get("helper_nick") or entry.get("worker") or "?"
        tool = entry.get("tool_name") or entry.get("tool") or "?"
        inp = entry.get("input") or {}
        inp_str = json.dumps(inp, separators=(",", ":"))[:200]
        lines.append(f"  id={rid} worker={worker} tool={tool} input={inp_str}")
    lines.append(
        "Use `mesh approve <id> [--input-regex PATTERN] [--scope always|once]` "
        "or `mesh deny <id> [reason...]`."
    )
    lines.append("</system-reminder>")
    return "\n".join(lines)


def main() -> int:
    event = _read_stdin_json()
    tool_name = event.get("tool_name") or event.get("tool") or ""

    # Recursion guard — let mesh tools pass through.
    if _is_mesh_tool(tool_name):
        return 0

    cwd = event.get("cwd") or os.getcwd()
    nick = os.environ.get("CULTURE_NICK") or resolve_project_nick(cwd)

    entries = _drain_perm_requests(nick)
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
